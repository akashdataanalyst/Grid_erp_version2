"""Validate structured tool arguments and turn them into a safe `frappe.get_list` call.

The LLM never writes SQL. It sends JSON like:

    {"doctype": "Sales Invoice", "operation": "sum", "field": "grand_total",
     "date_range": {"period": "this_month"}}

Every DocType, operation, field, operator and value is checked against the AI Tool
Configuration whitelist. Anything not explicitly allowed is rejected.
"""

from dataclasses import dataclass, field

from grid_erp.grid_ai.exceptions import InvalidRequestError, NotConfiguredError
from grid_erp.grid_ai.tools import aggregations, dates
from grid_erp.grid_ai.tools.discovery import DATE_FIELDTYPES, TEXT_SEARCH_FIELDTYPES

MAX_FILTERS = 10
MAX_IN_VALUES = 50
MAX_STRING_LENGTH = 140
MAX_LIST_FIELDS = 12
DEFAULT_LIST_LIMIT = 10

OPERATOR_ALIASES = {
	"=": "=",
	"==": "=",
	"eq": "=",
	"equals": "=",
	"!=": "!=",
	"<>": "!=",
	"ne": "!=",
	"not equals": "!=",
	">": ">",
	"gt": ">",
	"<": "<",
	"lt": "<",
	">=": ">=",
	"gte": ">=",
	"<=": "<=",
	"lte": "<=",
	"like": "like",
	"contains": "like",
	"not like": "not like",
	"in": "in",
	"not in": "not in",
	"between": "between",
	"is": "is",
}


@dataclass
class QueryPlan:
	doctype: str
	operation: str
	fields: list
	filters: list[list] = field(default_factory=list)
	or_filters: list[list] = field(default_factory=list)
	group_by: str | None = None
	order_by: str | None = None
	limit: int | None = None
	referenced_fields: set = field(default_factory=set)
	explicit_filters: list[list] = field(default_factory=list)
	date_info: dict | None = None
	max_query_time: int = 10
	value_field: str | None = None  # field behind the "value" column of sum/average/min/max
	group_field: str | None = None
	child_table: str | None = None  # table field (e.g. "items") when line fields are used
	aliases: dict = field(default_factory=dict)  # result column -> field name (child fields)

	def describe(self) -> str:
		"""Short, value-free summary kept as conversation context for follow-up questions."""
		parts = [self.doctype, self.operation]
		if self.value_field:
			parts.append(f"of {self.value_field}")
		if self.group_field:
			parts.append(f"by {self.group_field}")
		if self.child_table:
			parts.append(f"per {self.child_table} line")
		if self.date_info:
			parts.append(
				f"{self.date_info['field']} {self.date_info['period']} ({self.date_info['from']}..{self.date_info['to']})"
			)
		filter_fields = [f[0] for f in self.explicit_filters if f[0] != (self.date_info or {}).get("field")]
		if filter_fields:
			parts.append("filtered by " + ", ".join(sorted(set(filter_fields))))
		return " · ".join(parts)


def build_plan(spec: dict, args: dict) -> QueryPlan:
	operation = str(args.get("operation") or "").strip().lower().replace(" ", "_")
	operation = {"avg": "average", "mean": "average", "total": "sum", "groupby": "group_by"}.get(operation, operation)
	if operation not in aggregations.OPERATIONS:
		raise InvalidRequestError(detail=f"unknown operation {operation!r}")
	if operation not in spec["operations"]:
		raise NotConfiguredError(
			f"{spec['label']} ke liye '{operation}' operation AI ke liye allowed nahi hai.",
			detail=f"operation {operation} not enabled for {spec['doctype']}",
		)

	plan = QueryPlan(
		doctype=spec["doctype"],
		operation=operation,
		fields=[],
		max_query_time=spec["max_query_time"],
	)

	filters = _normalize_filters(args.get("filters"))
	for fieldname, operator, value in filters:
		_add_filter(plan, spec, fieldname, operator, value)

	if args.get("date_range"):
		_add_date_range(plan, spec, args["date_range"])

	if spec["is_submittable"]:
		plan.filters.append(["docstatus", "!=", 2] if spec["include_drafts"] else ["docstatus", "=", 1])

	builder = {
		"count": _build_count,
		"sum": _build_aggregate,
		"average": _build_aggregate,
		"min": _build_aggregate,
		"max": _build_aggregate,
		"list": _build_list,
		"search": _build_search,
		"group_by": _build_group_by,
	}[operation]
	builder(plan, spec, args)

	if plan.child_table:
		if plan.value_field and "." not in plan.value_field:
			# Joining line items repeats the document total once per line: the sum would be wrong.
			raise NotConfiguredError(
				"Item-wise sawal mein document ka total nahi jod sakte; item ka amount / qty use kariye.",
				detail=f"parent field {plan.value_field} aggregated with {plan.child_table} lines",
			)
		# A redundant condition on the line table so Frappe joins it on parentfield too, even
		# when the line field only appears inside an aggregate.
		plan.filters.append([f"{plan.child_table}.parentfield", "=", plan.child_table])
	return plan


# ---------------------------------------------------------------- operations


def _build_count(plan: QueryPlan, spec: dict, args: dict):
	plan.fields = [aggregations.count_field()]


def _build_aggregate(plan: QueryPlan, spec: dict, args: dict):
	fieldname = _require_str(args.get("field"), "field")
	f = _field(spec, fieldname, plan)
	is_date_extreme = plan.operation in ("min", "max") and f["fieldtype"] in DATE_FIELDTYPES and f["filter"]
	if not (f["aggregate"] or is_date_extreme):
		raise NotConfiguredError(detail=f"{fieldname} cannot be aggregated")
	plan.referenced_fields.add(fieldname)
	plan.value_field = fieldname
	plan.fields = [
		aggregations.aggregate_field(plan.operation, _sql_ref(spec, fieldname)),
		aggregations.count_field(),
	]


def _build_list(plan: QueryPlan, spec: dict, args: dict):
	requested = args.get("fields") or []
	if not isinstance(requested, list):
		raise InvalidRequestError(detail="fields must be a list")
	if requested:
		fields = []
		for name in requested[:MAX_LIST_FIELDS]:
			name = _require_str(name, "fields[]")
			if not _field(spec, name, plan)["display"]:
				raise NotConfiguredError(detail=f"{name} is not displayable")
			fields.append(name)
	else:
		# Line fields only when asked for: by default one row per document.
		fields = [name for name, f in spec["fields"].items() if f["display"] and "." not in name][:6]
	if "name" not in fields:
		fields.insert(0, "name")
	plan.fields = [_select(plan, name) for name in fields]
	plan.referenced_fields.update(f for f in fields if f != "name")
	plan.order_by = _order_by(plan, spec, args, default_field=spec["default_date_field"] or "creation")
	plan.limit = _limit(spec, args)


def _build_search(plan: QueryPlan, spec: dict, args: dict):
	text = _require_str(args.get("search_text"), "search_text").replace("%", "").strip()
	if len(text) < 2:
		raise InvalidRequestError(detail="search_text too short")
	searchable = [
		name
		for name, f in spec["fields"].items()
		if f["filter"] and f["fieldtype"] in TEXT_SEARCH_FIELDTYPES and "." not in name
	]
	if "name" not in searchable:
		searchable.insert(0, "name")
	plan.or_filters = [[name, "like", f"%{text}%"] for name in searchable]
	plan.referenced_fields.update(n for n in searchable if n != "name")
	_build_list(plan, spec, args)


def _build_group_by(plan: QueryPlan, spec: dict, args: dict):
	group_field = _require_str(args.get("group_by"), "group_by")
	if not _field(spec, group_field, plan)["group_by"]:
		raise NotConfiguredError(detail=f"cannot group by {group_field}")
	plan.group_by = _sql_ref(spec, group_field)
	plan.group_field = group_field
	plan.referenced_fields.add(group_field)

	aggregate = str(args.get("aggregate") or "count").lower()
	aggregate = {"avg": "average", "total": "sum"}.get(aggregate, aggregate)
	if aggregate not in aggregations.GROUP_AGGREGATES:
		raise InvalidRequestError(detail=f"invalid aggregate {aggregate!r}")

	fields = [_select(plan, group_field), aggregations.count_field()]
	sort_alias = "record_count"
	if aggregate != "count":
		if aggregate not in spec["operations"] and not (aggregate in ("min", "max") and "min" in spec["operations"]):
			raise NotConfiguredError(detail=f"aggregate {aggregate} not enabled")
		value_field = _require_str(args.get("field"), "field")
		if not _field(spec, value_field, plan)["aggregate"]:
			raise NotConfiguredError(detail=f"{value_field} cannot be aggregated")
		plan.referenced_fields.add(value_field)
		plan.value_field = value_field
		fields.append(aggregations.aggregate_field(aggregate, _sql_ref(spec, value_field)))
		sort_alias = "value"

	direction = "asc" if str(args.get("order") or "desc").lower() == "asc" else "desc"
	plan.fields = fields
	plan.order_by = f"{sort_alias} {direction}"
	plan.limit = _limit(spec, args)


# ---------------------------------------------------------------- filters


def _normalize_filters(raw) -> list[tuple]:
	"""Accept [{field, operator, value}], [[field, op, value]] or {field: value}."""
	if not raw:
		return []
	items = []
	if isinstance(raw, dict):
		for key, value in raw.items():
			if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and value[0].lower() in OPERATOR_ALIASES:
				items.append((key, value[0], value[1]))
			else:
				items.append((key, "=", value))
	elif isinstance(raw, list):
		for item in raw:
			if isinstance(item, dict):
				items.append((item.get("field") or item.get("fieldname"), item.get("operator") or "=", item.get("value")))
			elif isinstance(item, list | tuple) and len(item) == 3:
				items.append(tuple(item))
			else:
				raise InvalidRequestError(detail="malformed filter")
	else:
		raise InvalidRequestError(detail="filters must be a list or object")

	if len(items) > MAX_FILTERS:
		raise InvalidRequestError(detail="too many filters")
	return items


def _add_filter(plan: QueryPlan, spec: dict, fieldname, operator, value):
	fieldname = _require_str(fieldname, "filter field")
	f = _field(spec, fieldname, plan)
	if not f["filter"]:
		raise NotConfiguredError(detail=f"filtering by {fieldname} not allowed")

	operator = OPERATOR_ALIASES.get(str(operator).strip().lower())
	if not operator:
		raise InvalidRequestError(detail="invalid operator")

	# A period word on a date field ("posting_date = this_month") becomes a date range.
	if f["fieldtype"] in DATE_FIELDTYPES and operator == "=" and isinstance(value, str):
		try:
			dates.normalize_period(value)
		except Exception:
			pass
		else:
			_add_date_range(plan, spec, {"field": fieldname, "period": value})
			return

	value = _clean_value(operator, value)
	if operator == "like" and isinstance(value, str) and "%" not in value:
		value = f"%{value}%"
	if operator == "between" and f["fieldtype"] == "Datetime":
		start, end = (str(v) for v in value)
		value = [
			f"{start} 00:00:00" if len(start) == 10 else start,
			f"{end} 23:59:59.999999" if len(end) == 10 else end,
		]

	condition = [fieldname, operator, value]
	plan.filters.append(condition)
	plan.explicit_filters.append(condition)
	plan.referenced_fields.add(fieldname)


def _add_date_range(plan: QueryPlan, spec: dict, date_range):
	if not isinstance(date_range, dict):
		raise InvalidRequestError(detail="date_range must be an object")
	fieldname = date_range.get("field") or spec["default_date_field"]
	if not fieldname:
		raise InvalidRequestError(detail="no date field available")
	f = _field(spec, fieldname, plan)
	if f["fieldtype"] not in DATE_FIELDTYPES or not f["filter"]:
		raise NotConfiguredError(detail=f"{fieldname} is not a filterable date field")

	start, end, label = dates.resolve_range(date_range)
	if f["fieldtype"] == "Datetime":
		value = [f"{start} 00:00:00", f"{end} 23:59:59.999999"]
	else:
		value = [str(start), str(end)]

	condition = [fieldname, "between", value]
	plan.filters.append(condition)
	plan.explicit_filters.append(condition)
	plan.referenced_fields.add(fieldname)
	plan.date_info = {"field": fieldname, "period": label, "from": str(start), "to": str(end)}


def _clean_value(operator: str, value):
	if operator == "is":
		if str(value).lower() not in ("set", "not set"):
			raise InvalidRequestError(detail="'is' needs 'set' or 'not set'")
		return str(value).lower()
	if operator in ("in", "not in"):
		if isinstance(value, str):
			value = [v.strip() for v in value.split(",") if v.strip()]
		if not isinstance(value, list) or not value or len(value) > MAX_IN_VALUES:
			raise InvalidRequestError(detail="'in' needs a non-empty list")
		return [_scalar(v) for v in value]
	if operator == "between":
		if not isinstance(value, list) or len(value) != 2:
			raise InvalidRequestError(detail="'between' needs two values")
		return [_scalar(v) for v in value]
	return _scalar(value)


def _scalar(value):
	if isinstance(value, bool):
		return 1 if value else 0
	if isinstance(value, int | float):
		return value
	if isinstance(value, str):
		if len(value) > MAX_STRING_LENGTH:
			raise InvalidRequestError(detail="filter value too long")
		return value
	raise InvalidRequestError(detail="filter values must be text or numbers")


# ---------------------------------------------------------------- helpers


def _field(spec: dict, fieldname: str, plan: QueryPlan | None = None) -> dict:
	f = spec["fields"].get(fieldname)
	if not f:
		raise NotConfiguredError(
			"Ye field AI ke liye configured nahi hai.",
			detail=f"field {fieldname!r} not configured for {spec['doctype']}",
		)
	if plan is not None and "." in fieldname:
		table = fieldname.partition(".")[0]
		if plan.child_table and plan.child_table != table:
			# Two line tables in one query multiply each other's rows.
			raise InvalidRequestError(
				"Ek sawal mein ek hi table ke fields use ho sakte hain.",
				detail=f"tables {plan.child_table} and {table} in one query",
			)
		plan.child_table = table
	return f


def _sql_ref(spec: dict, fieldname: str) -> str:
	"""Column reference for aggregates and GROUP BY. Child table fields are written as
	`tabChild`.field, the form Frappe accepts there; plain fields stay as they are."""
	child_doctype = spec["fields"][fieldname].get("child_doctype")
	if not child_doctype:
		return fieldname
	return f"`tab{child_doctype}`.{fieldname.partition('.')[2]}"


def _select(plan: QueryPlan, fieldname: str) -> str:
	"""Selected column; child table fields get an alias that is mapped back to `items.x`."""
	if "." not in fieldname:
		return fieldname
	alias = fieldname.replace(".", "__")
	plan.aliases[alias] = fieldname
	return f"{fieldname} as {alias}"


def _require_str(value, name: str) -> str:
	if not isinstance(value, str) or not value.strip():
		raise InvalidRequestError(detail=f"{name} is required")
	return value.strip()


def _limit(spec: dict, args: dict) -> int:
	try:
		limit = int(args.get("limit") or DEFAULT_LIST_LIMIT)
	except (TypeError, ValueError):
		limit = DEFAULT_LIST_LIMIT
	return max(1, min(limit, spec["max_records"]))


def _order_by(plan: QueryPlan, spec: dict, args: dict, default_field: str) -> str:
	fieldname = args.get("order_by") or default_field
	if not isinstance(fieldname, str):
		raise InvalidRequestError(detail="order_by must be a field name")
	f = spec["fields"].get(fieldname)
	if fieldname != "creation" and (not f or not (f["display"] or f["filter"])):
		raise NotConfiguredError(detail=f"cannot sort by {fieldname}")
	if f:
		_field(spec, fieldname, plan)
	direction = "asc" if str(args.get("order") or "desc").lower() == "asc" else "desc"
	return f"{fieldname} {direction}"
