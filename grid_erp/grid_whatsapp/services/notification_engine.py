"""Generic notification engine.

    document event (hooks.py, wildcard)
      -> handle_doc_event: cached lookup of enabled rules for the DocType + event
      -> per rule: value-change / workflow check, condition (condition_engine)
      -> queue a job (after the document's transaction commits)
    background job
      -> process_trigger: render template, resolve recipients
      -> one WhatsApp Notification Log per recipient (idempotent)
      -> dispatcher.send_log -> provider

Nothing here knows any DocType, field or person: rules supply all of it."""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from grid_erp.grid_whatsapp.services import condition_engine, dispatcher, recipient_resolver, template_renderer
from grid_erp.grid_whatsapp.services.errors import ConfigurationError, WhatsAppError
from grid_erp.grid_whatsapp.services.field_resolver import Resolver
from grid_erp.grid_whatsapp.services.whatsapp_provider import load_instance
from grid_erp.grid_whatsapp.utils.logger import debug
from grid_erp.grid_whatsapp.utils.sanitize import to_log_text

RULE = "WhatsApp Notification Rule"
LOG = dispatcher.LOG
OWN_DOCTYPES = frozenset(
	{
		RULE,
		LOG,
		"WhatsApp Settings",
		"WhatsApp Instance",
		"WhatsApp Template",
		"WhatsApp Template Variable",
		"WhatsApp Notification Recipient",
	}
)

AFTER_INSERT = "After Insert"
AFTER_SAVE = "After Save"
AFTER_SUBMIT = "After Submit"
AFTER_CANCEL = "After Cancel"
ON_UPDATE = "On Update"
ON_VALUE_CHANGE = "On Value Change"
WORKFLOW_STATE_CHANGE = "Workflow State Change"
MANUAL = "Manual"

# Rule event -> Frappe document method that fires it.
EVENT_METHODS = {
	AFTER_INSERT: "after_insert",
	AFTER_SAVE: "on_update",
	AFTER_SUBMIT: "on_submit",
	AFTER_CANCEL: "on_cancel",
	ON_UPDATE: "on_change",
	ON_VALUE_CHANGE: "on_change",
	WORKFLOW_STATE_CHANGE: "on_change",
}
SUBMIT_EVENTS = (AFTER_SUBMIT, AFTER_CANCEL)

RULE_FIELDS = (
	"name",
	"event",
	"value_changed_field",
	"workflow_state",
	"condition_type",
	"condition_expression",
	"priority",
	"allow_duplicate",
	"queue_enabled",
)
CACHE_DOCTYPES = "grid_whatsapp:rule_doctypes"
CACHE_RULES = "grid_whatsapp:rules"


# ---------------------------------------------------------------------------
# Rule cache
# ---------------------------------------------------------------------------


def rules_for(doctype):
	"""Enabled rules of a DocType, highest priority first. One Redis read when the
	DocType has no rules; the database is queried only after a rule changes."""
	doctypes = frappe.cache.get_value(CACHE_DOCTYPES, generator=_doctypes_with_rules)
	if doctype not in doctypes:
		return []
	return frappe.cache.hget(CACHE_RULES, doctype, generator=lambda: _load_rules(doctype))


def _doctypes_with_rules():
	return sorted(set(frappe.get_all(RULE, filters={"enabled": 1}, pluck="document_type", distinct=True)))


def _load_rules(doctype):
	return frappe.get_all(
		RULE,
		filters={"enabled": 1, "document_type": doctype},
		fields=list(RULE_FIELDS),
		order_by="priority desc, name asc",
	)


def clear_rule_cache():
	frappe.cache.delete_value(CACHE_DOCTYPES)
	frappe.cache.delete_value(CACHE_RULES)


# ---------------------------------------------------------------------------
# Document events
# ---------------------------------------------------------------------------


def handle_doc_event(doc, method=None):
	"""Wildcard doc_events handler. Never raises unless Failure Behaviour is Block Document."""
	if _skip(doc):
		return
	rules = rules_for(doc.doctype)
	if not rules:
		return
	for rule in rules:
		if EVENT_METHODS.get(rule.event) != method:
			continue
		try:
			_evaluate_rule(rule, doc)
		except Exception as e:
			_on_trigger_error(rule, doc, e)


def _skip(doc):
	flags = frappe.flags
	return bool(
		flags.in_install
		or flags.in_migrate
		or flags.in_patch
		or flags.in_import
		or flags.in_setup_wizard
		or doc.doctype in OWN_DOCTYPES
		or doc.flags.get("skip_whatsapp_notifications")
	)


def _evaluate_rule(rule, doc):
	trigger = rule.event
	if rule.event == ON_VALUE_CHANGE:
		field = rule.value_changed_field
		if not field or not doc.has_value_changed(field):
			return
		trigger = f"{rule.event}:{field}={doc.get(field)}"
	elif rule.event == WORKFLOW_STATE_CHANGE:
		field = workflow_state_field(doc.doctype)
		if not field or not doc.has_value_changed(field):
			return
		state = doc.get(field)
		if rule.workflow_state and state != rule.workflow_state:
			return
		trigger = f"{rule.event}:{state}"

	if rule.condition_type == "Expression":
		if not condition_engine.evaluate(rule.condition_expression, doc, previous=doc.get_doc_before_save()):
			debug("%s %s: rule %s condition is false", doc.doctype, doc.name, rule.name)
			return

	if rule.allow_duplicate:
		# Each matching save/change is its own trigger.
		trigger = f"{trigger}@{doc.modified}"
	queue_trigger(rule, doc, trigger)


def workflow_state_field(doctype):
	from frappe.model.workflow import get_workflow_name, get_workflow_state_field

	workflow = get_workflow_name(doctype)
	return get_workflow_state_field(workflow) if workflow else None


def queue_trigger(rule, doc, trigger):
	kwargs = {"rule": rule.name, "doctype": doc.doctype, "name": doc.name, "trigger": trigger}
	if rule.queue_enabled and dispatcher.settings().queue_enabled and not frappe.flags.in_test:
		frappe.enqueue(
			"grid_erp.grid_whatsapp.services.notification_engine.process_trigger",
			queue="short",
			enqueue_after_commit=True,
			job_id="grid_whatsapp:" + _hash(rule.name, doc.doctype, doc.name, trigger),
			deduplicate=True,
			**kwargs,
		)
	else:
		process_trigger(**kwargs, commit=False)


def _on_trigger_error(rule, doc, error):
	"""An error while evaluating a rule during save (e.g. an invalid condition)."""
	message = str(error) if isinstance(error, WhatsAppError) else _("Unexpected error: {0}").format(error)
	if not isinstance(error, WhatsAppError):
		frappe.log_error(f"WhatsApp rule {rule.name} failed", reference_doctype=doc.doctype, reference_name=doc.name)
	try:
		_save_log(
			{
				"reference_doctype": doc.doctype,
				"reference_name": doc.name,
				"rule": rule.name,
				"event": rule.event,
				"status": "Failed",
				"error": to_log_text(message),
			},
			key=_hash(rule.name, doc.doctype, doc.name, rule.event, "rule-error"),
		)
	except Exception:
		frappe.log_error(f"WhatsApp rule {rule.name}: could not write the failure log")
	behaviour = dispatcher.settings().failure_behavior
	if behaviour == "Block Document":
		frappe.throw(_("WhatsApp notification rule {0} failed: {1}").format(rule.name, message))
	if behaviour == "Show Message":
		frappe.msgprint(
			_("WhatsApp notification rule {0} failed: {1}").format(rule.name, message),
			indicator="orange",
			alert=True,
		)


# ---------------------------------------------------------------------------
# Background job
# ---------------------------------------------------------------------------


def process_trigger(rule, doctype, name, trigger, commit=True):
	"""Render, resolve recipients and send for one rule + document + trigger."""
	if not frappe.db.exists(RULE, rule):
		return
	rule_doc = frappe.get_doc(RULE, rule)
	if not rule_doc.enabled:
		debug("Rule %s disabled before its job ran", rule)
		return
	if not frappe.db.exists(doctype, name):
		debug("%s %s deleted before its job ran", doctype, name)
		return
	doc = frappe.get_doc(doctype, name)

	instance = rule_doc.whatsapp_instance or dispatcher.settings().default_instance
	base = {
		"reference_doctype": doctype,
		"reference_name": name,
		"rule": rule,
		"event": trigger.split("@", 1)[0][:140],
		"template": rule_doc.template,
		"whatsapp_instance": instance,
	}

	try:
		load_instance(instance)
		resolver = Resolver(doc)
		message = template_renderer.render(rule_doc.template, doc, resolver)
		recipients, failures = recipient_resolver.resolve(rule_doc, doc, resolver)
		if not recipients and not failures:
			raise ConfigurationError(_("No recipient with a mobile number was found for this document"))
	except Exception as e:
		if not isinstance(e, WhatsAppError):
			frappe.log_error(f"WhatsApp rule {rule} failed", reference_doctype=doctype, reference_name=name)
			e = _("Unexpected error: {0}").format(e)
		_save_log({**base, "status": "Failed", "error": to_log_text(str(e))}, key=_hash(rule, doctype, name, trigger))
		_commit(commit)
		return

	for failure in failures:
		_save_log(
			{
				**base,
				"status": "Failed",
				"recipient": failure.recipient,
				"recipient_label": failure.label,
				"recipient_type": failure.recipient_type,
				"error": to_log_text(failure.error),
			},
			key=_hash(rule, doctype, name, trigger, "failure", failure.label, failure.recipient),
		)

	variables = json.dumps(message.variables, ensure_ascii=False)
	to_send = []
	for recipient in recipients:
		log_name = _save_log(
			{
				**base,
				"status": "Queued",
				"recipient": recipient.number,
				"recipient_label": recipient.label,
				"recipient_type": recipient.recipient_type,
				"message": message.body,
				"variables": variables,
			},
			key=_hash(rule, doctype, name, trigger, recipient.number),
		)
		if log_name:
			to_send.append(log_name)
	_commit(commit)

	for log_name in to_send:
		dispatcher.send_log(log_name, commit=commit)


def _commit(commit):
	if commit:
		frappe.db.commit()


def _hash(*parts):
	return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _save_log(values, key):
	"""Create the log for an idempotency key, or reuse a Failed/Cancelled one.

	Returns the log name when it should be sent now, None when this exact
	notification already exists (queued, sent, delivered or read)."""
	existing = frappe.db.get_value(LOG, {"idempotency_key": key}, ["name", "status"], as_dict=True)
	if existing:
		if existing.status not in ("Failed", "Cancelled"):
			debug("Duplicate WhatsApp notification skipped (log %s is %s)", existing.name, existing.status)
			return None
		log = frappe.get_doc(LOG, existing.name)
		log.update({**values, "retry_count": 0, "next_retry_at": None, "error": values.get("error", "")})
		log.save(ignore_permissions=True)
		return log.name if log.status == "Queued" else None

	log = frappe.get_doc({"doctype": LOG, **values, "idempotency_key": key})
	frappe.db.savepoint("grid_whatsapp_log")
	try:
		log.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Another worker created the same notification at the same moment.
		frappe.db.rollback(save_point="grid_whatsapp_log")
		return None
	return log.name if log.status == "Queued" else None


# ---------------------------------------------------------------------------
# Manual sending and resending
# ---------------------------------------------------------------------------


def send_manually(rule, doctype, name):
	"""Send a rule's message for a document now, regardless of its event and condition."""
	rule_doc = frappe.get_doc(RULE, rule)
	if rule_doc.document_type != doctype:
		frappe.throw(_("Rule {0} is for {1}, not {2}").format(rule, rule_doc.document_type, doctype))
	doc = frappe.get_doc(doctype, name)
	queue_trigger(rule_doc, doc, f"{MANUAL}@{now_datetime().isoformat()}")


def resend(log_name):
	"""Send a logged message again. Failed/Cancelled logs are retried in place; for a
	sent message a new log is created so the history of the first one is kept."""
	log = frappe.get_doc(LOG, log_name)
	if not log.recipient or not log.message:
		frappe.throw(_("This log has no recipient or message to send. Fix the cause and trigger the rule again."))
	if log.status in ("Failed", "Cancelled"):
		log.db_set({"status": "Queued", "retry_count": 0, "next_retry_at": None, "error": ""})
		dispatcher.enqueue_send(log.name)
		return log.name
	if log.status in ("Queued", "Processing"):
		frappe.throw(_("This message is still being sent"))

	copy = frappe.copy_doc(log)
	copy.update(
		{
			"status": "Queued",
			"event": f"{log.event} (resend)"[:140],
			"idempotency_key": _hash(log.idempotency_key, "resend", now_datetime().isoformat()),
			"message_id": None,
			"request_reference": None,
			"response": None,
			"error": None,
			"retry_count": 0,
			"next_retry_at": None,
			"sent_at": None,
			"delivered_at": None,
			"read_at": None,
			"last_attempt_at": None,
		}
	)
	copy.insert(ignore_permissions=True)
	dispatcher.enqueue_send(copy.name)
	return copy.name
