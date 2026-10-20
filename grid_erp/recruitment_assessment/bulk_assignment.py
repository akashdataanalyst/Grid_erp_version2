from __future__ import annotations

"""Bulk Assessment Assignment — assign one template to multiple Job Applicants at once."""

import frappe
from frappe import _
from frappe.utils import cint

from .service import RecruitmentAssessmentService


@frappe.whitelist()
def bulk_assign_assessment(
    applicant_names,
    template: str,
    validity_days: int = 3,
    duration_minutes: int = 60,
    passing_marks: float = 0,
    negative_marking: float = 0,
    randomize_questions: int = 1,
    shuffle_options: int = 1,
    max_attempts: int = 1,
    webcam_recording: int = 0,
    send_email: int = 1,
) -> dict:
    """Assign an assessment to multiple applicants in one call.

    ``applicant_names`` can be a JSON string or a Python list of Job Applicant names.
    Returns a summary dict with success/failure counts.
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection({"System Manager", "HR Manager", "HR User", "Recruiter"}):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    import json

    if isinstance(applicant_names, str):
        try:
            applicant_names = json.loads(applicant_names)
        except Exception:
            applicant_names = [a.strip() for a in applicant_names.split(",") if a.strip()]

    if not applicant_names:
        frappe.throw(_("No applicants selected."))

    if not template:
        frappe.throw(_("Assessment Template is required."))

    service = RecruitmentAssessmentService()
    success = []
    failed = []

    for name in applicant_names:
        try:
            assignment = service.create_assignment(
                job_applicant=name,
                template=template,
                validity_days=cint(validity_days),
                duration_minutes=cint(duration_minutes),
                passing_marks=passing_marks,
                negative_marking=negative_marking,
                randomize_questions=cint(randomize_questions),
                shuffle_options=cint(shuffle_options),
                max_attempts=cint(max_attempts),
                webcam_recording=cint(webcam_recording),
                send_email=cint(send_email),
            )
            success.append({"applicant": name, "assignment": assignment.name})
        except Exception as exc:
            failed.append({"applicant": name, "error": str(exc)})

    return {
        "success_count": len(success),
        "failed_count": len(failed),
        "success": success,
        "failed": failed,
        "message": _("{0} assigned, {1} failed").format(len(success), len(failed)),
    }


@frappe.whitelist()
def get_eligible_applicants(job_opening: str | None = None) -> list:
    """Return Job Applicants who don't yet have an assessment assigned."""
    filters = {"status": ["in", ["Open", "Replied", "Shortlisted", "Hold"]]}
    if job_opening:
        filters["job_title"] = job_opening

    applicants = frappe.get_all(
        "Job Applicant",
        filters=filters,
        fields=["name", "applicant_name", "email_id", "status", "job_title"],
        order_by="creation desc",
        limit=500,
    )

    # Filter out those who already have an assignment
    assigned = set(
        frappe.get_all(
            "Assessment Assignment",
            fields=["job_applicant"],
            pluck="job_applicant",
        )
    )

    return [a for a in applicants if a.name not in assigned]
