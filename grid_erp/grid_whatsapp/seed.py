"""Starter WhatsApp configuration created when grid_erp is installed on a site.

seed_data/ is an export of the working setup: the Evolution instance, WhatsApp Settings,
the Leave Application templates and their notification rules. Records are only inserted
when missing, so whatever is changed on a site afterwards is never overwritten.

Secrets (API Key, Webhook Secret) are not shipped: fill them on the WhatsApp Instance
after install. Templates and rules whose DocType is not installed yet (e.g. Leave
Application before HRMS) are skipped; run this again once that app is installed:

    bench --site <site> execute grid_erp.grid_whatsapp.seed.seed_all
"""

import json
import os

import frappe

SEED_DIR = os.path.join(os.path.dirname(__file__), "seed_data")


def seed_all():
	for doc in _load("whatsapp_instance.json"):
		_insert(doc, "instance_name")
	_apply_settings(_load("whatsapp_settings.json"))
	for doc in _load("whatsapp_template.json"):
		_insert(doc, "template_name", needs_doctype=doc.get("reference_doctype"))
	for doc in _load("whatsapp_notification_rule.json"):
		_insert(doc, "rule_name", needs_doctype=doc.get("document_type"))


def _load(filename):
	with open(os.path.join(SEED_DIR, filename)) as f:
		return json.load(f)


def _insert(data, name_field, needs_doctype=None):
	doctype, name = data["doctype"], data[name_field]
	if frappe.db.exists(doctype, name):
		return
	if needs_doctype and not frappe.db.exists("DocType", needs_doctype):
		print(f"WhatsApp seed: skipped {doctype} {name} ({needs_doctype} is not installed)")
		return
	try:
		frappe.get_doc(data).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"WhatsApp seed: could not create {doctype} {name}")


def _apply_settings(data):
	"""Fill WhatsApp Settings only while it has never been configured."""
	settings = frappe.get_single("WhatsApp Settings")
	if settings.default_country_code:
		return
	data = dict(data)
	if data.get("default_instance") and not frappe.db.exists("WhatsApp Instance", data["default_instance"]):
		data.pop("default_instance")
	settings.update(data)
	try:
		settings.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "WhatsApp seed: could not update WhatsApp Settings")
