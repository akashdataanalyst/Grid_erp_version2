"""Delete one Job Applicant together with everything recorded about them.

Job Applicant > "Delete Candidate Data" (Grid Interview Administrator / System Manager), one
applicant at a time. Frappe's normal delete is blocked because other records point at the
applicant (Interview → Video Session → Notification / Audit Log …). This deletes them in a safe
order:

    Assessment Assignments with their attempts      (recruitment_assessment.cleanup)
    Grid Interview: Processing Jobs, Analyses, Transcripts, Recordings, Consents, Access Sessions,
                    Results, References, Video Sessions, Notification Logs, Audit Logs
    HRMS: Interview Feedback, Interviews
    → Job Applicant

All of it runs in the request's single transaction: if any step fails, nothing is deleted.
Files (resume, documents, recordings) are removed from disk only by a background job queued after
the commit. One "candidate.data_deleted" audit row records who deleted what (counts only).

Not deleted: Job Offer, Appointment Letter and Employee Onboarding (hiring decisions — the delete
refuses while they exist), and the candidate's portal account (it may serve other applications).
"""

from __future__ import annotations

import frappe
from frappe import _

DELETE_ROLES = ("Grid Interview Administrator", "System Manager")

# HRMS records that mean the candidate was hired / offered: delete refuses while they exist.
BLOCKING_DOCTYPES = ("Job Offer", "Appointment Letter", "Employee Onboarding")

GI = "Grid Interview "
# Deletion order: whatever points at a record goes before it. The logs come first because their
# Dynamic Links (reference_doctype / reference_name) would block deleting the records they name.
GI_ORDER = (
	"Audit Log",
	"Notification Log",
	"Processing Job",
	"Analysis",
	"Transcript",
	"Recording",
	"Consent",
	"Access Session",
	"Result",
	"Reference",
	"Video Session",
)


def _names(doctype: str, filters) -> list[str]:
	return frappe.get_all(doctype, filters=filters, pluck="name") if filters is not None else []


def _referencing(doctype: str, refs: dict[str, list[str]]) -> list[str]:
	"""Rows of a reference_doctype / reference_name log that point at any collected record."""
	names = set()
	for ref_doctype, ref_names in refs.items():
		if ref_names:
			names.update(_names(doctype, {"reference_doctype": ref_doctype, "reference_name": ["in", ref_names]}))
	return sorted(names)


def _collect(job_applicant: str) -> dict:
	docs: dict[str, list[str]] = {}
	interviews = _names("Interview", {"job_applicant": job_applicant})
	docs["Interview"] = interviews
	docs["Interview Feedback"] = sorted(
		set(_names("Interview Feedback", {"job_applicant": job_applicant}))
		| set(_names("Interview Feedback", {"interview": ["in", interviews]}) if interviews else [])
	)
	docs["Assessment Assignment"] = _names("Assessment Assignment", {"job_applicant": job_applicant})

	def by_applicant_or_interview(doctype):
		names = set(_names(doctype, {"job_applicant": job_applicant}))
		if interviews:
			names.update(_names(doctype, {"interview": ["in", interviews]}))
		return sorted(names)

	sessions = by_applicant_or_interview(GI + "Video Session")
	docs[GI + "Video Session"] = sessions
	docs[GI + "Result"] = by_applicant_or_interview(GI + "Result")
	docs[GI + "Reference"] = _names(GI + "Reference", {"job_applicant": job_applicant})

	def by_session_or_interview(doctype):
		names = set(_names(doctype, {"video_session": ["in", sessions]}) if sessions else [])
		if interviews:
			names.update(_names(doctype, {"interview": ["in", interviews]}))
		return sorted(names)

	docs[GI + "Recording"] = by_session_or_interview(GI + "Recording")
	docs[GI + "Consent"] = by_session_or_interview(GI + "Consent")
	docs[GI + "Access Session"] = _names(GI + "Access Session", {"video_session": ["in", sessions]}) if sessions else []
	transcripts = set(by_session_or_interview(GI + "Transcript"))
	if docs[GI + "Recording"]:
		transcripts.update(_names(GI + "Transcript", {"recording": ["in", docs[GI + "Recording"]]}))
	docs[GI + "Transcript"] = sorted(transcripts)
	analyses = set(_names(GI + "Analysis", {"interview": ["in", interviews]}) if interviews else [])
	if transcripts:
		analyses.update(_names(GI + "Analysis", {"transcript": ["in", sorted(transcripts)]}))
	docs[GI + "Analysis"] = sorted(analyses)

	refs = {"Job Applicant": [job_applicant]}
	refs.update({doctype: names for doctype, names in docs.items() if names})
	docs[GI + "Processing Job"] = _referencing(GI + "Processing Job", refs)
	refs[GI + "Processing Job"] = docs[GI + "Processing Job"]
	docs[GI + "Notification Log"] = _referencing(GI + "Notification Log", refs)
	refs[GI + "Notification Log"] = docs[GI + "Notification Log"]
	docs[GI + "Audit Log"] = _referencing(GI + "Audit Log", refs)

	# Files attached to the applicant or to anything deleted here (assessment files are handled
	# by the assessment cleanup).
	files = set()
	for doctype, names in [("Job Applicant", [job_applicant]), *docs.items()]:
		if names and not doctype.startswith("Assessment"):
			files.update(
				frappe.get_all("File", filters={"attached_to_doctype": doctype, "attached_to_name": ["in", names]}, pluck="name")
			)
	recordings = frappe.get_all(
		GI + "Recording",
		filters={"name": ["in", docs[GI + "Recording"]], "storage_key": ["is", "set"]},
		fields=["storage_backend", "storage_key"],
	) if docs[GI + "Recording"] else []

	blockers = {doctype: _names(doctype, {"job_applicant": job_applicant}) for doctype in BLOCKING_DOCTYPES}
	return {
		"docs": docs,
		"files": sorted(files),
		"recording_files": [(r.storage_backend, r.storage_key) for r in recordings],
		"blockers": {doctype: names for doctype, names in blockers.items() if names},
	}


@frappe.whitelist()
def get_delete_summary(job_applicant: str) -> dict:
	"""What "Delete Candidate Data" would remove, for the confirm dialog."""
	frappe.only_for(DELETE_ROLES)
	applicant = frappe.get_doc("Job Applicant", job_applicant)
	collected = _collect(job_applicant)
	return {
		"applicant": applicant.applicant_name or job_applicant,
		"counts": {doctype: len(names) for doctype, names in collected["docs"].items() if names},
		"files": len(collected["files"]) + len(collected["recording_files"]),
		"blockers": collected["blockers"],
	}


@frappe.whitelist(methods=["POST"])
def delete_candidate_data(job_applicant: str, confirm: str | None = None) -> dict:
	frappe.only_for(DELETE_ROLES)
	if (confirm or "").strip() != job_applicant:
		frappe.throw(_("Type the Job Applicant ID {0} to confirm.").format(frappe.bold(job_applicant)))
	applicant = frappe.get_doc("Job Applicant", job_applicant)
	collected = _collect(job_applicant)
	if collected["blockers"]:
		frappe.throw(
			_("{0} has hiring records ({1}). Delete or cancel those first.").format(
				applicant.applicant_name or job_applicant,
				", ".join(f"{doctype}: {', '.join(names)}" for doctype, names in collected["blockers"].items()),
			)
		)
	docs = collected["docs"]

	# Assessments: same steps as "Delete with Attempts" on each assignment.
	from grid_erp.recruitment_assessment.cleanup import delete_assignment_records

	assessment_files = 0
	for assignment in docs["Assessment Assignment"]:
		assessment_files += delete_assignment_records(assignment)

	# Detach files so deleting their documents doesn't remove them from disk before the commit.
	for name in collected["files"]:
		frappe.db.set_value("File", name, {"attached_to_doctype": None, "attached_to_name": None}, update_modified=False)

	# Grid Interview. Audit Log rows are append-only; this flag (like the retention job's) lets
	# exactly the collected rows go.
	frappe.flags.in_candidate_data_delete = True
	try:
		for short in GI_ORDER:
			for name in docs[GI + short]:
				frappe.delete_doc(GI + short, name, ignore_permissions=True)
	finally:
		frappe.flags.in_candidate_data_delete = False

	# HRMS Interview Feedback and Interview are submittable: mark cancelled, then delete. Their
	# cancel hooks only recompute ratings / send updates for records that are being removed.
	for doctype in ("Interview Feedback", "Interview"):
		for name in docs[doctype]:
			if doctype == "Interview":
				frappe.db.set_value("Interview", name, "amended_from", None, update_modified=False)
			if frappe.db.get_value(doctype, name, "docstatus") == 1:
				frappe.db.set_value(doctype, name, "docstatus", 2, update_modified=False)
			frappe.delete_doc(doctype, name, ignore_permissions=True)

	# No force: if anything else still links to the applicant, Frappe stops and all of it rolls back.
	frappe.delete_doc("Job Applicant", job_applicant, ignore_permissions=True)

	if collected["files"] or collected["recording_files"]:
		frappe.enqueue(
			"grid_erp.candidate_data.delete_files",
			files=collected["files"],
			recording_files=collected["recording_files"],
			queue="short",
			enqueue_after_commit=True,
		)

	counts = {doctype: len(names) for doctype, names in docs.items() if names}
	from grid_erp.grid_interview.utils import audit

	audit.log(
		"candidate.data_deleted",
		applicant=job_applicant,
		counts=counts,
		files=len(collected["files"]) + len(collected["recording_files"]) + assessment_files,
	)
	return {"deleted": counts, "files": len(collected["files"]) + len(collected["recording_files"]) + assessment_files}


def delete_files(files: list[str], recording_files: list | None = None):
	"""Background job, runs only after the delete was committed."""
	for name in files:
		frappe.delete_doc("File", name, ignore_permissions=True, ignore_missing=True)
	if recording_files:
		from grid_erp.grid_interview.services.storage import get_storage

		for backend, key in recording_files:
			try:
				get_storage(backend).delete(key)
			except Exception:
				frappe.log_error(title=f"Candidate data delete: could not remove recording file {key}")
