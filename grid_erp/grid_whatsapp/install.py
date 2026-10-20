import frappe

MANAGER_ROLE = "WhatsApp Manager"


def ensure_manager_role():
	"""Role for people who maintain templates and rules without full System Manager rights."""
	if frappe.db.exists("Role", MANAGER_ROLE):
		return
	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": MANAGER_ROLE,
			"desk_access": 1,
		}
	).insert(ignore_permissions=True)
