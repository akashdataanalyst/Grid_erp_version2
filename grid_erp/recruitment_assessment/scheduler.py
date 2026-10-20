from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from .notifications import send_assessment_email
from .report_utils import assessment_assignment_open_filters


def expire_pending_assignments():
	assignments = frappe.get_all(
		"Assessment Assignment",
		filters=assessment_assignment_open_filters(),
		fields=["name", "valid_till"],
	)
	for row in assignments:
		if row.valid_till and row.valid_till < now_datetime():
			doc = frappe.get_doc("Assessment Assignment", row.name)
			doc.portal_status = "Expired"
			doc.save(ignore_permissions=True)


def send_assessment_reminders():
	# Simplified reminder job: send a reminder when expiry is near and submission is pending.
	assignments = frappe.get_all(
		"Assessment Assignment",
		filters={**assessment_assignment_open_filters(), "portal_status": ["in", ["Assigned", "Pending"]]},
		fields=["*"],
	)
	for assignment in assignments:
		send_assessment_email(frappe.get_doc("Assessment Assignment", assignment.name), "Assessment Reminder", "assessment_reminder")


def process_due_notifications():
	frappe.db.commit()


def recompute_results():
	attempts = frappe.get_all("Assessment Attempt", filters={"status": "Submitted"}, fields=["name"])
	for row in attempts:
		doc = frappe.get_doc("Assessment Attempt", row.name)
		doc.db_set("status", "Submitted", update_modified=False)
