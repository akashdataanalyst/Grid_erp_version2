from __future__ import annotations

import frappe
from frappe.utils import now_datetime


def _append_attempt_row(attempt: str, child_doctype: str, parentfield: str, values: dict):
	"""Insert one log row under an attempt without re-saving the whole (busy) attempt document."""
	idx = frappe.db.sql(
		f"select ifnull(max(idx), 0) from `tab{child_doctype}` where parent=%s and parenttype='Assessment Attempt'",
		attempt,
	)[0][0]
	row = frappe.get_doc(
		{
			"doctype": child_doctype,
			"parent": attempt,
			"parenttype": "Assessment Attempt",
			"parentfield": parentfield,
			"idx": idx + 1,
			**values,
		}
	)
	row.db_insert()
	return row


def log_assessment_audit(
	attempt: str,
	event_type: str,
	remarks: str | None = None,
	payload: dict | None = None,
):
	return _append_attempt_row(
		attempt,
		"Assessment Audit Log",
		"audit_logs",
		{
			"event_type": event_type,
			"timestamp": now_datetime(),
			"user": frappe.session.user if getattr(frappe, "session", None) else "Guest",
			"ip_address": getattr(frappe.local, "request_ip", None),
			"remarks": remarks,
			"payload_json": frappe.as_json(payload or {}),
		},
	)


def log_security_event(
	assignment: str,
	attempt: str | None,
	event_type: str,
	payload: dict | None = None,
	severity: str = "Medium",
	remarks: str | None = None,
	screenshot: str | None = None,
):
	timestamp = now_datetime()
	request = getattr(frappe.local, "request", None)
	values = {
		"assignment": assignment,
		"violation_type": event_type,
		"event_type": event_type,
		"timestamp": timestamp,
		"event_timestamp": timestamp,
		"severity": severity or "Medium",
		"remarks": remarks,
		"screenshot": screenshot,
		"user_agent": frappe.get_request_header("User-Agent") if request else None,
		"ip_address": getattr(frappe.local, "request_ip", None),
		"payload_json": frappe.as_json(payload or {}),
	}
	if attempt:
		row = _append_attempt_row(attempt, "Assessment Violation", "violations", {"attempt": attempt, **values})
		count = frappe.db.count("Assessment Violation", {"parent": attempt, "parenttype": "Assessment Attempt"})
		frappe.db.set_value("Assessment Attempt", attempt, "violation_count", count, update_modified=False)
		return row

	doc = frappe.new_doc("Assessment Violation")
	doc.update(values)
	return doc


def log_notification(assignment: str, notification_type: str, channel: str, payload: dict | None = None):
	doc = frappe.new_doc("Assessment Notification")
	doc.assignment = assignment
	doc.notification_type = notification_type
	doc.channel = channel
	doc.payload_json = frappe.as_json(payload or {})
	doc.sent_on = now_datetime()
	doc.insert(ignore_permissions=True)
	return doc
