import frappe
from frappe.tests import IntegrationTestCase

from grid_erp.grid_whatsapp.services import condition_engine
from grid_erp.grid_whatsapp.services.errors import ConditionError, InvalidRecipientError
from grid_erp.grid_whatsapp.tests import fixtures
from grid_erp.grid_whatsapp.utils.phone import normalize


class TestPhoneNormalization(IntegrationTestCase):
	def test_separators_removed(self):
		self.assertEqual(normalize("+91 (98765) 43-210"), "+919876543210")

	def test_default_country_code_added(self):
		self.assertEqual(normalize("98765 43210", "91"), "+919876543210")
		self.assertEqual(normalize("098765 43210", "91"), "+919876543210")
		self.assertEqual(normalize("050 123 4567", "971"), "+971501234567")

	def test_international_numbers_kept(self):
		self.assertEqual(normalize("+44 20 7946 0958", "91"), "+442079460958")
		self.assertEqual(normalize("0044 20 7946 0958", "91"), "+442079460958")
		self.assertEqual(normalize("919876543210", "91"), "+919876543210")

	def test_national_number_starting_with_code_digits(self):
		# A 10-digit Indian number beginning with 91 is not mistaken for +91.
		self.assertEqual(normalize("9123456789", "91"), "+919123456789")

	def test_invalid_numbers(self):
		for bad in ("", "abc", "12345", "+12345678901234567"):
			with self.assertRaises(InvalidRecipientError):
				normalize(bad, "91")
		with self.assertRaises(InvalidRecipientError):
			normalize("9876543210")  # no country code anywhere


class TestConditionEngine(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_test_doctypes()

	def order(self, **values):
		return frappe.get_doc(
			{
				"doctype": fixtures.ORDER,
				"grand_total": 600000,
				"status": "Approved",
				"company_name": "ABC",
				"docstatus": 1,
				"transaction_date": "2026-05-01",
				"items": [{"item_code": "A"}, {"item_code": "B"}],
				**values,
			}
		)

	def check(self, expression, doc=None, previous=None):
		return condition_engine.evaluate(expression, doc or self.order(), previous=previous)

	def test_comparisons_and_logic(self):
		self.assertTrue(self.check("docstatus == 1"))
		self.assertTrue(self.check('status == "Approved" and company_name == "ABC"'))
		self.assertTrue(self.check("grand_total > 500000"))
		self.assertFalse(self.check("grand_total > 500000 and not docstatus == 1"))
		self.assertTrue(self.check("100000 < grand_total <= 600000"))
		self.assertTrue(self.check('status in ["Approved", "Rejected"]'))
		self.assertTrue(self.check('"B" in items.item_code'))
		self.assertTrue(self.check("grand_total - 100000 >= 500000"))
		self.assertTrue(self.check('transaction_date >= "2026-04-01"'))

	def test_missing_values_are_none(self):
		self.assertTrue(self.check("supplier is None"))
		self.assertFalse(self.check("supplier.supplier_name == 'X'"))
		self.assertFalse(self.check("grand_total > supplier"))

	def test_previous_values(self):
		before = self.order(status="Draft")
		self.assertTrue(self.check('previous.status == "Draft" and status == "Approved"', previous=before))
		self.assertTrue(self.check("previous.status is None"))

	def test_unsafe_expressions_rejected(self):
		for expression in (
			"__import__('os').system('id')",
			"open('/etc/passwd')",
			"grand_total.__class__",
			"[x for x in items]",
			"items[0]",
			"lambda: 1",
			"frappe.db.sql('select 1')",
		):
			with self.assertRaises(ConditionError, msg=expression):
				self.check(expression)

	def test_validation_checks_fields(self):
		condition_engine.validate("grand_total > 10 and supplier.supplier_name == 'X'", fixtures.ORDER)
		with self.assertRaises(ConditionError):
			condition_engine.validate("no_such_field == 1", fixtures.ORDER)
		with self.assertRaises(ConditionError):
			condition_engine.validate("grand_total >", fixtures.ORDER)
