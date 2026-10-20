"""Add the line item fields (items.item_code, items.qty, ...) to the invoice / order
configurations, so item-wise questions work on sites installed before child table support.
Only adds missing rows; nothing an admin configured is changed or removed."""

import frappe

from grid_erp.grid_ai.install import ITEM_DESCRIPTION, ITEM_DOCTYPES, ITEM_FIELDS, append_fields
from grid_erp.grid_ai.tools.registry import clear_registry_cache


def execute():
	for doctype in ITEM_DOCTYPES:
		if not frappe.db.exists("AI Tool Configuration", doctype):
			continue
		config = frappe.get_doc("AI Tool Configuration", doctype)
		if not config.enabled:
			continue  # not in use: the admin adds fields when enabling it
		if not append_fields(config, ITEM_FIELDS):
			continue
		if ITEM_DESCRIPTION.strip() not in (config.ai_description or ""):
			config.ai_description = (config.ai_description or "").rstrip() + ITEM_DESCRIPTION
		frappe.db.savepoint("grid_ai_item_fields")
		try:
			config.save(ignore_permissions=True)
		except Exception:
			# Never fail the migration over an optional improvement.
			frappe.db.rollback(save_point="grid_ai_item_fields")
			frappe.log_error(title=f"Grid AI: item fields not added to {doctype}")
	clear_registry_cache()
