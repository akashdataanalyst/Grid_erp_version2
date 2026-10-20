"""Delivery-status callbacks from WhatsApp providers.

URL to give the provider:
    https://<site>/api/method/grid_erp.grid_whatsapp.api.webhook.status_callback?instance=<WhatsApp Instance>
The provider must send the instance's Webhook Secret in the X-Webhook-Secret header.
Parsing the payload is provider specific: WhatsAppProvider.parse_status_callback()."""

import hmac

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from grid_erp.grid_whatsapp.services import dispatcher
from grid_erp.grid_whatsapp.services.errors import WhatsAppError
from grid_erp.grid_whatsapp.services.whatsapp_provider import get_provider, load_instance


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=1200, seconds=60)
def status_callback(instance: str):
	try:
		config = load_instance(instance)
	except WhatsAppError:
		raise frappe.AuthenticationError(_("Unknown or inactive WhatsApp Instance")) from None

	secret = frappe.get_request_header("X-Webhook-Secret") or ""
	if not config.webhook_secret or not hmac.compare_digest(secret, config.webhook_secret):
		raise frappe.AuthenticationError(_("Invalid webhook secret"))

	payload = frappe.request.get_json(silent=True) or dict(frappe.form_dict)
	payload.pop("cmd", None)
	payload.pop("instance", None)
	try:
		updates = get_provider(config).parse_status_callback(payload)
	except WhatsAppError as e:
		frappe.throw(str(e))

	updated = [
		dispatcher.update_status(update.message_id, update.status, update.timestamp, update.error)
		for update in updates
	]
	return {"updated": len([name for name in updated if name])}
