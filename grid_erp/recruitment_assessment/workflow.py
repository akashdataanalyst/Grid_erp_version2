from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import escape_html

from .service import RecruitmentAssessmentService
from .utils import set_job_applicant_values


SHORTLIST_STATUSES = {"Shortlisted"}


def get_default_assessment_template() -> str | None:
	return frappe.db.get_single_value("Assessment Settings", "default_assessment_template") or None


def get_job_opening_assessment_template(job_opening: str | None) -> str | None:
	if job_opening:
		template = frappe.db.get_value("Job Opening", job_opening, "assessment_template")
		if template:
			return template
	return get_default_assessment_template()


def get_job_applicant_assessment_template(job_applicant_doc) -> str | None:
	"""Assessment the applicant got (or will get): already assigned > Job Opening > default.

	The applicant never chooses this; HR sets it on the Job Opening or in the Assign dialog.
	"""
	existing_assignment = get_existing_assignment(job_applicant_doc.name)
	if existing_assignment:
		return existing_assignment.template
	return get_job_opening_assessment_template(job_applicant_doc.get("job_title"))


def is_auto_assign_enabled(job_applicant_doc) -> bool:
	"""Auto-send on Shortlist: on for everyone via Assessment Settings, or per Job Opening."""
	if frappe.db.get_single_value("Assessment Settings", "auto_assign_on_shortlist"):
		return True
	job_opening = job_applicant_doc.get("job_title")
	return bool(job_opening and frappe.db.get_value("Job Opening", job_opening, "assessment_enabled"))


def get_existing_assignment(job_applicant: str, template: str | None = None):
	filters = {"job_applicant": job_applicant}
	if template:
		filters["template"] = template
	rows = frappe.get_all(
		"Assessment Assignment",
		filters=filters,
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	name = rows[0].name if rows else None
	return frappe.get_doc("Assessment Assignment", name) if name else None


def build_assessment_summary_html(job_applicant_doc, assignment=None, template: str | None = None) -> str:
	template = template or get_job_applicant_assessment_template(job_applicant_doc)
	if not template:
		return _("<div class='text-muted'>No assessment set on the Job Opening or in Assessment Settings.</div>")
	template_label = frappe.db.get_value("Assessment Template", template, "template_name") or template

	if not assignment:
		hint = (
			_("Assessment link will be sent automatically when the applicant is marked Shortlisted.")
			if is_auto_assign_enabled(job_applicant_doc)
			else _("Not assigned yet. Use Assessment > Assign Assessment to send the link.")
		)
		return (
			"<div><b>Planned Assessment:</b> {template}</div>"
			"<div class='text-muted' style='margin-top:6px;'>{hint}</div>"
		).format(template=escape_html(template_label), hint=escape_html(hint))

	portal_link = assignment.get_portal_link()
	assignment_link = frappe.utils.get_link_to_form("Assessment Assignment", assignment.name)
	return (
		"<div><b>Assessment Template:</b> {template}</div>"
		"<div><b>Assignment:</b> <a href='{assignment_link}' target='_blank'>{assignment_name}</a></div>"
		"<div><b>Portal:</b> <a href='{portal_link}' target='_blank'>Open Candidate Portal</a></div>"
		"<div><b>Status:</b> {status}</div>"
	).format(
		template=escape_html(template_label),
		assignment_link=escape_html(assignment_link),
		assignment_name=escape_html(assignment.name),
		portal_link=escape_html(portal_link),
		status=escape_html(assignment.portal_status or "Assigned"),
	)


def _sync_job_applicant_fields(job_applicant_name: str, values: dict):
	set_job_applicant_values(job_applicant_name, values)


def sync_job_applicant_assessment(doc, method=None, force: bool = False):
	assignment = get_existing_assignment(doc.name)
	template = assignment.template if assignment else get_job_applicant_assessment_template(doc)

	if (
		not assignment
		and template
		and doc.status in SHORTLIST_STATUSES
		and (force or is_auto_assign_enabled(doc))
	):
		service = RecruitmentAssessmentService()
		if force:
			assignment = service.create_assignment(doc.name, template)
		else:
			# Auto-send must never block saving the applicant.
			try:
				assignment = service.create_assignment(doc.name, template)
				frappe.msgprint(_("Assessment link emailed to {0}.").format(doc.email_id), alert=True, indicator="green")
			except Exception:
				frappe.log_error(frappe.get_traceback(), _("Auto assessment on Shortlist failed"))
				frappe.msgprint(_("Could not send the assessment automatically. Use Assessment > Assign Assessment."), alert=True, indicator="orange")

	values = {
		"assessment_assignment_link": assignment.name if assignment else None,
		"assessment_state": assignment.portal_status if assignment else "Not Assigned",
	}
	_sync_job_applicant_fields(doc.name, values)
	return get_job_applicant_assessment_overview(doc.name)


@frappe.whitelist()
def get_job_applicant_assessment_overview(job_applicant: str) -> dict:
	doc = frappe.get_doc("Job Applicant", job_applicant)
	assignment = get_existing_assignment(doc.name)
	template = assignment.template if assignment else get_job_applicant_assessment_template(doc)
	return {
		"template": template,
		"template_name": frappe.db.get_value("Assessment Template", template, "template_name") if template else None,
		"assignment": assignment.name if assignment else None,
		"attempt": assignment.current_attempt if assignment else None,
		"portal_link": assignment.get_portal_link() if assignment else None,
		"portal_status": assignment.portal_status if assignment else "Not Assigned",
		"assessment_state": assignment.portal_status if assignment else "Not Assigned",
		"recording": frappe.db.get_value(
			"Assessment Attempt",
			assignment.current_attempt,
			"screen_recording_file",
		)
		if assignment and assignment.current_attempt
		else None,
		"summary_html": build_assessment_summary_html(doc, assignment, template),
	}


@frappe.whitelist()
def ensure_job_applicant_assessment(job_applicant: str) -> dict:
	doc = frappe.get_doc("Job Applicant", job_applicant)
	doc.check_permission("write")
	return sync_job_applicant_assessment(doc, force=True)
