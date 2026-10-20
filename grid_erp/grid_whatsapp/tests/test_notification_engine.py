import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from grid_erp.grid_whatsapp.services import dispatcher, notification_engine, recipient_resolver, template_renderer
from grid_erp.grid_whatsapp.services.errors import (
	AuthenticationError,
	ConfigurationError,
	ProviderTimeoutError,
	VariableError,
)
from grid_erp.grid_whatsapp.services.field_resolver import Resolver
from grid_erp.grid_whatsapp.tests import fixtures
from grid_erp.grid_whatsapp.tests.fixtures import FakeProvider

def send_in_transaction(log_name):
	# Stands in for the background job; no commit, so the test rollback undoes it.
	dispatcher.send_log(log_name, commit=False)


SUPPLIER_ROW = {"label": "Supplier", "recipient_type": "Document Link", "source_field": "supplier", "required": 1}
EMPLOYEE_ROW = {"label": "Buyer", "recipient_type": "Employee", "source_field": "employee", "required": 1}


class EngineTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures.ensure_test_doctypes()

	def setUp(self):
		super().setUp()
		# Each test starts clean: undo its records and forget rules cached from them.
		self.addCleanup(frappe.db.rollback)
		self.addCleanup(notification_engine.clear_rule_cache)
		self.addCleanup(frappe.clear_document_cache, "WhatsApp Settings", "WhatsApp Settings")
		fixtures.setup_settings()
		fixtures.make_instance(active=1)
		FakeProvider.reset()
		notification_engine.clear_rule_cache()
		patcher = patch.object(dispatcher, "get_provider", lambda instance: FakeProvider(instance))
		patcher.start()
		self.addCleanup(patcher.stop)

	def template(self, body="PO {{po}} for {{supplier_name}}", variables=None, **kwargs):
		variables = variables or [
			{"variable_name": "po", "source_type": "Document Field", "source_field": "name", "required": 1},
			{"variable_name": "supplier_name", "source_type": "Document Field", "source_field": "supplier.supplier_name"},
		]
		return fixtures.make_template(variables, body, **kwargs).name


class TestTemplates(EngineTestCase):
	def test_variable_resolution_and_rendering(self):
		supplier = fixtures.make_supplier(name="WA Render Supplier")
		order = fixtures.make_order(supplier=supplier.name)
		template = fixtures.make_template(
			[
				{"variable_name": "po", "source_type": "Document Field", "source_field": "name"},
				{"variable_name": "supplier_name", "source_type": "Document Field", "source_field": "supplier.supplier_name"},
				{"variable_name": "total", "source_type": "Document Field", "source_field": "grand_total"},
				{"variable_name": "raw_total", "source_type": "Document Field", "source_field": "grand_total", "raw_value": 1},
				{"variable_name": "items", "source_type": "Document Field", "source_field": "items.item_code"},
				{"variable_name": "first", "source_type": "Document Field", "source_field": "items.0.item_code"},
				{"variable_name": "greeting", "source_type": "Static Value", "default_value": "Hello"},
				{"variable_name": "doc", "source_type": "System Value", "source_field": "document_type"},
				{"variable_name": "missing", "source_type": "Document Field", "source_field": "buyer_mobile", "default_value": "n/a"},
			],
			"{{greeting}} {{ supplier_name }}: {{doc}} {{po}} = {{total}} ({{raw_total}}) [{{items}}] {{first}} {{missing}}",
		)
		message = template_renderer.render(template.name, order)
		self.assertEqual(message.variables["supplier_name"], "WA Render Supplier")
		self.assertEqual(message.variables["items"], "ITEM-A, ITEM-B")
		self.assertEqual(message.variables["first"], "ITEM-A")
		self.assertEqual(message.variables["raw_total"], "125000.5")
		self.assertEqual(message.variables["missing"], "n/a")
		self.assertEqual(list(message.variables), [row.variable_name for row in template.variables])
		self.assertTrue(message.body.startswith("Hello WA Render Supplier: WA Test Order " + order.name))
		self.assertIn("[ITEM-A, ITEM-B] ITEM-A n/a", message.body)

	def test_linked_document_paths(self):
		supplier = fixtures.make_supplier(contact_mobile="9876500011")
		order = fixtures.make_order(supplier=supplier.name)
		resolver = Resolver(order)
		self.assertEqual(resolver.get_value("supplier.supplier_name"), supplier.supplier_name)
		self.assertEqual(resolver.get_value("supplier.supplier_primary_contact.mobile_no"), "9876500011")
		self.assertEqual(resolver.get_value("items.qty"), [2, 3])
		self.assertIsNone(resolver.get_value("employee.first_name"))  # empty link

	def test_required_variable_missing(self):
		order = fixtures.make_order()
		template = fixtures.make_template(
			[{"variable_name": "mobile", "source_type": "Document Field", "source_field": "buyer_mobile", "required": 1}],
			"Call {{mobile}}",
		)
		with self.assertRaises(VariableError):
			template_renderer.render(template.name, order)

	def test_template_validation(self):
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_template([], "Hi {{undefined_variable}}")
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_template(
				[{"variable_name": "x", "source_type": "Document Field", "source_field": "nope"}],
				"{{x}}",
				reference_doctype=fixtures.ORDER,
			)
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_template(
				[{"variable_name": "key", "source_type": "Document Field", "source_field": "api_key"}],
				"{{key}}",
				reference_doctype="WhatsApp Instance",
			)


class TestRecipients(EngineTestCase):
	def resolve(self, rows, order, **rule_values):
		rule = frappe._dict(recipients=[frappe._dict(enabled=1, idx=i + 1, **row) for i, row in enumerate(rows)], **rule_values)
		return recipient_resolver.resolve(rule, order, country_code="91")

	def test_supplier_primary_contact_mobile(self):
		supplier = fixtures.make_supplier(contact_mobile="98765-00021")
		order = fixtures.make_order(supplier=supplier.name)
		recipients, failures = self.resolve([SUPPLIER_ROW], order)
		self.assertEqual([r.number for r in recipients], ["+919876500021"])
		self.assertFalse(failures)

	def test_supplier_fallbacks(self):
		# No primary contact: Auto falls back to a Contact linked to the supplier.
		supplier = fixtures.make_supplier(contact_mobile="9876500031", primary=False)
		order = fixtures.make_order(supplier=supplier.name)
		recipients, _ = self.resolve([SUPPLIER_ROW], order)
		self.assertEqual(recipients[0].number, "+919876500031")
		# Primary Contact only: the same supplier resolves to nothing.
		recipients, failures = self.resolve([{**SUPPLIER_ROW, "contact_resolution": "Primary Contact"}], order)
		self.assertFalse(recipients)
		self.assertIn("No mobile number", failures[0].error)

	def test_employee_mobile(self):
		user = fixtures.make_user()
		employee = fixtures.make_employee(mobile="9876500041", user=user.name)
		order = fixtures.make_order(employee=employee.name)
		recipients, _ = self.resolve([EMPLOYEE_ROW], order)
		self.assertEqual(recipients[0].number, "+919876500041")
		# Employee found from a User id (the document owner set to the employee's user).
		order.db_set("owner", user.name)
		recipients, _ = self.resolve([{"recipient_type": "Employee", "source_field": "owner", "label": "Owner"}], order)
		self.assertEqual(recipients[0].number, "+919876500041")

	def test_role_recipients_with_department_filter(self):
		role = frappe.get_doc({"doctype": "Role", "role_name": f"WA Role {frappe.generate_hash(length=5)}"}).insert()
		user_a = fixtures.make_user(mobile="9876500051", roles=[role.name])
		user_b = fixtures.make_user(mobile="9876500052", roles=[role.name])
		fixtures.make_employee(user=user_a.name, department="Purchase")
		fixtures.make_employee(user=user_b.name, department="Sales")
		order = fixtures.make_order(company_name="Purchase")
		row = {"recipient_type": "Role", "source": role.name, "label": "Managers"}
		recipients, _ = self.resolve([row], order)
		self.assertEqual(sorted(r.number for r in recipients), ["+919876500051", "+919876500052"])

		recipients, _ = self.resolve(
			[{**row, "record_filter_field": "department", "document_filter_field": "company_name"}], order
		)
		self.assertEqual([r.number for r in recipients], ["+919876500051"])

	def test_multiple_recipients_and_duplicates(self):
		supplier = fixtures.make_supplier(contact_mobile="9876500061")
		employee = fixtures.make_employee(mobile="+91 98765 00062")
		order = fixtures.make_order(supplier=supplier.name, employee=employee.name, buyer_mobile="9876500062")
		rows = [
			SUPPLIER_ROW,
			EMPLOYEE_ROW,
			{"label": "Buyer Mobile", "recipient_type": "Document Field", "source_field": "buyer_mobile"},
			{"label": "Office", "recipient_type": "Fixed Number", "fixed_number": "+44 20 7946 0958"},
		]
		recipients, _ = self.resolve(rows, order)
		self.assertEqual([r.number for r in recipients], ["+919876500061", "+919876500062", "+442079460958"])
		self.assertEqual(recipients[1].label, "Buyer + Buyer Mobile")

		recipients, _ = self.resolve(rows, order, allow_duplicate_recipient=1)
		self.assertEqual(len(recipients), 4)

	def test_missing_and_invalid_mobile(self):
		supplier = fixtures.make_supplier()  # no contact, no mobile
		order = fixtures.make_order(supplier=supplier.name, buyer_mobile="12-34")
		recipients, failures = self.resolve(
			[
				SUPPLIER_ROW,
				{**SUPPLIER_ROW, "label": "Optional", "required": 0},
				{"label": "Buyer", "recipient_type": "Document Field", "source_field": "buyer_mobile"},
			],
			order,
		)
		self.assertFalse(recipients)
		self.assertEqual(len(failures), 2)
		self.assertIn("No mobile number found for Supplier", failures[0].error)
		self.assertIn("Invalid mobile number", failures[1].error)


class TestRules(EngineTestCase):
	def supplier_rule(self, **kwargs):
		return fixtures.make_rule(self.template(), [SUPPLIER_ROW], **kwargs)

	def test_rule_matching_by_event(self):
		self.supplier_rule(event="After Submit")
		supplier = fixtures.make_supplier(contact_mobile="9876500071")
		order = fixtures.make_order(supplier=supplier.name)
		self.assertFalse(fixtures.logs_for(order))  # saved, not submitted
		order.submit()
		logs = fixtures.logs_for(order)
		self.assertEqual([(l.status, l.recipient) for l in logs], [("Sent", "+919876500071")])
		self.assertIn(order.name, logs[0].message)
		self.assertEqual(FakeProvider.sent[0].template_name, "test_template")
		self.assertEqual(FakeProvider.sent[0].parameters, [order.name, supplier.supplier_name])

	def test_condition_and_value_change(self):
		self.supplier_rule(
			event="On Value Change",
			value_changed_field="status",
			condition_type="Expression",
			condition_expression='status == "Approved"',
		)
		supplier = fixtures.make_supplier(contact_mobile="9876500081")
		order = fixtures.make_order(supplier=supplier.name)  # created as Draft: condition false
		self.assertFalse(fixtures.logs_for(order))
		order.status = "Approved"
		order.save()
		self.assertEqual(len(fixtures.logs_for(order)), 1)
		order.grand_total = 1
		order.save()  # status unchanged
		self.assertEqual(len(FakeProvider.sent), 1)

	def test_disabled_rule(self):
		self.supplier_rule(enabled=0)
		supplier = fixtures.make_supplier(contact_mobile="9876500091")
		order = fixtures.make_order(supplier=supplier.name, submit=True)
		self.assertFalse(fixtures.logs_for(order))
		self.assertFalse(FakeProvider.sent)

	def test_no_rules_no_queries(self):
		notification_engine.clear_rule_cache()
		notification_engine.rules_for("ToDo")  # warm the cache
		todo = frappe.get_doc({"doctype": "ToDo", "description": "x"})
		with self.assertQueryCount(0):
			notification_engine.handle_doc_event(todo, "on_update")

	def test_duplicate_notification_prevented(self):
		self.supplier_rule(event="On Update")
		supplier = fixtures.make_supplier(contact_mobile="9876500101")
		order = fixtures.make_order(supplier=supplier.name)
		order.grand_total = 2
		order.save()
		order.submit()
		self.assertEqual(len(fixtures.logs_for(order)), 1)
		self.assertEqual(len(FakeProvider.sent), 1)

	def test_send_on_every_trigger(self):
		self.supplier_rule(event="On Update", allow_duplicate=1)
		supplier = fixtures.make_supplier(contact_mobile="9876500111")
		order = fixtures.make_order(supplier=supplier.name)
		order.grand_total = 2
		order.save()
		self.assertEqual(len(FakeProvider.sent), 2)

	def test_disabled_instance(self):
		self.supplier_rule()
		fixtures.make_instance(active=0)
		supplier = fixtures.make_supplier(contact_mobile="9876500121")
		order = fixtures.make_order(supplier=supplier.name, submit=True)
		logs = fixtures.logs_for(order)
		self.assertEqual(logs[0].status, "Failed")
		self.assertIn("inactive", logs[0].error)
		self.assertFalse(FakeProvider.sent)

	def test_missing_mobile_logged(self):
		self.supplier_rule()
		order = fixtures.make_order(supplier=fixtures.make_supplier().name, submit=True)
		logs = fixtures.logs_for(order)
		self.assertEqual([l.status for l in logs], ["Failed"])
		self.assertIn("No mobile number found", logs[0].error)

	def test_test_mode(self):
		fixtures.setup_settings(test_mode=1)
		self.supplier_rule()
		supplier = fixtures.make_supplier(contact_mobile="9876500131")
		order = fixtures.make_order(supplier=supplier.name, submit=True)
		self.assertEqual(fixtures.logs_for(order)[0].status, "Sent")
		self.assertFalse(FakeProvider.sent)

	def test_rule_validation(self):
		template = self.template()
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_rule(template, [SUPPLIER_ROW], condition_type="Expression", condition_expression="nope == 1")
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_rule(template, [SUPPLIER_ROW], document_type="ToDo")  # ToDo is not submittable
		with self.assertRaises(frappe.ValidationError):
			fixtures.make_rule(template, [{**SUPPLIER_ROW, "source_field": "grand_total"}])


class TestDelivery(EngineTestCase):
	def submitted_order(self, mobile):
		fixtures.make_rule(self.template(), [SUPPLIER_ROW])
		supplier = fixtures.make_supplier(contact_mobile=mobile)
		return fixtures.make_order(supplier=supplier.name, submit=True)

	def test_permanent_api_failure_not_retried(self):
		FakeProvider.error = AuthenticationError("Rejected key test-secret-key-123")
		order = self.submitted_order("9876500141")
		log = fixtures.logs_for(order)[0]
		self.assertEqual(log.status, "Failed")
		self.assertEqual(log.retry_count, 0)
		self.assertIn("Rejected key", log.error)
		self.assertNotIn("test-secret-key-123", log.error)  # instance API key is masked

	def test_retry_then_fail(self):
		FakeProvider.error = ProviderTimeoutError("timed out")
		order = self.submitted_order("9876500151")
		log = fixtures.logs_for(order)[0]
		self.assertEqual((log.status, log.retry_count), ("Queued", 1))
		self.assertIsNotNone(log.next_retry_at)

		with patch.object(dispatcher, "enqueue_send", send_in_transaction):
			dispatcher.process_due_retries()  # second attempt
			log = fixtures.logs_for(order)[0]
			self.assertEqual((log.status, log.retry_count), ("Queued", 2))
			dispatcher.process_due_retries()  # third attempt: max_retries (2) used up
		log = fixtures.logs_for(order)[0]
		self.assertEqual((log.status, log.retry_count), ("Failed", 2))
		self.assertIn("timed out", log.error)

	def test_retry_succeeds(self):
		FakeProvider.error = ProviderTimeoutError("timed out")
		order = self.submitted_order("9876500161")
		FakeProvider.error = None
		with patch.object(dispatcher, "enqueue_send", send_in_transaction):
			dispatcher.process_due_retries()
		log = fixtures.logs_for(order)[0]
		self.assertEqual(log.status, "Sent")
		self.assertEqual(json.loads(log.response)["token"], "********")  # secret-looking keys masked

	def test_resend(self):
		order = self.submitted_order("9876500171")
		log = fixtures.logs_for(order)[0]
		with patch.object(dispatcher, "enqueue_send", send_in_transaction):
			new_log = notification_engine.resend(log.name)
		self.assertNotEqual(new_log, log.name)
		self.assertEqual(len(FakeProvider.sent), 2)

	def test_delivery_status_updates(self):
		order = self.submitted_order("9876500181")
		log = fixtures.logs_for(order)[0]
		message_id = frappe.db.get_value(dispatcher.LOG, log.name, "message_id")
		dispatcher.update_status(message_id, "Read")
		dispatcher.update_status(message_id, "Delivered")  # older receipt arriving late
		status, read_at, delivered_at = frappe.db.get_value(
			dispatcher.LOG, log.name, ["status", "read_at", "delivered_at"]
		)
		self.assertEqual(status, "Read")
		self.assertTrue(read_at and delivered_at)

	def test_evalidation_provider_not_configured(self):
		from grid_erp.grid_whatsapp.services.providers.evalidation import EValidationProvider
		from grid_erp.grid_whatsapp.services.whatsapp_provider import OutgoingMessage, load_instance

		provider = EValidationProvider(load_instance(fixtures.INSTANCE))
		with self.assertRaises(ConfigurationError):
			provider.send(OutgoingMessage(recipient="+919876500000", body="x", variables={}))
