from __future__ import annotations

import frappe
from frappe import _


RESULT_LABELS = {"Passed": "✅ Pass", "Failed": "❌ Fail", "Pending Review": "⏳ Pending Review"}


def execute(filters=None):
    filters = filters or {}

    columns = [
        {"label": _("Rank"), "fieldname": "rank", "fieldtype": "Int", "width": 70},
        {"label": _("Candidate"), "fieldname": "applicant_name", "fieldtype": "Data", "width": 180},
        {"label": _("Email"), "fieldname": "candidate_email", "fieldtype": "Data", "width": 200},
        {"label": _("Template"), "fieldname": "template", "fieldtype": "Link", "options": "Assessment Template", "width": 180},
        {"label": _("Job Opening"), "fieldname": "job_opening", "fieldtype": "Data", "width": 150},
        {"label": _("Score"), "fieldname": "total_score", "fieldtype": "Float", "width": 90},
        {"label": _("Max Marks"), "fieldname": "maximum_marks", "fieldtype": "Float", "width": 100},
        {"label": _("Percentage %"), "fieldname": "percentage", "fieldtype": "Percent", "width": 120},
        {"label": _("Result"), "fieldname": "result_label", "fieldtype": "Data", "width": 100},
        {"label": _("Submitted On"), "fieldname": "submitted_on", "fieldtype": "Datetime", "width": 150},
        {"label": _("Attempt"), "fieldname": "name", "fieldtype": "Link", "options": "Assessment Attempt", "width": 140},
    ]

    # HR scoring moves an attempt from Submitted to Evaluated; both are finished tests.
    conditions = ["at.status in ('Submitted', 'Evaluated')"]
    values = {}

    if filters.get("template"):
        conditions.append("aa.template = %(template)s")
        values["template"] = filters["template"]

    if filters.get("job_opening"):
        conditions.append("aa.job_opening = %(job_opening)s")
        values["job_opening"] = filters["job_opening"]

    if filters.get("from_date"):
        conditions.append("at.submitted_on >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("at.submitted_on <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    where = " AND ".join(conditions) if conditions else "1=1"

    data = frappe.db.sql(
        f"""
        SELECT
            at.name,
            aa.applicant_name,
            aa.candidate_email,
            aa.template,
            aa.job_opening,
            at.total_score,
            at.maximum_score AS maximum_marks,
            at.percentage,
            at.result_status,
            at.submitted_on
        FROM `tabAssessment Attempt` at
        LEFT JOIN `tabAssessment Assignment` aa ON at.assignment = aa.name
        WHERE {where}
        ORDER BY at.percentage DESC, at.total_score DESC, at.submitted_on ASC
        """,
        values,
        as_dict=True,
    )

    for rank, row in enumerate(data, start=1):
        row["rank"] = rank
        row["result_label"] = RESULT_LABELS.get(row.get("result_status"), row.get("result_status") or "")

    return columns, data


def get_filters_config():
    return [
        {
            "fieldname": "template",
            "label": _("Assessment Template"),
            "fieldtype": "Link",
            "options": "Assessment Template",
        },
        {
            "fieldname": "job_opening",
            "label": _("Job Opening"),
            "fieldtype": "Link",
            "options": "Job Opening",
        },
        {
            "fieldname": "from_date",
            "label": _("From Date"),
            "fieldtype": "Date",
        },
        {
            "fieldname": "to_date",
            "label": _("To Date"),
            "fieldtype": "Date",
        },
    ]
