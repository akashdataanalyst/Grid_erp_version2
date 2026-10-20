import frappe
from frappe.model.document import Document

from grid_erp.grid_whatsapp.services import template_renderer
from grid_erp.grid_whatsapp.services.errors import WhatsAppError


class WhatsAppTemplate(Document):
	def validate(self):
		for row in self.variables:
			row.variable_name = (row.variable_name or "").strip()
			row.source_field = (row.source_field or "").strip()
		try:
			template_renderer.validate_template(self, self.reference_doctype)
		except WhatsAppError as e:
			frappe.throw(str(e), title=self.template_name)
