"""Permission engine. ERPNext / Frappe permissions are the source of truth.

Layers, all checked *before* any ERP data is read:

1. Who may use the assistant at all (logged-in System User, optionally one of the Allowed Roles).
2. DocType read permission (`frappe.has_permission`).
3. Field-level permission (permlevel) for every field the query touches.
4. Explicit record filters (e.g. sales_person = "Rahul") against User Permissions and
   record-level read permission, so a request for someone else's data is *denied*,
   not silently answered with zero.
5. The query itself always runs through `frappe.get_list` *without* ignore_permissions, so
   role permissions, User Permissions, permission query conditions, if-owner rules and
   sharing are applied by Frappe in the SQL itself. Restricted rows are never fetched.

The user is always `frappe.session.user`; nothing supplied by the client or the LLM is trusted.
"""

import frappe

from grid_erp.grid_ai.exceptions import AssistantDisabledError, PermissionDeniedError
from grid_erp.grid_ai.tools.discovery import STANDARD_FIELDS

EQUALITY_OPERATORS = ("=", "in")


def current_user() -> str:
	return frappe.session.user


def can_use_assistant(user: str | None = None) -> bool:
	"""Whether the user may open the assistant."""
	user = user or current_user()
	if not user or user == "Guest":
		return False
	if frappe.get_cached_value("User", user, "user_type") != "System User":
		return False
	return has_allowed_role(get_allowed_roles(), frappe.get_roles(user))


def get_allowed_roles() -> list[str]:
	settings = frappe.get_cached_doc("AI Settings")
	return [row.role for row in settings.get("allowed_roles") or [] if row.role]


def has_allowed_role(allowed_roles: list[str], user_roles: list[str]) -> bool:
	"""No roles configured: every System User. Otherwise any one of the roles is enough."""
	return not allowed_roles or bool(set(allowed_roles) & set(user_roles))


def ensure_can_use_assistant(user: str | None = None):
	if not can_use_assistant(user):
		raise PermissionDeniedError("Aapko AI Assistant use karne ki permission nahi hai.")
	if not frappe.db.get_single_value("AI Settings", "enabled"):
		raise AssistantDisabledError()


def can_read_doctype(doctype: str, user: str) -> bool:
	try:
		return bool(frappe.has_permission(doctype, "read", user=user))
	except Exception:
		return False


def ensure_doctype_access(doctype: str, user: str):
	if not can_read_doctype(doctype, user):
		raise PermissionDeniedError(detail=f"no read permission on {doctype}")


def ensure_fields_permitted(doctype: str, fieldnames: set[str], user: str):
	"""Every referenced field must be readable at the user's permlevel. For a child table field
	(`items.qty`) both the table field and the field inside the child table are checked."""
	meta = frappe.get_meta(doctype)
	permitted = set(meta.get_permitted_fieldnames(user=user)) | set(STANDARD_FIELDS)
	child_permitted = {}
	blocked = set()
	for fieldname in fieldnames:
		if "." not in fieldname:
			if fieldname not in permitted:
				blocked.add(fieldname)
			continue
		table_fieldname, _, child_fieldname = fieldname.partition(".")
		table_df = meta.get_field(table_fieldname)
		# Table fields are not in get_permitted_fieldnames: check their permlevel directly.
		if (
			not table_df
			or table_df.fieldtype != "Table"
			or (table_df.permlevel or 0) not in meta.get_permlevel_access("read", user=user)
		):
			blocked.add(fieldname)
			continue
		if table_df.options not in child_permitted:
			child_permitted[table_df.options] = set(
				frappe.get_meta(table_df.options).get_permitted_fieldnames(parenttype=doctype, user=user)
			)
		if child_fieldname not in child_permitted[table_df.options]:
			blocked.add(fieldname)
	if blocked:
		raise PermissionDeniedError(detail=f"field permission denied on {doctype}: {sorted(blocked)}")


def ensure_filter_values_permitted(doctype: str, filters: list[list], user: str):
	"""Deny explicit filters that target records the user is not allowed to see.

	Example: Salesperson A asks "Rahul ki sales kitni hai?" -> the filter sales_person = Rahul is
	checked against A's User Permissions before any query runs, and the request is denied.
	"""
	meta = frappe.get_meta(doctype)
	user_permissions = frappe.permissions.get_user_permissions(user) or {}
	owner_only = _has_owner_only_read(meta, user)

	for fieldname, operator, value in filters:
		if operator not in EQUALITY_OPERATORS:
			continue
		values = value if isinstance(value, list) else [value]

		if fieldname == "owner":
			if owner_only and any(v != user for v in values):
				raise PermissionDeniedError(detail=f"{doctype}: owner-only access")
			continue

		df = _get_df(meta, fieldname)
		if not df or df.fieldtype != "Link" or not df.options:
			continue
		link_doctype = df.options

		allowed = _allowed_user_permission_values(user_permissions, link_doctype, doctype)
		if allowed is not None and any(v not in allowed for v in values):
			raise PermissionDeniedError(detail=f"{doctype}.{fieldname}: value outside user permissions")

		# Record-level check, only when the user has some access to the linked DocType at all
		# (lack of any access to a master DocType is not a reason to block the parent query).
		if can_read_doctype(link_doctype, user):
			for v in values:
				if (
					isinstance(v, str)
					and frappe.db.exists(link_doctype, v)
					and not frappe.has_permission(link_doctype, "read", doc=v, user=user)
				):
					raise PermissionDeniedError(detail=f"{doctype}.{fieldname}: linked record not readable")


def _get_df(meta, fieldname: str):
	"""DocField of a parent field or of a child table field (`items.item_code`)."""
	if "." not in fieldname:
		return meta.get_field(fieldname)
	table_fieldname, _, child_fieldname = fieldname.partition(".")
	table_df = meta.get_field(table_fieldname)
	if not table_df or not table_df.options:
		return None
	return frappe.get_meta(table_df.options).get_field(child_fieldname)


def _allowed_user_permission_values(user_permissions: dict, link_doctype: str, doctype: str) -> set | None:
	"""Values of `link_doctype` the user is restricted to for `doctype`, or None if unrestricted."""
	entries = user_permissions.get(link_doctype)
	if not entries:
		return None
	allowed = {
		e.get("doc")
		for e in entries
		if not e.get("applicable_for") or e.get("applicable_for") == doctype
	}
	return allowed or None


def _has_owner_only_read(meta, user: str) -> bool:
	"""True when the user's read access comes only from "If Owner" role permissions.

	Frappe marks this by putting the permission type as a key in `if_owner`
	(its value is only 1 when evaluated for a specific owned document).
	"""
	try:
		perms = frappe.permissions.get_role_permissions(meta, user=user)
	except Exception:
		return False
	return "read" in (perms.get("if_owner") or {})
