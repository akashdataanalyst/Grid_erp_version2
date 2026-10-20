import frappe
from frappe import _
from frappe.model.document import Document

from grid_erp.grid_whatsapp.services.whatsapp_provider import registered_providers

SETTINGS = "WhatsApp Settings"


class WhatsAppInstance(Document):
	def validate(self):
		if self.provider not in registered_providers():
			frappe.throw(_("No provider named {0} is installed").format(self.provider))
		if not 1 <= (self.timeout or 30) <= 300:
			frappe.throw(_("Timeout must be between 1 and 300 seconds"))
		if self.api_base_url and not self.api_base_url.startswith(("https://", "http://")):
			frappe.throw(_("API Base URL must start with https://"))
		if self.default_instance and not self.active:
			frappe.throw(_("The default instance must be active"))

	def on_update(self):
		current = frappe.db.get_single_value(SETTINGS, "default_instance")
		if self.default_instance and current != self.name:
			frappe.db.set_value(
				"WhatsApp Instance", {"default_instance": 1, "name": ["!=", self.name]}, "default_instance", 0
			)
			_set_default(self.name)
		elif not self.default_instance and current == self.name:
			_set_default(None)

	def on_trash(self):
		if frappe.db.get_single_value(SETTINGS, "default_instance") == self.name:
			_set_default(None)


def _set_default(name):
	frappe.db.set_single_value(SETTINGS, "default_instance", name)
	frappe.clear_document_cache(SETTINGS, SETTINGS)
