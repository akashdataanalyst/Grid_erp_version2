import frappe
from frappe import _
from frappe.model.document import Document

from grid_erp.branding import ensure_editor, is_unlocked


class GridBrandingSettings(Document):
	def onload(self):
		self.set_onload("unlocked", is_unlocked())

	def validate(self):
		ensure_editor()
		if not is_unlocked():
			frappe.throw(_("Enter the branding password to edit these settings."), frappe.PermissionError)

	def on_update(self):
		# Desk boot info and rendered website pages are cached; drop them so every
		# user sees the new branding on their next page load.
		frappe.clear_cache()
