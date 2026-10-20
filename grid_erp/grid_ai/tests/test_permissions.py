"""Permission helpers and field discovery. Read-only: uses DocType metadata and the
Administrator / Guest users only, nothing is created."""

import unittest

import frappe

from grid_erp.grid_ai.exceptions import PermissionDeniedError
from grid_erp.grid_ai.doctype.ai_settings.ai_settings import AISettings
from grid_erp.grid_ai.tools import discovery, permissions


class TestAllowedRoles(unittest.TestCase):
	def test_no_roles_allows_everyone(self):
		self.assertTrue(permissions.has_allowed_role([], ["Employee"]))

	def test_any_one_role_is_enough(self):
		self.assertTrue(permissions.has_allowed_role(["Accounts User", "HR User"], ["Employee", "HR User"]))
		self.assertFalse(permissions.has_allowed_role(["Accounts User", "HR User"], ["Employee"]))

	def test_guest_never(self):
		self.assertFalse(permissions.can_use_assistant("Guest"))


@unittest.skipUnless(frappe.db and frappe.db.exists("DocType", "Sales Invoice"), "needs ERPNext")
class TestLineItemFields(unittest.TestCase):
	def test_child_field_info(self):
		meta = frappe.get_meta("Sales Invoice")
		info = discovery.get_field_info(meta, "items.item_code")
		self.assertEqual(info["child_doctype"], "Sales Invoice Item")
		self.assertEqual(info["fieldtype"], "Link")
		self.assertIsNone(discovery.get_field_info(meta, "customer.customer_name"))  # not a table
		self.assertIsNone(discovery.get_field_info(meta, "items.no_such_field"))
		self.assertIsNone(discovery.get_field_info(meta, "items.item_code.x"))

	def test_selectable_fields_include_lines_but_suggestions_do_not(self):
		names = {f["fieldname"] for f in discovery.get_selectable_fields("Sales Invoice")}
		self.assertIn("items.item_code", names)
		self.assertFalse(any("." in f["fieldname"] for f in discovery.suggest_fields("Sales Invoice")))

	def test_field_permissions_cover_line_fields(self):
		permissions.ensure_fields_permitted("Sales Invoice", {"customer", "items.item_code"}, "Administrator")
		with self.assertRaises(PermissionDeniedError):
			permissions.ensure_fields_permitted("Sales Invoice", {"items.no_such_field"}, "Administrator")
		with self.assertRaises(PermissionDeniedError):
			permissions.ensure_fields_permitted("Sales Invoice", {"no_table.item_code"}, "Administrator")


class TestModelValidation(unittest.TestCase):
	def check(self, provider, model):
		doc = frappe._dict(provider=provider, model=model)
		AISettings.validate_model(doc)
		return doc.model

	def test_other_providers_model_is_rejected(self):
		for provider, model in (("OpenAI", "openrouter/free"), ("Claude", "gpt-4o"), ("OpenAI", "claude-opus-5-5")):
			with self.assertRaises(frappe.ValidationError):
				self.check(provider, model)

	def test_valid_models(self):
		self.assertEqual(self.check("OpenRouter", "openrouter/free"), "openrouter/free")
		self.assertEqual(self.check("Gemini", "models/gemini-2.5-flash"), "gemini-2.5-flash")
		self.assertEqual(self.check("Ollama", "llama3.1"), "llama3.1")
