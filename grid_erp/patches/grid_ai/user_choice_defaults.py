"""AI Settings is a Single: new fields of an existing site get no value from their default.
Set the defaults of the user choice options once, only where nothing was saved yet."""

import frappe

DEFAULTS = {"allow_user_provider": 1, "company_key_models": "Free models only"}


def execute():
	singles = frappe.qb.Table("tabSingles")
	saved = set(
		frappe.qb.from_(singles)
		.select(singles.field)
		.where((singles.doctype == "AI Settings") & singles.field.isin(list(DEFAULTS)))
		.run(pluck=True)
	)
	for fieldname, value in DEFAULTS.items():
		if fieldname not in saved:
			frappe.db.set_single_value("AI Settings", fieldname, value)
	frappe.clear_document_cache("AI Settings", "AI Settings")
