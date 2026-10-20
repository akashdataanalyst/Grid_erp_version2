"""AI Settings: the single Allowed Role becomes the first row of Allowed Roles.

Written directly (no settings.save) so the settings' own validation cannot fail the migration."""

import frappe


def execute():
	role = frappe.db.get_single_value("AI Settings", "allowed_role")
	if not role:
		return
	existing = frappe.get_all(
		"AI Allowed Role", filters={"parent": "AI Settings", "parenttype": "AI Settings"}, pluck="role"
	)
	if role not in existing:
		row = frappe.get_doc(
			{
				"doctype": "AI Allowed Role",
				"parent": "AI Settings",
				"parenttype": "AI Settings",
				"parentfield": "allowed_roles",
				"idx": len(existing) + 1,
				"role": role,
			}
		)
		row.db_insert()
	frappe.db.set_single_value("AI Settings", "allowed_role", None)
	frappe.clear_document_cache("AI Settings", "AI Settings")
