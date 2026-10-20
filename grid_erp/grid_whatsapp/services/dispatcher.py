"""Send one WhatsApp Notification Log through its provider, with retries.

Log status flow:
    Queued -> Processing -> Sent (-> Delivered -> Read, via provider callbacks)
                         -> Queued again with next_retry_at   (temporary error, retries left)
                         -> Failed                             (permanent error or retries used up)

Retries are picked up by process_due_retries(), which the scheduler runs every
minute, so a retry survives worker restarts."""

import json

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from grid_erp.grid_whatsapp.services.errors import TransientError, WhatsAppError
from grid_erp.grid_whatsapp.services.whatsapp_provider import (
	OutgoingMessage,
	ProviderResult,
	get_provider,
	load_instance,
)
from grid_erp.grid_whatsapp.utils.sanitize import to_log_text

LOG = "WhatsApp Notification Log"
FINAL_STATUSES = ("Sent", "Delivered", "Read", "Failed", "Cancelled")
STATUS_ORDER = {"Sent": 1, "Delivered": 2, "Read": 3}
RETRY_BATCH = 200


def settings():
	return frappe.get_cached_doc("WhatsApp Settings")


def enqueue_send(log_name):
	frappe.enqueue(
		"grid_erp.grid_whatsapp.services.dispatcher.send_log",
		queue="short",
		job_id=f"grid_whatsapp:log:{log_name}",
		deduplicate=True,
		enqueue_after_commit=True,
		log_name=log_name,
	)


def send_log(log_name, commit=True):
	"""Attempt delivery of one log. commit=False when running inside the
	document's own transaction (Send in Background switched off)."""
	if not frappe.db.exists(LOG, log_name):
		return
	log = frappe.get_doc(LOG, log_name, for_update=True)
	if log.status != "Queued":
		# Already handled by another worker, or cancelled.
		return

	log.db_set({"status": "Processing", "last_attempt_at": now_datetime()}, update_modified=False)
	if commit:
		frappe.db.commit()

	secrets = []
	try:
		instance = load_instance(log.whatsapp_instance)
		secrets = instance.secrets
		message = _outgoing_message(log)
		if settings().test_mode:
			result = ProviderResult(
				message_id=f"TEST-{log.name}",
				response={"test_mode": True, "note": "Not sent: Test Mode is on in WhatsApp Settings"},
			)
		else:
			result = get_provider(instance).send(message)
		_mark_sent(log, result, secrets)
	except WhatsAppError as e:
		_mark_failed(log, e, secrets)
	except Exception as e:
		# Unexpected errors (bugs, library errors) are recorded in Error Log with a
		# traceback and retried like temporary failures.
		frappe.log_error(f"WhatsApp send failed: {log.name}", reference_doctype=LOG, reference_name=log.name)
		_mark_failed(log, TransientError(_("Unexpected error: {0}").format(e)), secrets)

	if commit:
		frappe.db.commit()


def _outgoing_message(log):
	template = frappe.get_cached_doc("WhatsApp Template", log.template) if log.template else None
	return OutgoingMessage(
		recipient=log.recipient,
		body=log.message or "",
		variables=json.loads(log.variables or "{}"),
		template_name=template.provider_template_name if template else None,
		template_id=template.provider_template_id if template else None,
		language=template.language if template else None,
		reference=log.name,
	)


def _mark_sent(log, result, secrets):
	now = now_datetime()
	values = {
		"status": result.status if result.status in STATUS_ORDER else "Sent",
		"message_id": result.message_id,
		"request_reference": result.request_reference,
		"response": to_log_text(result.response, secrets),
		"error": "",
		"next_retry_at": None,
		"sent_at": now,
	}
	if values["status"] == "Delivered":
		values["delivered_at"] = now
	log.db_set(values)


def _mark_failed(log, error, secrets):
	conf = settings()
	message = to_log_text(str(error), secrets)
	can_retry = error.retryable and conf.retry_enabled and (log.retry_count or 0) < (conf.max_retries or 0)
	if can_retry:
		retry_at = add_to_date(now_datetime(), minutes=max(conf.retry_delay or 0, 0))
		log.db_set(
			{
				"status": "Queued",
				"retry_count": (log.retry_count or 0) + 1,
				"next_retry_at": retry_at,
				"error": _("Attempt {0} failed, retrying at {1}: {2}").format(
					(log.retry_count or 0) + 1, retry_at.strftime("%Y-%m-%d %H:%M"), message
				),
			}
		)
	else:
		log.db_set({"status": "Failed", "next_retry_at": None, "error": message})


def process_due_retries():
	"""Scheduler job: queue every log whose retry time has come."""
	due = frappe.get_all(
		LOG,
		filters={"status": "Queued", "next_retry_at": ["<=", now_datetime()]},
		pluck="name",
		order_by="next_retry_at asc",
		limit=RETRY_BATCH,
	)
	for name in due:
		enqueue_send(name)


def update_status(message_id, status, timestamp=None, error=None):
	"""Apply a provider delivery receipt. Never moves a message backwards (Read stays Read)."""
	log_name = frappe.db.get_value(LOG, {"message_id": message_id}, "name")
	if not log_name:
		return None
	log = frappe.get_doc(LOG, log_name)
	when = timestamp or now_datetime()
	if status == "Failed":
		log.db_set({"status": "Failed", "error": to_log_text(error or _("Provider reported delivery failure"))})
	elif STATUS_ORDER.get(status, 0) > STATUS_ORDER.get(log.status, 0):
		values = {"status": status}
		if status == "Delivered":
			values["delivered_at"] = when
		if status == "Read":
			values["read_at"] = when
			values["delivered_at"] = log.delivered_at or when
		log.db_set(values)
	return log_name
