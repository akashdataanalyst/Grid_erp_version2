import re

import frappe
from frappe import _
from frappe.model.document import Document

INSTANCE = "WhatsApp Instance"


class WhatsAppSettings(Document):
	def validate(self):
		if self.default_country_code:
			code = re.sub(r"[\s+]", "", self.default_country_code)
			if not code.isdigit() or len(code) > 4:
				frappe.throw(_("Default Country Code must be 1-4 digits, e.g. 91"))
			self.default_country_code = code
		if (self.max_retries or 0) < 0 or (self.retry_delay or 0) < 0:
			frappe.throw(_("Max Retries and Retry Delay cannot be negative"))
		if self.default_instance and not frappe.db.get_value(INSTANCE, self.default_instance, "active"):
			frappe.throw(_("Default WhatsApp Instance {0} is not active").format(self.default_instance))

	def on_update(self):
		# Keep the Default Instance checkbox on instances in step with this setting.
		for name in frappe.get_all(INSTANCE, filters={"default_instance": 1}, pluck="name"):
			if name != self.default_instance:
				frappe.db.set_value(INSTANCE, name, "default_instance", 0)
		if self.default_instance:
			frappe.db.set_value(INSTANCE, self.default_instance, "default_instance", 1)
