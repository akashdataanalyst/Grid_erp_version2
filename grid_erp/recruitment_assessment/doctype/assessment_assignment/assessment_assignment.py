from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_url

from grid_erp.recruitment_assessment.base import BaseAssessmentDocument
from grid_erp.recruitment_assessment.utils import set_job_applicant_values


class AssessmentAssignment(BaseAssessmentDocument):
	def validate(self):
		self.portal_status = self.portal_status or "Assigned"
		self.assessment_state = self.assessment_state or "Assigned"
		self.max_attempts = self.max_attempts or 1

	def get_secret(self, fieldname: str) -> str | None:
		"""Password fields read back from the DB as '****'; the real value lives in __Auth."""
		value = self.get(fieldname)
		if value and set(value) != {"*"}:
			return value
		if self.is_new():
			return None
		return self.get_password(fieldname, raise_exception=False)

	def get_portal_link(self):
		path = f"/assessment-portal?assignment={self.name}&token={self.get_secret('assignment_token')}"
		base_url = (frappe.db.get_single_value("Assessment Settings", "portal_base_url") or "").strip().rstrip("/")
		return f"{base_url}{path}" if base_url else get_url(path)

	def on_update(self):
		if self.job_applicant and self.portal_status:
			set_job_applicant_values(
				self.job_applicant,
				{
					"assessment_state": self.portal_status,
					"assessment_status": self.portal_status,
					"assessment_assignment_link": self.name,
					"assessment_assigned_on": getattr(self, "assigned_on", None) or self.creation,
					"assessment_assigned_by": getattr(self, "assigned_by", None) or self.owner,
					"assessment_link": self.get_portal_link(),
					"assessment_email_sent": getattr(self, "email_sent", 0),
				},
			)
