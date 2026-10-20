"""Test records and a fake provider. Everything is created through the same UI
DocTypes an administrator uses; nothing here is used outside tests."""

import frappe

from grid_erp.grid_whatsapp.services import notification_engine
from grid_erp.grid_whatsapp.services.whatsapp_provider import ProviderResult, WhatsAppProvider

ORDER = "WA Test Order"
ORDER_ITEM = "WA Test Order Item"
INSTANCE = "WA Test Instance"
COUNTRY_CODE = "91"


def ensure_test_doctypes():
	"""A submittable custom DocType with links and a child table, standing in for
	any business document (Purchase Order, Quality Inspection, ...)."""
	if not frappe.db.exists("DocType", ORDER_ITEM):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": ORDER_ITEM,
				"module": "Grid WhatsApp",
				"custom": 1,
				"istable": 1,
				"fields": [
					{"fieldname": "item_code", "fieldtype": "Data", "label": "Item Code"},
					{"fieldname": "qty", "fieldtype": "Float", "label": "Qty"},
				],
			}
		).insert()
	if not frappe.db.exists("DocType", ORDER):
		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": ORDER,
				"module": "Grid WhatsApp",
				"custom": 1,
				"is_submittable": 1,
				"autoname": "hash",
				"fields": [
					{"fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "label": "Supplier"},
					{"fieldname": "employee", "fieldtype": "Link", "options": "Employee", "label": "Employee"},
					{"fieldname": "buyer_mobile", "fieldtype": "Data", "label": "Buyer Mobile"},
					{"fieldname": "grand_total", "fieldtype": "Float", "label": "Grand Total"},
					{"fieldname": "transaction_date", "fieldtype": "Date", "label": "Date"},
					{
						"fieldname": "status",
						"fieldtype": "Select",
						"options": "Draft\nApproved\nRejected",
						"default": "Draft",
						"label": "Status",
					},
					{"fieldname": "company_name", "fieldtype": "Data", "label": "Company Name"},
					{"fieldname": "items", "fieldtype": "Table", "options": ORDER_ITEM, "label": "Items"},
				],
				"permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1}],
			}
		).insert()
	frappe.db.commit()


def setup_settings(**overrides):
	settings = frappe.get_single("WhatsApp Settings")
	settings.update(
		{
			"default_country_code": COUNTRY_CODE,
			"queue_enabled": 1,
			"test_mode": 0,
			"retry_enabled": 1,
			"max_retries": 2,
			"retry_delay": 0,
			"failure_behavior": "Log Only",
			"allow_manual_resend": 1,
			**overrides,
		}
	)
	settings.save()
	frappe.clear_document_cache("WhatsApp Settings", "WhatsApp Settings")
	return settings


def make_instance(name=INSTANCE, active=1, **kwargs):
	if frappe.db.exists("WhatsApp Instance", name):
		doc = frappe.get_doc("WhatsApp Instance", name)
		doc.update({"active": active, **kwargs})
		doc.save()
		return doc
	return frappe.get_doc(
		{
			"doctype": "WhatsApp Instance",
			"instance_name": name,
			"provider": "eValidation",
			"active": active,
			"api_base_url": "https://whatsapp.example.invalid",
			"api_key": "test-secret-key-123",
			**kwargs,
		}
	).insert()


def make_template(variables, body, name=None, **kwargs):
	return frappe.get_doc(
		{
			"doctype": "WhatsApp Template",
			"template_name": name or f"WA Test Template {frappe.generate_hash(length=6)}",
			"provider_template_name": "test_template",
			"language": "en",
			"message_body": body,
			"variables": variables,
			**kwargs,
		}
	).insert()


def make_rule(template, recipients, event="After Submit", document_type=ORDER, **kwargs):
	rule = frappe.get_doc(
		{
			"doctype": "WhatsApp Notification Rule",
			"rule_name": f"WA Test Rule {frappe.generate_hash(length=6)}",
			"document_type": document_type,
			"event": event,
			"template": template,
			"whatsapp_instance": INSTANCE,
			"recipients": recipients,
			**kwargs,
		}
	).insert()
	notification_engine.clear_rule_cache()
	return rule


def make_supplier(name=None, contact_mobile=None, primary=True, supplier_mobile=None):
	supplier = frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": name or f"WA Supplier {frappe.generate_hash(length=6)}",
			"supplier_type": "Company",
		}
	).insert()
	if contact_mobile:
		contact = make_contact(contact_mobile, "Supplier", supplier.name)
		if primary:
			supplier.db_set("supplier_primary_contact", contact.name)
	if supplier_mobile:
		supplier.db_set("mobile_no", supplier_mobile)
	return supplier


def make_contact(mobile, link_doctype=None, link_name=None):
	contact = frappe.get_doc({"doctype": "Contact", "first_name": f"WA Contact {frappe.generate_hash(length=6)}"})
	contact.append("phone_nos", {"phone": mobile, "is_primary_mobile_no": 1})
	if link_doctype:
		contact.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	contact.insert()
	return contact


def make_employee(mobile=None, user=None, department=None):
	employee = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": f"WA Employee {frappe.generate_hash(length=6)}",
			"status": "Active",
			"cell_number": mobile,
		}
	)
	employee.flags.ignore_mandatory = True
	employee.flags.ignore_links = True
	employee.insert()
	# Set directly: through validation these need a Company, which the test site has none of.
	if user or department:
		employee.db_set({"user_id": user, "department": department})
	return employee


def make_user(mobile=None, roles=()):
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": f"wa-{frappe.generate_hash(length=8)}@example.com",
			"first_name": "WA User",
			"mobile_no": mobile,
			"send_welcome_email": 0,
		}
	)
	for role in roles:
		user.append("roles", {"role": role})
	user.insert()
	return user


def make_order(supplier=None, employee=None, submit=False, **kwargs):
	order = frappe.get_doc(
		{
			"doctype": ORDER,
			"supplier": supplier,
			"employee": employee,
			"grand_total": 125000.5,
			"company_name": "WA Test Co",
			"items": [{"item_code": "ITEM-A", "qty": 2}, {"item_code": "ITEM-B", "qty": 3}],
			**kwargs,
		}
	).insert()
	if submit:
		order.submit()
	return order


def logs_for(doc, **filters):
	return frappe.get_all(
		notification_engine.LOG,
		filters={"reference_doctype": doc.doctype, "reference_name": doc.name, **filters},
		fields=["name", "status", "recipient", "recipient_label", "error", "retry_count", "message", "response", "next_retry_at"],
		order_by="creation asc",
	)


class FakeProvider(WhatsAppProvider):
	"""Records messages instead of calling an API. Set FakeProvider.error to make it fail."""

	sent = []
	error = None

	def send(self, message):
		if FakeProvider.error:
			raise FakeProvider.error
		FakeProvider.sent.append(message)
		return ProviderResult(message_id=f"FAKE-{len(FakeProvider.sent)}", response={"ok": True, "token": "abc-secret"})

	@classmethod
	def reset(cls):
		cls.sent = []
		cls.error = None
