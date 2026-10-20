from __future__ import annotations

import frappe


def execute(filters=None):
	columns = [
		{"label": "Violation Type", "fieldname": "violation_type", "fieldtype": "Data", "width": 180},
		{"label": "Assignment", "fieldname": "assignment", "fieldtype": "Link", "options": "Assessment Assignment", "width": 180},
		{"label": "Attempt", "fieldname": "attempt", "fieldtype": "Link", "options": "Assessment Attempt", "width": 180},
		{"label": "Severity", "fieldname": "severity", "fieldtype": "Data", "width": 100},
		{"label": "Timestamp", "fieldname": "timestamp", "fieldtype": "Datetime", "width": 180},
		{"label": "Remarks", "fieldname": "remarks", "fieldtype": "Small Text", "width": 240},
	]
	data = frappe.get_all(
		"Assessment Violation",
		fields=["violation_type", "assignment", "attempt", "severity", "timestamp", "remarks"],
		order_by="creation desc",
	)
	return columns, data
