# Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AIUserSettings(Document):
	"""Written only through grid_erp.grid_ai.api.my_ai (by the user themself). System Managers can see
	the choice (never the key) and delete the record to reset a user to the company default."""

	def on_update(self):
		frappe.cache.delete_keys(f"grid_ai:live_models:user:{self.user}")

	def on_trash(self):
		frappe.cache.delete_keys(f"grid_ai:live_models:user:{self.user}")
