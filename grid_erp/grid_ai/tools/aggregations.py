"""Supported generic operations and how their results are shaped.

All aggregation happens in the database (COUNT / SUM / AVG / MIN / MAX / GROUP BY);
only the aggregated result, never the underlying rows, is sent to the LLM.
"""

import datetime
from decimal import Decimal

# operation -> AI Tool Configuration checkbox that enables it
OPERATION_FLAGS = {
	"count": "allow_count",
	"list": "allow_list",
	"search": "allow_search",
	"sum": "allow_sum",
	"average": "allow_average",
	"min": "allow_min_max",
	"max": "allow_min_max",
	"group_by": "allow_group_by",
}

OPERATIONS = tuple(OPERATION_FLAGS)

# operation -> SQL aggregate function understood by frappe.qb
AGGREGATE_FUNCTIONS = {
	"count": "COUNT",
	"sum": "SUM",
	"average": "AVG",
	"min": "MIN",
	"max": "MAX",
}

# aggregates usable inside group_by
GROUP_AGGREGATES = ("count", "sum", "average", "min", "max")


def count_field() -> dict:
	return {"COUNT": "name", "as": "record_count"}


def aggregate_field(operation: str, fieldname: str) -> dict:
	return {AGGREGATE_FUNCTIONS[operation]: fieldname, "as": "value"}


def to_json_safe(value):
	"""Convert DB values into JSON-friendly primitives."""
	if isinstance(value, Decimal):
		value = float(value)
	if isinstance(value, float):
		return round(value, 2)
	if isinstance(value, datetime.datetime):
		return value.strftime("%Y-%m-%d %H:%M:%S")
	if isinstance(value, datetime.date):
		return value.isoformat()
	if isinstance(value, datetime.timedelta):
		return str(value)
	return value


def clean_rows(rows: list[dict]) -> list[dict]:
	return [{k: to_json_safe(v) for k, v in row.items()} for row in rows]


def shape_result(operation: str, rows: list[dict], limit: int | None) -> dict:
	"""Turn query rows into a compact result for the LLM."""
	rows = clean_rows(rows)

	if operation == "count":
		count = rows[0]["record_count"] if rows else 0
		return {"count": count or 0}

	if operation in ("sum", "average", "min", "max"):
		row = rows[0] if rows else {}
		count = row.get("record_count") or 0
		value = row.get("value")
		if value is None and operation == "sum" and count:
			value = 0
		return {"value": value, "matching_records": count}

	# list / search / group_by
	return {
		"rows": rows,
		"row_count": len(rows),
		"truncated": bool(limit and len(rows) >= limit),
	}
