from __future__ import annotations

import frappe

from hrms.hr.doctype.job_applicant.job_applicant import get_interview_details as hrms_get_interview_details

from .service import RecruitmentAssessmentService


@frappe.whitelist()
def get_interview_details(job_applicant: str) -> dict:
	data = hrms_get_interview_details(job_applicant) or {}
	if not isinstance(data, dict):
		data = {}

	if "interviews" not in data:
		data["interviews"] = {}
	if "stars" not in data:
		meta = frappe.get_meta("Interview")
		data["stars"] = meta.get_options("average_rating") or 5

	assignment_filters = {
		"job_applicant": job_applicant,
		"portal_status": ["in", ["Assigned", "Pending", "Started"]],
	}
	if frappe.db.has_column("Assessment Assignment", "submitted"):
		assignment_filters["submitted"] = 0
	assignments = frappe.get_all(
		"Assessment Assignment",
		filters=assignment_filters,
		fields=["name", "portal_status", "template", "valid_till", "creation AS assigned_on", "submitted"],
		order_by="creation desc",
	)
	for assignment in assignments:
		assignment.submitted = assignment.submitted or 0
	data["assessments"] = assignments
	data.setdefault("interview_rounds", [])
	return data


def assign_assessment_to_applicants(job_applicants, template, **kwargs):
	service = RecruitmentAssessmentService()
	results = []
	for applicant in job_applicants:
		results.append(service.create_assignment(applicant, template, **kwargs))
	return results
