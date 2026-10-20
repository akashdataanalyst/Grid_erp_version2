"""Assessments shown on the candidate portal (/candidate-portal, www/candidate-portal).

The portal lists the signed-in candidate's Job Applicants (matched by email, see
grid_erp.grid_interview.services.portal.own_applicants); this returns their Assessment
Assignments. Taking the assessment still happens on /assessment-portal with the usual
token / temporary password; nothing about how a candidate signs in there changes.
"""

import frappe
from frappe.utils import get_datetime, now_datetime

# The candidate can still open the assessment in these states.
OPEN_STATES = ("Assigned", "Pending", "Started")


def candidate_assessments(applicants: list[str]) -> list[dict]:
	if not applicants:
		return []
	rows = frappe.get_all(
		"Assessment Assignment",
		filters={"job_applicant": ["in", applicants], "docstatus": ["<", 2]},
		fields=["name", "template", "job_opening", "designation", "portal_status", "valid_till",
				"duration_minutes", "token_used_on", "submitted"],
		order_by="creation desc",
		limit=50,
	)
	now = now_datetime()
	cards = []
	for row in rows:
		expired = bool(row.valid_till and get_datetime(row.valid_till) < now)
		status = "Expired" if expired and row.portal_status in OPEN_STATES else row.portal_status
		can_open = status in OPEN_STATES and not row.submitted
		cards.append({
			"name": row.name,
			"title": frappe.db.get_value("Assessment Template", row.template, "template_name") or row.template,
			"job_title": _job_title(row),
			"status": status,
			"valid_till": row.valid_till,
			"duration_minutes": row.duration_minutes,
			"can_open": can_open,
			"link": _link(row) if can_open else None,
		})
	return cards


def _job_title(row) -> str:
	if row.job_opening:
		return frappe.db.get_value("Job Opening", row.job_opening, "job_title") or row.job_opening
	return row.designation or ""


def _link(row) -> str:
	# The emailed token works once. Before it is used the portal gives the full link; after
	# that /assessment-portal resumes the browser's session or asks for the temporary password.
	if not row.token_used_on:
		return frappe.get_doc("Assessment Assignment", row.name).get_portal_link()
	return f"/assessment-portal?assignment={row.name}"
