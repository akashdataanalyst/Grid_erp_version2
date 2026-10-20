from __future__ import annotations

import frappe


def execute(filters=None):
	columns = [
		{"label": "Assignment", "fieldname": "name", "fieldtype": "Link", "options": "Assessment Assignment", "width": 180},
		{"label": "Applicant", "fieldname": "applicant_name", "fieldtype": "Data", "width": 160},
		{"label": "Template", "fieldname": "template", "fieldtype": "Link", "options": "Assessment Template", "width": 160},
		{"label": "Status", "fieldname": "portal_status", "fieldtype": "Data", "width": 120},
	]
	data = frappe.get_all("Assessment Assignment", filters={"portal_status": "Expired"}, fields=["name", "applicant_name", "template", "portal_status"], order_by="creation desc")
	return columns, data

