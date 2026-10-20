import frappe
from frappe import _
from frappe.model.document import Document

from grid_erp.grid_whatsapp.services import condition_engine, notification_engine, recipient_resolver, template_renderer
from grid_erp.grid_whatsapp.services.errors import WhatsAppError
from grid_erp.grid_whatsapp.services.field_resolver import get_field


class WhatsAppNotificationRule(Document):
	def validate(self):
		try:
			self.validate_document_type()
			self.validate_event()
			self.validate_condition()
			self.validate_template()
			self.validate_recipients()
		except WhatsAppError as e:
			frappe.throw(str(e), title=self.rule_name)

	def validate_document_type(self):
		meta = frappe.get_meta(self.document_type)
		if meta.istable:
			frappe.throw(_("{0} is a child table. Choose its parent DocType.").format(self.document_type))
		if self.document_type in notification_engine.OWN_DOCTYPES:
			frappe.throw(_("Rules cannot be created for WhatsApp DocTypes"))

	def validate_event(self):
		meta = frappe.get_meta(self.document_type)
		if self.event in notification_engine.SUBMIT_EVENTS and not meta.is_submittable:
			frappe.throw(_("{0} is not submittable, so {1} never happens").format(self.document_type, self.event))
		if self.event == notification_engine.ON_VALUE_CHANGE:
			self.value_changed_field = (self.value_changed_field or "").strip()
			get_field(self.document_type, self.value_changed_field)
		if self.event == notification_engine.WORKFLOW_STATE_CHANGE and not notification_engine.workflow_state_field(
			self.document_type
		):
			frappe.throw(_("{0} has no active Workflow").format(self.document_type))

	def validate_condition(self):
		if self.condition_type == "Expression":
			condition_engine.validate(self.condition_expression, self.document_type)

	def validate_template(self):
		template = frappe.get_doc("WhatsApp Template", self.template)
		if template.reference_doctype and template.reference_doctype != self.document_type:
			frappe.throw(
				_("Template {0} is for {1}, but this rule is for {2}").format(
					self.template, template.reference_doctype, self.document_type
				)
			)
		template_renderer.validate_template(template, self.document_type)
		if not template.active:
			frappe.msgprint(_("Template {0} is not active; messages will fail until it is.").format(self.template))
		if self.whatsapp_instance and not frappe.db.get_value("WhatsApp Instance", self.whatsapp_instance, "active"):
			frappe.msgprint(
				_("WhatsApp Instance {0} is inactive; messages will fail until it is.").format(self.whatsapp_instance)
			)

	def validate_recipients(self):
		if not any(row.enabled for row in self.recipients):
			frappe.throw(_("Add at least one enabled recipient"))
		for row in self.recipients:
			row.source_field = (row.source_field or "").strip()
			row.source_doctype = recipient_resolver.source_doctype_for(row, self.document_type)
			if row.enabled:
				recipient_resolver.validate_row(row, self.document_type)

	def on_update(self):
		notification_engine.clear_rule_cache()

	def on_trash(self):
		notification_engine.clear_rule_cache()

	def after_rename(self, old, new, merge=False):
		notification_engine.clear_rule_cache()
