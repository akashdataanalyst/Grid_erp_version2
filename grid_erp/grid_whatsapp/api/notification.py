"""Desk actions for administrators: preview a rule, send now, resend a message."""

import frappe
from frappe import _

from grid_erp.grid_whatsapp.install import MANAGER_ROLE
from grid_erp.grid_whatsapp.services import condition_engine, notification_engine, recipient_resolver, template_renderer
from grid_erp.grid_whatsapp.services.errors import WhatsAppError
from grid_erp.grid_whatsapp.services.field_resolver import Resolver

MANAGERS = ("System Manager", MANAGER_ROLE)


def _load(rule, docname):
	frappe.only_for(MANAGERS)
	rule_doc = frappe.get_doc(notification_engine.RULE, rule)
	doc = frappe.get_doc(rule_doc.document_type, docname)
	doc.check_permission("read")
	return rule_doc, doc


@frappe.whitelist()
def preview(rule: str, docname: str):
	"""What the rule would send for a document, without sending or logging anything."""
	rule_doc, doc = _load(rule, docname)
	resolver = Resolver(doc)
	result = {"condition": None, "message": None, "variables": {}, "recipients": [], "errors": []}

	if rule_doc.condition_type == "Expression":
		try:
			result["condition"] = condition_engine.evaluate(rule_doc.condition_expression, doc, resolver=resolver)
		except WhatsAppError as e:
			result["errors"].append(_("Condition: {0}").format(e))
	else:
		result["condition"] = True

	try:
		message = template_renderer.render(rule_doc.template, doc, resolver)
		result["message"] = message.body
		result["variables"] = message.variables
	except WhatsAppError as e:
		result["errors"].append(_("Template: {0}").format(e))

	try:
		recipients, failures = recipient_resolver.resolve(rule_doc, doc, resolver)
		result["recipients"] = [
			{"number": r.number, "label": r.label, "type": r.recipient_type, "source": r.source} for r in recipients
		]
		result["errors"] += [f"{f.label}: {f.error}" for f in failures]
	except WhatsAppError as e:
		result["errors"].append(_("Recipients: {0}").format(e))
	return result


@frappe.whitelist(methods=["POST"])
def send_now(rule: str, docname: str):
	"""Send the rule's message for a document now, ignoring its event and condition."""
	rule_doc, doc = _load(rule, docname)
	notification_engine.send_manually(rule_doc.name, doc.doctype, doc.name)
	return True


@frappe.whitelist(methods=["POST"])
def resend(log: str):
	frappe.only_for(MANAGERS)
	if not frappe.db.get_single_value("WhatsApp Settings", "allow_manual_resend"):
		frappe.throw(_("Manual resend is switched off in WhatsApp Settings"))
	frappe.has_permission(notification_engine.LOG, "read", log, throw=True)
	return notification_engine.resend(log)
