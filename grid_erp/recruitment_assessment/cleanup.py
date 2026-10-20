"""Delete an Assessment Assignment together with everything recorded under it.

Assignment and Attempt link to each other (Attempt.assignment, Assignment.current_attempt), so
Frappe's normal delete is blocked either way. This deletes them in a safe order:

    Assessment Result, Snapshot, Recording, Notification
    → Assessment Attempt (answers, violations and audit logs are its child tables)
    → Assessment Assignment

All of it runs in the request's single transaction: if any step fails, nothing is deleted.
Uploaded files (videos, recordings, snapshots) are detached first and only removed from disk by a
background job that is queued after the commit, because deleting a File removes it from disk at once.

Only for wrong or test assignments. Real candidates' records are hiring evidence; to give a
candidate another test use "Assign Another Assessment" instead.
"""

from __future__ import annotations

import frappe
from frappe import _

DELETE_ROLES = ("HR Manager", "System Manager")
FINAL_RESULTS = ("Passed", "Failed")

# DocTypes stored per assignment, deleted before the Attempt (all have an `assignment` link).
DEPENDENT_DOCTYPES = (
	"Assessment Result",
	"Assessment Snapshot",
	"Assessment Recording",
	"Assessment Notification",
)

# Job Applicant mirror fields (install.py) cleared when its assessment is deleted.
JOB_APPLICANT_RESET = {
	"assessment_state": "Not Assigned",
	"assessment_assignment_link": None,
	"assessment_assigned_on": None,
	"assessment_assigned_by": None,
	"assessment_link": None,
	"assessment_status": None,
	"assessment_attempt_status": None,
	"assessment_score": 0,
	"assessment_email_sent": 0,
	"assessment_violation_count": 0,
	"assessment_recording": None,
}


def _collect(assignment: str) -> dict:
	attempts = frappe.get_all("Assessment Attempt", filters={"assignment": assignment}, pluck="name")
	docs = {doctype: frappe.get_all(doctype, filters={"assignment": assignment}, pluck="name") for doctype in DEPENDENT_DOCTYPES}
	docs["Assessment Attempt"] = attempts

	attached_to = [("Assessment Assignment", [assignment])] + [(doctype, names) for doctype, names in docs.items() if names]
	files = []
	for doctype, names in attached_to:
		files += frappe.get_all(
			"File", filters={"attached_to_doctype": doctype, "attached_to_name": ["in", names]}, pluck="name"
		)
	return {"docs": docs, "files": files}


@frappe.whitelist()
def get_delete_summary(assignment: str) -> dict:
	"""What "Delete with Attempts" would remove, for the confirm dialog."""
	frappe.only_for(DELETE_ROLES)
	doc = frappe.get_doc("Assessment Assignment", assignment)
	collected = _collect(assignment)
	results = frappe.get_all(
		"Assessment Result", filters={"assignment": assignment}, pluck="pass_fail"
	) + frappe.get_all("Assessment Attempt", filters={"assignment": assignment}, pluck="result_status")

	applicant_status = frappe.db.get_value("Job Applicant", doc.job_applicant, "status") if doc.job_applicant else None
	other_assignments = frappe.db.count(
		"Assessment Assignment", {"job_applicant": doc.job_applicant, "name": ["!=", assignment]}
	) if doc.job_applicant else 0

	from .workflow import is_auto_assign_enabled

	resend_on_save = bool(
		doc.job_applicant
		and not other_assignments
		and applicant_status == "Shortlisted"
		and is_auto_assign_enabled(frappe.get_doc("Job Applicant", doc.job_applicant))
	)
	return {
		"counts": {doctype: len(names) for doctype, names in collected["docs"].items()},
		"files": len(collected["files"]),
		"has_final_result": any(result in FINAL_RESULTS for result in results),
		"applicant": doc.applicant_name or doc.job_applicant,
		"resend_on_save": resend_on_save,
	}


@frappe.whitelist(methods=["POST"])
def delete_assignment_with_attempts(assignment: str, confirm: int = 0) -> dict:
	frappe.only_for(DELETE_ROLES)
	if not frappe.utils.cint(confirm):
		frappe.throw(_("Tick the confirmation checkbox to delete."))
	collected = _collect(assignment)
	files = delete_assignment_records(assignment)
	return {"deleted": {doctype: len(names) for doctype, names in collected["docs"].items()}, "files": files}


def delete_assignment_records(assignment: str) -> int:
	"""The delete itself, without permission / confirmation checks (callers do those). Also used by
	Job Applicant > Delete Candidate Data (grid_erp.candidate_data). Returns the number of files."""
	doc = frappe.get_doc("Assessment Assignment", assignment)
	collected = _collect(assignment)

	# Detach files so deleting their documents doesn't remove them from disk before the commit.
	for name in collected["files"]:
		frappe.db.set_value("File", name, {"attached_to_doctype": None, "attached_to_name": None}, update_modified=False)

	# Break the Assignment <-> Attempt and Job Applicant -> Assignment links.
	frappe.db.set_value("Assessment Assignment", assignment, "current_attempt", None, update_modified=False)
	if doc.job_applicant and frappe.db.get_value("Job Applicant", doc.job_applicant, "assessment_assignment_link") == assignment:
		_reset_job_applicant(doc.job_applicant, assignment)

	# Violation rows (child of Attempt) link back to their own Attempt and Assignment, which Frappe
	# counts as "linked" and blocks the delete. They go with the Attempt anyway, so remove them first.
	frappe.db.delete("Assessment Violation", {"assignment": assignment})
	for attempt in collected["docs"]["Assessment Attempt"]:
		frappe.db.delete("Assessment Violation", {"parent": attempt, "parenttype": "Assessment Attempt"})

	# No force: if anything else still links here, Frappe stops and the whole delete rolls back.
	for doctype in (*DEPENDENT_DOCTYPES, "Assessment Attempt"):
		for name in collected["docs"][doctype]:
			frappe.delete_doc(doctype, name, ignore_permissions=True)
	frappe.delete_doc("Assessment Assignment", assignment, ignore_permissions=True)

	if collected["files"]:
		frappe.enqueue(
			"grid_erp.recruitment_assessment.cleanup.delete_files",
			files=collected["files"],
			queue="short",
			enqueue_after_commit=True,
		)

	if doc.job_applicant:
		frappe.get_doc("Job Applicant", doc.job_applicant).add_comment(
			"Info",
			_("Assessment Assignment {0} ({1}) deleted with its attempts by {2}.").format(
				assignment, doc.template, frappe.session.user
			),
		)

	return len(collected["files"])


def _reset_job_applicant(job_applicant: str, deleted_assignment: str):
	"""Point the applicant at its next newest assignment, or back to Not Assigned."""
	from .utils import set_job_applicant_values

	remaining = frappe.get_all(
		"Assessment Assignment",
		filters={"job_applicant": job_applicant, "name": ["!=", deleted_assignment]},
		fields=["name", "portal_status"],
		order_by="creation desc",
		limit=1,
	)
	values = dict(JOB_APPLICANT_RESET)
	if remaining:
		values.update(
			{"assessment_assignment_link": remaining[0].name, "assessment_state": remaining[0].portal_status or "Assigned"}
		)
	set_job_applicant_values(job_applicant, values)


def delete_files(files: list[str]):
	"""Background job, runs only after the delete was committed."""
	for name in files:
		frappe.delete_doc("File", name, ignore_permissions=True, ignore_missing=True)
