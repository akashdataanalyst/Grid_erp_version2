"""One candidate portal: /candidate-portal also lists the candidate's assessments."""

import frappe
from frappe.utils import add_days, now_datetime

from grid_erp.grid_interview.services import candidates, portal
from grid_erp.grid_interview.tests.utils import RollbackTestCase, make_applicant, needs_hrms
from grid_erp.recruitment_assessment.portal import candidate_assessments


def _template() -> str:
	name = frappe.get_all("Assessment Template", pluck="name", limit=1)
	if name:
		return name[0]
	return frappe.get_doc(
		{"doctype": "Assessment Template", "template_name": "GI Portal Test", "is_active": 1}
	).insert(ignore_permissions=True).name


def _assign(applicant: str):
	from grid_erp.recruitment_assessment.service import RecruitmentAssessmentService

	return RecruitmentAssessmentService().create_assignment(applicant, _template())


@needs_hrms
class TestCandidatePortalAssessments(RollbackTestCase):
	def setUp(self):
		super().setUp()
		frappe.db.set_single_value("Grid Interview Settings", "enable_candidate_portal", 1)
		frappe.clear_cache(doctype="Grid Interview Settings")
		self.user = candidates.ensure_candidate_account("assess.cand@example.com", "Assess Cand", "Invitation")
		self.applicant = make_applicant("assess.cand@example.com", "Assess Cand")

	def own(self):
		frappe.set_user(self.user)
		try:
			return candidate_assessments(portal.own_applicants(candidates.require_candidate()))
		finally:
			frappe.set_user("Administrator")

	def test_open_assessment_has_token_link_until_used(self):
		assignment = _assign(self.applicant)
		cards = self.own()
		self.assertEqual([c["name"] for c in cards], [assignment.name])
		self.assertTrue(cards[0]["can_open"])
		self.assertIn("token=", cards[0]["link"])

		frappe.db.set_value("Assessment Assignment", assignment.name, "token_used_on", now_datetime())
		cards = self.own()
		self.assertEqual(cards[0]["link"], f"/assessment-portal?assignment={assignment.name}")

	def test_expired_or_submitted_cannot_be_opened(self):
		assignment = _assign(self.applicant)
		frappe.db.set_value("Assessment Assignment", assignment.name, "valid_till", add_days(now_datetime(), -1))
		card = self.own()[0]
		self.assertEqual(card["status"], "Expired")
		self.assertFalse(card["can_open"])
		self.assertIsNone(card["link"])

	def test_other_candidates_assessments_are_not_listed(self):
		other = make_applicant("someone.else@example.com", "Someone Else")
		_assign(other)
		self.assertEqual(self.own(), [])

	def test_candidate_lands_on_portal_not_desk(self):
		from frappe.apps import get_default_path

		from grid_erp.desktop import can_open_grid_erp

		self.assertFalse(can_open_grid_erp(self.user))
		self.assertTrue(can_open_grid_erp("Administrator"))
		frappe.set_user(self.user)
		try:
			self.assertIsNone(get_default_path())
		finally:
			frappe.set_user("Administrator")

	def test_portal_page_shows_assessments(self):
		_assign(self.applicant)
		from frappe.website.serve import get_response_content

		frappe.set_user(self.user)
		try:
			html = get_response_content("candidate-portal")
		finally:
			frappe.set_user("Administrator")
		self.assertIn("Candidate Portal", html)
		self.assertIn("Start assessment", html)
