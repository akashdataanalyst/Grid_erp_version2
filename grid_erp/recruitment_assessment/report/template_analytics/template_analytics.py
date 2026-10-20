from __future__ import annotations

import frappe


def execute(filters=None):
	columns = [
		{"label": "Template", "fieldname": "template", "fieldtype": "Link", "options": "Assessment Template", "width": 180},
		{"label": "Assessments", "fieldname": "assignments", "fieldtype": "Int", "width": 100},
		{"label": "Submitted", "fieldname": "submitted", "fieldtype": "Int", "width": 100},
		{"label": "Passed", "fieldname": "passed", "fieldtype": "Int", "width": 100},
		{"label": "Average Score", "fieldname": "avg_score", "fieldtype": "Float", "width": 120},
		{"label": "Pass %", "fieldname": "pass_percent", "fieldtype": "Float", "width": 100},
	]
	rows = frappe.db.sql(
		"""
		SELECT
			a.template AS template,
			COUNT(a.name) AS assignments,
			SUM(CASE WHEN a.submitted = 1 THEN 1 ELSE 0 END) AS submitted,
			SUM(CASE WHEN r.pass_fail = 'Passed' THEN 1 ELSE 0 END) AS passed,
			AVG(r.percentage) AS avg_score,
			CASE WHEN SUM(CASE WHEN a.submitted = 1 THEN 1 ELSE 0 END) = 0
				THEN 0
				ELSE ROUND((SUM(CASE WHEN r.pass_fail = 'Passed' THEN 1 ELSE 0 END) / SUM(CASE WHEN a.submitted = 1 THEN 1 ELSE 0 END)) * 100, 2)
			END AS pass_percent
		FROM `tabAssessment Assignment` a
		LEFT JOIN `tabAssessment Result` r ON r.assignment = a.name
		GROUP BY a.template
		ORDER BY a.template
		""",
		as_dict=True,
	)
	return columns, rows

