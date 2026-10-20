"""Tool registry: enabled AI Tool Configurations as plain, cached tool specs.

The registry is site-wide and cached in Redis. What a *user* sees is filtered per request by
`get_tools_for_user`, which drops every DocType the user cannot read in ERPNext.
Nothing here is ever sent to the browser.
"""

import frappe

from grid_erp.grid_ai.tools.aggregations import OPERATION_FLAGS

CACHE_KEY = "grid_ai:tool_registry"


def clear_registry_cache():
	frappe.cache.delete_value(CACHE_KEY)


def get_registry() -> dict[str, dict]:
	"""{doctype: tool spec} for every enabled configuration."""
	registry = frappe.cache.get_value(CACHE_KEY)
	if registry is None:
		registry = _build_registry()
		frappe.cache.set_value(CACHE_KEY, registry, expires_in_sec=3600)
	return registry


def _build_registry() -> dict[str, dict]:
	registry = {}
	names = frappe.get_all("AI Tool Configuration", filters={"enabled": 1}, pluck="name")
	for name in names:
		config = frappe.get_doc("AI Tool Configuration", name)
		if not frappe.db.exists("DocType", config.reference_doctype):
			continue  # app uninstalled: skip silently
		registry[config.reference_doctype] = _to_spec(config)
	return registry


def _to_spec(config) -> dict:
	from grid_erp.grid_ai.tools.discovery import get_field_info

	operations = [op for op, flag in OPERATION_FLAGS.items() if config.get(flag)]
	meta = frappe.get_meta(config.reference_doctype)
	fields = {}
	for row in config.fields:
		child_doctype = None
		if "." in row.fieldname:
			info = get_field_info(meta, row.fieldname)
			if not info:
				continue  # child table field removed from the DocType: skip silently
			child_doctype = info["child_doctype"]
		fields[row.fieldname] = {
			"label": row.label,
			"fieldtype": row.fieldtype,
			"options": row.options or "",
			"display": bool(row.allow_display),
			"filter": bool(row.allow_filter),
			"group_by": bool(row.allow_group_by),
			"aggregate": bool(row.allow_aggregate),
			"child_doctype": child_doctype,
		}
	return {
		"doctype": config.reference_doctype,
		"label": config.label or config.reference_doctype,
		"description": (config.ai_description or "").strip(),
		"operations": operations,
		"fields": fields,
		"default_date_field": config.default_date_field or ("creation" if "creation" in fields else None),
		"is_submittable": bool(config.is_submittable),
		"include_drafts": bool(config.include_drafts),
		"max_records": config.max_records or 20,
		"max_query_time": config.max_query_time or 10,
	}


def get_tool(name: str) -> dict | None:
	"""Find an enabled tool by DocType name or label (case-insensitive)."""
	if not name or not isinstance(name, str):
		return None
	registry = get_registry()
	if name in registry:
		return registry[name]
	wanted = name.strip().lower()
	for spec in registry.values():
		if wanted in (spec["doctype"].lower(), spec["label"].lower()):
			return spec
	return None


def get_tools_for_user(user: str) -> list[dict]:
	"""Enabled tools whose DocType the user may read in ERPNext."""
	from grid_erp.grid_ai.tools.permissions import can_read_doctype

	return [spec for spec in get_registry().values() if can_read_doctype(spec["doctype"], user)]


def build_catalog(specs: list[dict]) -> str:
	"""Compact text description of the available data sources for the system prompt."""
	if not specs:
		return "(No ERP data sources are available to this user.)"

	blocks = []
	if any("." in name for spec in specs for name in spec["fields"]):
		blocks.append(
			"Fields written as <table>.<field> (e.g. items.item_code, items.qty, items.amount) come from the "
			"document's line items. Use them for item-wise questions (top selling items, quantity of an item): "
			"results are then per item line, and counts are line counts. Never combine them with document "
			"totals (grand_total etc.): sum the line field instead. Use only one table per query."
		)
	for spec in specs:
		lines = [f"### {spec['doctype']}" + (f" (label: {spec['label']})" if spec["label"] != spec["doctype"] else "")]
		if spec["description"]:
			lines.append(f"About: {spec['description']}")
		lines.append("Operations: " + ", ".join(spec["operations"]))
		if spec["default_date_field"]:
			lines.append(f"Default date field: {spec['default_date_field']}")
		if spec["is_submittable"]:
			lines.append(
				"Only submitted documents are counted." if not spec["include_drafts"] else "Draft and submitted documents are counted."
			)
		field_lines = []
		for fieldname, f in spec["fields"].items():
			caps = [c for c, ok in (("display", f["display"]), ("filter", f["filter"]), ("group", f["group_by"]), ("agg", f["aggregate"])) if ok]
			extra = ""
			if f["fieldtype"] == "Link" and f["options"]:
				extra = f" -> {f['options']}"
			elif f["fieldtype"] == "Select" and f["options"]:
				extra = " [" + "|".join(o for o in f["options"].split("\n") if o)[:120] + "]"
			field_lines.append(f"  - {fieldname} ({f['label']}; {f['fieldtype']}{extra}) {'/'.join(caps)}")
		lines.append("Fields:")
		lines.extend(field_lines)
		blocks.append("\n".join(lines))
	return "\n\n".join(blocks)
