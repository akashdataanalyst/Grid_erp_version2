from __future__ import annotations

import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields, delete_custom_fields


def get_custom_fields():
	return {
		"Job Applicant": [
			# HR decides the assessment (Job Opening / Assign dialog), so the applicant form
			# only shows read-only status in its own tab.
			{
				"fieldname": "assessment_tab",
				"fieldtype": "Tab Break",
				"label": _("Assessment"),
				"insert_after": "upper_range",
			},
			{
				"fieldname": "assessment_state",
				"fieldtype": "Select",
				"label": _("Assessment State"),
				"options": "Not Assigned\nAssigned\nPending\nStarted\nCompleted\nSubmitted\nEvaluated\nPassed\nFailed\nExpired",
				"insert_after": "assessment_tab",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_assignment_link",
				"fieldtype": "Link",
				"label": _("Assessment Assignment"),
				"options": "Assessment Assignment",
				"insert_after": "assessment_state",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_summary_html",
				"fieldtype": "HTML",
				"label": _("Assessment Summary"),
				"insert_after": "assessment_assignment_link",
			},
			{
				"fieldname": "assessment_assigned_on",
				"fieldtype": "Datetime",
				"label": _("Assigned On"),
				"insert_after": "assessment_summary_html",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_assigned_by",
				"fieldtype": "Link",
				"label": _("Assigned By"),
				"options": "User",
				"insert_after": "assessment_assigned_on",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_link",
				"fieldtype": "Data",
				"label": _("Assessment Link"),
				"insert_after": "assessment_assigned_by",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_status",
				"fieldtype": "Data",
				"label": _("Assessment Status"),
				"insert_after": "assessment_link",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_attempt_status",
				"fieldtype": "Data",
				"label": _("Attempt Status"),
				"insert_after": "assessment_status",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_score",
				"fieldtype": "Percent",
				"label": _("Score"),
				"insert_after": "assessment_attempt_status",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_email_sent",
				"fieldtype": "Check",
				"label": _("Email Sent"),
				"insert_after": "assessment_score",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_violation_count",
				"fieldtype": "Int",
				"label": _("Violation Count"),
				"insert_after": "assessment_email_sent",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_recording",
				"fieldtype": "Attach",
				"label": _("Recording"),
				"insert_after": "assessment_violation_count",
				"read_only": 1,
			},
			{
				"fieldname": "assessment_snapshots",
				"fieldtype": "HTML",
				"label": _("Snapshots"),
				"insert_after": "assessment_recording",
			},
		],
		"Job Opening": [
			{
				"fieldname": "assessment_template",
				"fieldtype": "Link",
				"label": _("Assessment Template"),
				"options": "Assessment Template",
				"insert_after": "publish_salary_range",
				"description": _("Assessment given to applicants of this opening. Leave empty to use the default template from Assessment Settings."),
			},
			{
				"fieldname": "assessment_enabled",
				"fieldtype": "Check",
				"label": _("Auto-assign on Shortlist"),
				"description": _("Automatically send the assessment link to the applicant when they are marked Shortlisted."),
				"default": "0",
				"insert_after": "assessment_template",
			},
		],
		# Same fieldnames as Job Opening, so HRMS "Create Job Opening" copies them across.
		"Job Requisition": [
			{
				"fieldname": "assessment_section",
				"fieldtype": "Section Break",
				"label": _("Assessment"),
				"insert_after": "reason_for_requesting",
			},
			{
				"fieldname": "assessment_template",
				"fieldtype": "Link",
				"label": _("Assessment Template"),
				"options": "Assessment Template",
				"insert_after": "assessment_section",
				"description": _("Assessment for candidates of this position. Leave empty to use the default from Assessment Settings."),
			},
			{
				"fieldname": "assessment_enabled",
				"fieldtype": "Check",
				"label": _("Auto-assign on Shortlist"),
				"default": "1",
				"insert_after": "assessment_template",
				"description": _("Send the assessment link automatically when an applicant is marked Shortlisted."),
			},
		],
		"Assessment Assignment": [
			{
				"fieldname": "started",
				"fieldtype": "Check",
				"label": _("Started"),
				"default": "0",
				"read_only": 1,
				"insert_after": "portal_status",
			},
			{
				"fieldname": "submitted",
				"fieldtype": "Check",
				"label": _("Submitted"),
				"default": "0",
				"read_only": 1,
				"insert_after": "started",
			},
		],
	}


OBSOLETE_CUSTOM_FIELDS = {
	# Applicant used to pick the template on their own record; HR owns that choice now.
	"Job Applicant": [{"fieldname": "assessment_template_link"}],
}


def remove_obsolete_custom_fields():
	delete_custom_fields(OBSOLETE_CUSTOM_FIELDS)


def after_install():
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	remove_obsolete_custom_fields()
	sync_assignment_status_flags()
	make_roles()
	seed_recruitment_assessment_data()
	_ensure_assessment_settings()


def after_migrate():
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	remove_obsolete_custom_fields()
	sync_assignment_status_flags()
	sync_existing_job_applicant_assessments()
	make_roles()
	seed_recruitment_assessment_data()
	_ensure_assessment_settings()


def _ensure_assessment_settings():
	"""Create the Assessment Settings singleton if it doesn't exist."""
	try:
		if not frappe.db.exists("Assessment Settings", "Assessment Settings"):
			doc = frappe.new_doc("Assessment Settings")
			doc.ai_model = "gpt-4o-mini"
			doc.certificate_footer_text = (
				"This certificate is awarded upon successful completion of the recruitment assessment."
			)
			doc.insert(ignore_permissions=True)
			frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Assessment Settings creation failed")


def before_uninstall():
	delete_custom_fields(get_custom_fields())


def sync_assignment_status_flags():
	if not frappe.db.table_exists("Assessment Assignment"):
		return

	columns = frappe.db.get_table_columns("Assessment Assignment")
	if "started" in columns:
		frappe.db.sql(
			"""
			UPDATE `tabAssessment Assignment`
			SET `started` = CASE
				WHEN `portal_status` IN ('Started', 'Completed', 'Submitted', 'Evaluated', 'Passed', 'Failed') THEN 1
				ELSE IFNULL(`started`, 0)
			END
			"""
		)
	if "submitted" in columns:
		frappe.db.sql(
			"""
			UPDATE `tabAssessment Assignment`
			SET `submitted` = CASE
				WHEN `portal_status` IN ('Submitted', 'Evaluated', 'Passed', 'Failed') THEN 1
				ELSE IFNULL(`submitted`, 0)
			END
			"""
		)


def sync_existing_job_applicant_assessments():
	if not frappe.db.table_exists("Job Applicant"):
		return

	from .workflow import sync_job_applicant_assessment

	for row in frappe.get_all(
		"Job Applicant",
		fields=["name"],
		order_by="modified desc",
	):
		try:
			sync_job_applicant_assessment(frappe.get_doc("Job Applicant", row.name))
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"Assessment sync failed for Job Applicant {row.name}")


def make_roles():
	for role in ["Candidate Portal", "HR User", "HR Manager", "Recruiter", "Interviewer"]:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)


def seed_recruitment_assessment_data():
	if not frappe.db.table_exists("Assessment Template") or not frappe.db.table_exists("Question Bank"):
		return
	from .seed import seed_all

	seed_all()
