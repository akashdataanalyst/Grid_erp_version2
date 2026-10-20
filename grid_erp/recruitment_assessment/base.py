from __future__ import annotations

import frappe
from frappe.model.document import Document


class BaseAssessmentDocument(Document):
	def autoname(self):
		if not self.name or self.name.startswith("New "):
			series = getattr(self.meta, "autoname", "") or self.doctype[:3].upper() + "-.#####"
			self.name = frappe.model.naming.make_autoname(series)

