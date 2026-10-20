from __future__ import annotations

import frappe

from ...report_utils import assessment_assignment_open_filters


def execute(filters=None):
	columns = [
		{"label": "Assignment", "fieldname": "name", "fieldtype": "Link", "options": "Assessment Assignment", "width": 180},
		{"label": "Applicant", "fieldname": "applicant_name", "fieldtype": "Data", "width": 160},
		{"label": "Template", "fieldname": "template", "fieldtype": "Link", "options": "Assessment Template", "width": 160},
		{"label": "Status", "fieldname": "portal_status", "fieldtype": "Data", "width": 120},
		{"label": "Valid Till", "fieldname": "valid_till", "fieldtype": "Datetime", "width": 180},
	]
	data = frappe.get_all(
		"Assessment Assignment",
		filters=assessment_assignment_open_filters(),
		fields=["name", "applicant_name", "template", "portal_status", "valid_till"],
		order_by="creation desc",
	)
	return columns, data
