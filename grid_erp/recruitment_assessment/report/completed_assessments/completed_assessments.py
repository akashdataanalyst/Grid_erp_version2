from __future__ import annotations

import frappe


def execute(filters=None):
	columns = [
		{"label": "Assignment", "fieldname": "assignment", "fieldtype": "Link", "options": "Assessment Assignment", "width": 180},
		{"label": "Applicant", "fieldname": "job_applicant", "fieldtype": "Link", "options": "Job Applicant", "width": 180},
		{"label": "Template", "fieldname": "template", "fieldtype": "Link", "options": "Assessment Template", "width": 160},
		{"label": "Result", "fieldname": "pass_fail", "fieldtype": "Data", "width": 120},
		{"label": "Percentage", "fieldname": "percentage", "fieldtype": "Float", "width": 120},
	]
	data = frappe.get_all("Assessment Result", fields=["assignment", "job_applicant", "template", "pass_fail", "percentage"], order_by="creation desc")
	return columns, data

