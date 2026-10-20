"""Candidate portal home: the candidate's assessments, interviews, applications and results."""

from frappe import _

from grid_erp.grid_interview.services import portal
from grid_erp.grid_interview.www_helpers import candidate_page, fmt
from grid_erp.recruitment_assessment.portal import candidate_assessments

no_cache = 1
no_sitemap = 1

PILL = {
	"Scheduled": "blue", "In Progress": "orange", "Completed": "green", "Cancelled": "gray",
	"Expired": "gray", "No Show": "gray",
	"Applied": "gray", "Under Review": "blue", "Interview Scheduled": "blue", "Interview In Progress": "orange",
	"Interview Completed": "blue", "Next Round": "green", "On Hold": "orange", "Selected": "green", "Not Selected": "gray",
	# Assessment Assignment portal_status
	"Assigned": "blue", "Pending": "blue", "Started": "orange", "Submitted": "green", "Evaluated": "green",
	"Passed": "green", "Failed": "gray",
}


def get_context(context):
	context.title = _("Candidate Portal")
	profile = candidate_page(context, "index", "/candidate-portal")
	if not profile:
		return context
	data = portal.get_dashboard(profile)
	data["assessments"] = candidate_assessments(portal.own_applicants(profile))
	for a in data["assessments"]:
		a["valid_till_label"] = fmt(a["valid_till"], "d MMM yyyy, HH:mm")
		a["pill"] = PILL.get(a["status"], "gray")
	for card in data["upcoming"] + data["past"]:
		card["when"] = fmt(card["start"], "EEEE, d MMM yyyy")
		card["time"] = f"{fmt(card['start'], 'HH:mm')} – {fmt(card['end'], 'HH:mm')}"
		card["opens"] = fmt(card["join_opens_at"], "d MMM, HH:mm")
		card["pill"] = PILL.get(card["status"], "gray")
	for app in data["applications"]:
		app["pill"] = PILL.get(app["status"], "gray")
	for res in data["results"]:
		res["published_label"] = fmt(res["published_on"], "d MMM yyyy")
		res["pill"] = PILL.get(res["candidate_status"], "gray")
	context.data = data
	return context
