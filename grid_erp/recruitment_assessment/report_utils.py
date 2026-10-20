from __future__ import annotations

import frappe


def as_report_rows(doctype: str, fields: list[str], filters: dict | None = None, order_by: str = "creation desc"):
	return frappe.get_all(doctype, filters=filters or {}, fields=fields, order_by=order_by)


def assessment_assignment_open_filters():
	filters = {"portal_status": ["in", ["Assigned", "Pending", "Started"]]}
	if frappe.db.table_exists("Assessment Assignment") and "submitted" in frappe.db.get_table_columns(
		"Assessment Assignment"
	):
		filters["submitted"] = 0
	return filters
