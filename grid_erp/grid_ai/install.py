"""Install / migrate hooks.

On first install, a few common ERPNext / HRMS DocTypes get a reviewed starter configuration
(curated, non-sensitive fields only) so the assistant is usable immediately. Everything else
is discovered as *disabled*. Presets are applied once and never overwrite admin changes.
These are configuration data, not code: an admin can edit or disable any of them.
"""

import frappe

from grid_erp.grid_ai.tools.discovery import (
	GROUPABLE_FIELDTYPES,
	NUMERIC_FIELDTYPES,
	get_field_info,
	sync_tool_configurations,
)
from grid_erp.grid_ai.tools.registry import clear_registry_cache

# Line item fields for item-wise questions ("sabse zyada kaunsa item bika").
# (fieldname, display, filter, group_by, aggregate)
ITEM_FIELDS = [
	("items.item_code", 1, 1, 1, 0),
	("items.item_name", 1, 1, 1, 0),
	("items.item_group", 1, 1, 1, 0),
	("items.qty", 1, 1, 0, 1),
	("items.amount", 1, 1, 0, 1),
]
ITEM_DOCTYPES = ("Sales Invoice", "Purchase Invoice", "Sales Order", "Purchase Order")
ITEM_DESCRIPTION = " Item-wise questions (top items, qty of an item): use the items.* line fields."

# (fieldname, display, filter, group_by, aggregate)
PRESETS = {
	"Lead": {
		"description": "Sales leads / enquiries (leads, naye leads, enquiry). Use creation date for 'kitne leads aaye'.",
		"ops": ("count", "list", "search", "group_by"),
		"date": "creation",
		"fields": [
			("name", 1, 1, 0, 0),
			("creation", 1, 1, 0, 0),
			("lead_name", 1, 1, 0, 0),
			("company_name", 1, 1, 0, 0),
			("status", 1, 1, 1, 0),
			("source", 1, 1, 1, 0),
			("lead_owner", 1, 1, 1, 0),
			("territory", 1, 1, 1, 0),
			("city", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
		],
	},
	"Customer": {
		"description": "Customers (grahak, client, naye customer).",
		"ops": ("count", "list", "search", "group_by"),
		"date": "creation",
		"fields": [
			("name", 1, 1, 0, 0),
			("creation", 1, 1, 0, 0),
			("customer_name", 1, 1, 0, 0),
			("customer_group", 1, 1, 1, 0),
			("customer_type", 1, 1, 1, 0),
			("territory", 1, 1, 1, 0),
			("disabled", 1, 1, 1, 0),
		],
	},
	"Supplier": {
		"description": "Suppliers / vendors.",
		"ops": ("count", "list", "search", "group_by"),
		"date": "creation",
		"fields": [
			("name", 1, 1, 0, 0),
			("creation", 1, 1, 0, 0),
			("supplier_name", 1, 1, 0, 0),
			("supplier_group", 1, 1, 1, 0),
			("country", 1, 1, 1, 0),
			("disabled", 1, 1, 1, 0),
		],
	},
	"Sales Invoice": {
		"description": "Submitted customer sales invoices. Use for sales, revenue, bikri, total sales, top customers, "
		"and receivables / outstanding (outstanding_amount).",
		"ops": ("count", "list", "search", "sum", "average", "min", "group_by"),
		"date": "posting_date",
		"fields": [
			("name", 1, 1, 0, 0),
			("posting_date", 1, 1, 1, 0),
			("customer", 1, 1, 1, 0),
			("customer_name", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("territory", 1, 1, 1, 0),
			("net_total", 1, 1, 0, 1),
			("grand_total", 1, 1, 0, 1),
			("outstanding_amount", 1, 1, 0, 1),
			("due_date", 1, 1, 0, 0),
			("is_return", 1, 1, 1, 0),
		],
	},
	"Purchase Invoice": {
		"description": "Submitted supplier purchase invoices. Use for purchase, kharid, top suppliers, payables.",
		"ops": ("count", "list", "search", "sum", "average", "min", "group_by"),
		"date": "posting_date",
		"fields": [
			("name", 1, 1, 0, 0),
			("posting_date", 1, 1, 1, 0),
			("supplier", 1, 1, 1, 0),
			("supplier_name", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("net_total", 1, 1, 0, 1),
			("grand_total", 1, 1, 0, 1),
			("outstanding_amount", 1, 1, 0, 1),
			("due_date", 1, 1, 0, 0),
			("is_return", 1, 1, 1, 0),
		],
	},
	"Sales Order": {
		"description": "Submitted customer sales orders.",
		"ops": ("count", "list", "search", "sum", "group_by"),
		"date": "transaction_date",
		"fields": [
			("name", 1, 1, 0, 0),
			("transaction_date", 1, 1, 1, 0),
			("customer", 1, 1, 1, 0),
			("customer_name", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("delivery_date", 1, 1, 0, 0),
			("grand_total", 1, 1, 0, 1),
		],
	},
	"Purchase Order": {
		"description": "Submitted purchase orders (PO) to suppliers.",
		"ops": ("count", "list", "search", "sum", "group_by"),
		"date": "transaction_date",
		"fields": [
			("name", 1, 1, 0, 0),
			("transaction_date", 1, 1, 1, 0),
			("supplier", 1, 1, 1, 0),
			("supplier_name", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("schedule_date", 1, 1, 0, 0),
			("grand_total", 1, 1, 0, 1),
		],
	},
	"Item": {
		"description": "Item master (products, materials). For stock quantities use Bin.",
		"ops": ("count", "list", "search", "group_by"),
		"date": "creation",
		"fields": [
			("name", 1, 1, 0, 0),
			("creation", 1, 1, 0, 0),
			("item_code", 1, 1, 0, 0),
			("item_name", 1, 1, 0, 0),
			("item_group", 1, 1, 1, 0),
			("stock_uom", 1, 1, 1, 0),
			("is_stock_item", 1, 1, 1, 0),
			("disabled", 1, 1, 1, 0),
		],
	},
	"Bin": {
		"description": "Current stock per item per warehouse (stock kitna hai). Sum actual_qty for an item across warehouses.",
		"ops": ("list", "search", "sum", "group_by"),
		"date": None,
		"fields": [
			("item_code", 1, 1, 1, 0),
			("warehouse", 1, 1, 1, 0),
			("actual_qty", 1, 1, 0, 1),
			("projected_qty", 1, 1, 0, 1),
			("reserved_qty", 1, 1, 0, 1),
			("ordered_qty", 1, 1, 0, 1),
			("stock_value", 1, 1, 0, 1),
			("stock_uom", 1, 1, 0, 0),
		],
	},
	"Employee": {
		"description": "Employees (karmachari, staff). Only non-personal fields are configured.",
		"ops": ("count", "list", "search", "group_by"),
		"date": "date_of_joining",
		"fields": [
			("name", 1, 1, 0, 0),
			("employee_name", 1, 1, 0, 0),
			("department", 1, 1, 1, 0),
			("designation", 1, 1, 1, 0),
			("branch", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("date_of_joining", 1, 1, 0, 0),
		],
	},
	"Attendance": {
		"description": "Daily employee attendance (present / absent / on leave, working hours). "
		"'Kal kitne absent the' = count with status Absent for yesterday.",
		"ops": ("count", "list", "search", "sum", "average", "group_by"),
		"date": "attendance_date",
		"fields": [
			("name", 1, 1, 0, 0),
			("attendance_date", 1, 1, 1, 0),
			("employee", 1, 1, 1, 0),
			("employee_name", 1, 1, 1, 0),
			("status", 1, 1, 1, 0),
			("department", 1, 1, 1, 0),
			("company", 1, 1, 1, 0),
			("working_hours", 1, 1, 0, 1),
			("late_entry", 1, 1, 1, 0),
			("early_exit", 1, 1, 1, 0),
		],
	},
}

OP_FLAGS = {
	"count": "allow_count",
	"list": "allow_list",
	"search": "allow_search",
	"sum": "allow_sum",
	"average": "allow_average",
	"min": "allow_min_max",
	"group_by": "allow_group_by",
}


def after_install():
	sync_tool_configurations()
	apply_presets()
	frappe.db.commit()


def after_migrate():
	sync_tool_configurations()
	clear_registry_cache()


def apply_presets():
	for doctype, preset in PRESETS.items():
		if not frappe.db.exists("DocType", doctype):
			continue
		try:
			_apply_preset(doctype, preset)
		except Exception:
			frappe.log_error(title=f"Grid AI: preset for {doctype} skipped")
	clear_registry_cache()


def _apply_preset(doctype: str, preset: dict):
	if frappe.db.exists("AI Tool Configuration", doctype):
		config = frappe.get_doc("AI Tool Configuration", doctype)
		if config.enabled or config.fields:
			return  # already configured by an admin
	else:
		config = frappe.new_doc("AI Tool Configuration")
		config.reference_doctype = doctype

	config.label = doctype
	config.ai_description = preset["description"]
	config.set("fields", [])
	fields = preset["fields"] + (ITEM_FIELDS if doctype in ITEM_DOCTYPES else [])
	append_fields(config, fields)
	if doctype in ITEM_DOCTYPES:
		config.ai_description += ITEM_DESCRIPTION
	for op in preset["ops"]:
		config.set(OP_FLAGS[op], 1)
	config.default_date_field = preset["date"]
	config.enabled = 1
	config.save(ignore_permissions=True) if not config.is_new() else config.insert(ignore_permissions=True)


def append_fields(config, fields: list[tuple]) -> int:
	"""Add preset field rows the configuration does not have yet. Returns how many were added."""
	meta = frappe.get_meta(config.reference_doctype)
	existing = {row.fieldname for row in config.fields}
	added = 0
	for fieldname, display, filt, group, agg in fields:
		if fieldname in existing:
			continue
		df = get_field_info(meta, fieldname)
		if not df:
			continue  # field not present in this ERPNext version
		# Field types differ between versions: only keep capabilities the actual type supports.
		config.append(
			"fields",
			{
				"fieldname": fieldname,
				"allow_display": display,
				"allow_filter": filt,
				"allow_group_by": group if df["fieldtype"] in GROUPABLE_FIELDTYPES else 0,
				"allow_aggregate": agg if df["fieldtype"] in NUMERIC_FIELDTYPES else 0,
			},
		)
		added += 1
	return added
