"""Turn a rule's Recipients table into normalised mobile numbers for one document.

Every recipient row names where people come from (a document field, a linked
record, a role, the workflow's next approvers, a fixed number) and how to find
their number. Phone fields, primary-contact links and User links are discovered
from DocType metadata, so the same code serves Supplier, Customer, Employee,
Lead, custom DocTypes and anything else configured in the UI.

Finding a record's number (Contact Resolution):
    Primary Contact  the record's Link-to-Contact field (supplier_primary_contact, ...)
    Record Field     a phone field on the record itself
    Linked Contact   Contacts linked to the record through Contact > Links
    Auto             Primary Contact -> Record Field -> Linked Contact
"""

from dataclasses import dataclass

import frappe
from frappe import _
from frappe.model import table_fields

from grid_erp.grid_whatsapp.services.errors import ConfigurationError, InvalidRecipientError, WhatsAppError
from grid_erp.grid_whatsapp.services.field_resolver import PathError, Resolver, find_field, get_field, validate_path
from grid_erp.grid_whatsapp.utils import phone
from grid_erp.grid_whatsapp.utils.logger import debug

DOCUMENT_FIELD = "Document Field"
DOCUMENT_LINK = "Document Link"
FIXED_NUMBER = "Fixed Number"
ROLE = "Role"
WORKFLOW_APPROVER = "Workflow Approver"
USER = "User"
EMPLOYEE = "Employee"
CONTACT = "Contact"
# Recipient types that name a record of the DocType with the same name.
RECORD_TYPES = ("Employee", "User", "Customer", "Supplier", "Lead", "Contact")

AUTO = "Auto"
PRIMARY_CONTACT = "Primary Contact"
RECORD_FIELD = "Record Field"
LINKED_CONTACT = "Linked Contact"
STRATEGIES = {
	AUTO: (PRIMARY_CONTACT, RECORD_FIELD, LINKED_CONTACT),
	PRIMARY_CONTACT: (PRIMARY_CONTACT,),
	RECORD_FIELD: (RECORD_FIELD,),
	LINKED_CONTACT: (LINKED_CONTACT,),
}

# Ranking of phone-like fields when no Mobile Field is configured.
_PHONE_KEYWORDS = (("whatsapp",), ("mobile", "cell"), ("phone",))
_PHONE_FIELDTYPES = ("Data", "Phone", "Read Only", "Small Text")


@dataclass
class Recipient:
	number: str
	recipient_type: str
	label: str
	source: str = ""  # e.g. "Supplier ABC Ltd", for the log


@dataclass
class RecipientFailure:
	label: str
	recipient_type: str
	error: str
	recipient: str = ""


# ---------------------------------------------------------------------------
# Configuration checks (rule save)
# ---------------------------------------------------------------------------


def source_doctype_for(row, document_type):
	"""DocType a row's records belong to; stored in the hidden source_doctype field."""
	if row.recipient_type in RECORD_TYPES:
		return row.recipient_type
	if row.recipient_type == ROLE:
		return "Role"
	if row.recipient_type == DOCUMENT_LINK and row.source_field and document_type:
		df = validate_path(document_type, row.source_field)
		if df and df.fieldtype == "Link":
			return df.options
	return None


def validate_row(row, document_type):
	label = row.label or row.recipient_type
	prefix = _("Recipient row {0} ({1})").format(row.idx, label)

	def fail(message):
		raise ConfigurationError(f"{prefix}: {message}")

	try:
		if row.recipient_type == FIXED_NUMBER:
			if not row.fixed_number:
				fail(_("Fixed Number is required"))
			phone.normalize(row.fixed_number, default_country_code())
		elif row.recipient_type in (DOCUMENT_FIELD, DOCUMENT_LINK):
			if not row.source_field:
				fail(_("Source Field is required"))
			df = validate_path(document_type, row.source_field)
			if row.recipient_type == DOCUMENT_LINK and df and df.fieldtype not in ("Link", "Dynamic Link"):
				fail(_("{0} must be a Link field for Document Link").format(row.source_field))
			if row.recipient_type == DOCUMENT_FIELD and df and (df.fieldtype in table_fields or df.fieldtype == "Link"):
				fail(_("{0} must hold a phone number; use Document Link for linked records").format(row.source_field))
		elif row.recipient_type == ROLE:
			if not row.source:
				fail(_("Select the Role in Fixed Record"))
		elif row.recipient_type in RECORD_TYPES:
			if not frappe.db.exists("DocType", row.recipient_type):
				fail(_("DocType {0} is not installed on this site").format(row.recipient_type))
			if not row.source_field and not row.source:
				fail(_("Set a Source Field or a Fixed Record"))
			if row.source_field:
				validate_path(document_type, row.source_field)
		elif row.recipient_type == WORKFLOW_APPROVER:
			pass
		else:
			fail(_("Unknown recipient type {0}").format(row.recipient_type))

		if bool(row.record_filter_field) != bool(row.document_filter_field):
			fail(_("Set both Employee Field and Document Field to filter people, or neither"))
		if row.record_filter_field:
			if not frappe.db.exists("DocType", EMPLOYEE):
				fail(_("Filtering people needs the Employee DocType"))
			get_field(EMPLOYEE, row.record_filter_field)
			validate_path(document_type, row.document_filter_field)
	except PathError as e:
		fail(str(e))
	except InvalidRecipientError as e:
		fail(str(e))


# ---------------------------------------------------------------------------
# Run time
# ---------------------------------------------------------------------------


def default_country_code():
	return frappe.db.get_single_value("WhatsApp Settings", "default_country_code", cache=True)


def resolve(rule, doc, resolver=None, country_code=None):
	"""Return (recipients, failures). Numbers are normalised; duplicates are removed
	unless the rule allows duplicate recipients."""
	resolver = resolver or Resolver(doc)
	country_code = country_code if country_code is not None else default_country_code()
	recipients, failures = [], []

	for row in rule.recipients:
		if not row.enabled:
			continue
		label = row.label or row.recipient_type
		try:
			candidates = _candidates(row, doc, resolver)
		except WhatsAppError as e:
			failures.append(RecipientFailure(label, row.recipient_type, str(e)))
			continue

		found = False
		for raw_number, source in candidates:
			try:
				number = phone.normalize(raw_number, country_code)
			except InvalidRecipientError as e:
				failures.append(RecipientFailure(label, row.recipient_type, f"{source}: {e}", str(raw_number)))
				continue
			recipients.append(Recipient(number, row.recipient_type, label, source))
			found = True

		if not found and not candidates:
			message = _("No mobile number found for {0}").format(label)
			if row.required:
				failures.append(RecipientFailure(label, row.recipient_type, message))
			else:
				debug("%s %s: %s (row not required, skipped)", doc.doctype, doc.name, message)

	if not rule.allow_duplicate_recipient:
		recipients = _unique(recipients)
	return recipients, failures


def _unique(recipients):
	seen = {}
	for recipient in recipients:
		if recipient.number in seen:
			first = seen[recipient.number]
			if recipient.label not in first.label.split(" + "):
				first.label = f"{first.label} + {recipient.label}"
			continue
		seen[recipient.number] = recipient
	return list(seen.values())


def _candidates(row, doc, resolver):
	"""List of (raw number, description of where it came from)."""
	kind = row.recipient_type

	if kind == FIXED_NUMBER:
		return [(row.fixed_number, _("Fixed number"))] if row.fixed_number else []

	if kind == DOCUMENT_FIELD:
		value = resolver.get_value(row.source_field)
		values = value if isinstance(value, list) else [value] if value else []
		return [(v, f"{doc.doctype} {doc.name}.{row.source_field}") for v in values]

	if kind == DOCUMENT_LINK:
		return _numbers_for_records(_linked_records(row.source_field, doc, resolver), row, resolver)

	if kind in RECORD_TYPES:
		names = _record_names(row, resolver)
		records = []
		for name in names:
			record = _find_record(kind, name)
			if record:
				records.append(record)
			else:
				debug("%s %s: %s %s not found", doc.doctype, doc.name, kind, name)
		if row.record_filter_field:
			records = [r for r in records if _passes_filter(row, _employee_of(r), resolver)]
		return _numbers_for_records(records, row, resolver)

	if kind == ROLE:
		users = _users_with_role(row.source)
		return _numbers_for_users(users, row, resolver)

	if kind == WORKFLOW_APPROVER:
		return _numbers_for_users(_workflow_approvers(doc), row, resolver)

	raise ConfigurationError(_("Unknown recipient type {0}").format(kind))


def _record_names(row, resolver):
	if row.source_field:
		value = resolver.get_value(row.source_field)
		return [v for v in (value if isinstance(value, list) else [value]) if v]
	return [row.source] if row.source else []


def _find_record(doctype, name):
	"""(doctype, name) of the record. A User id given for another DocType (e.g. the
	document owner for an Employee recipient) is mapped through that DocType's
	Link-to-User field."""
	if frappe.db.exists(doctype, name):
		return (doctype, name)
	if doctype != USER and frappe.db.exists(USER, name):
		linked = _record_linked_to_user(doctype, name)
		if linked:
			return (doctype, linked)
	return None


def _record_linked_to_user(doctype, user):
	for df in frappe.get_meta(doctype).fields:
		if df.fieldtype == "Link" and df.options == USER:
			name = frappe.db.get_value(doctype, {df.fieldname: user}, "name")
			if name:
				return name
	return None


def _linked_records(path, doc, resolver):
	"""(doctype, name) pairs a Link / Dynamic Link path points to."""
	parts = path.split(".")
	holders = [doc]
	if len(parts) > 1:
		holders, _df = resolver.resolve(".".join(parts[:-1]))
	records = []
	for holder in holders:
		if not hasattr(holder, "doctype"):
			raise ConfigurationError(_("{0} does not point to a record").format(path))
		df = get_field(holder.doctype, parts[-1], path)
		name = holder.get(parts[-1])
		if not name:
			continue
		if df.fieldtype == "Link":
			records.append((df.options, name))
		elif df.fieldtype == "Dynamic Link" and holder.get(df.options):
			records.append((holder.get(df.options), name))
		else:
			raise ConfigurationError(_("{0} is not a Link field").format(path))
	return records


def _numbers_for_records(records, row, resolver):
	candidates = []
	for doctype, name in records:
		number = record_number(doctype, name, row.contact_resolution or AUTO, row.mobile_field)
		if number:
			candidates.append((number, f"{doctype} {name}"))
		else:
			debug("No mobile number on %s %s (%s)", doctype, name, row.contact_resolution)
	return candidates


def _numbers_for_users(users, row, resolver):
	candidates = []
	for user in users:
		if row.record_filter_field and not _passes_filter(row, _employee_of((USER, user)), resolver):
			continue
		number = record_number(USER, user, row.contact_resolution or AUTO, row.mobile_field)
		if number:
			candidates.append((number, f"User {user}"))
		else:
			debug("No mobile number for user %s", user)
	return candidates


def record_number(doctype, name, resolution=AUTO, mobile_field=None):
	"""First number found for a record using the given Contact Resolution."""
	strategies = STRATEGIES.get(resolution or AUTO)
	if not strategies:
		raise ConfigurationError(_("Unknown Contact Resolution {0}").format(resolution))
	for strategy in strategies:
		if strategy == PRIMARY_CONTACT:
			number = _primary_contact_number(doctype, name, mobile_field)
		elif strategy == RECORD_FIELD:
			number = _field_number(doctype, name, mobile_field)
			if not number and doctype == USER:
				number = _user_linked_number(name, mobile_field)
		else:
			number = _linked_contact_number(doctype, name, mobile_field)
		if number:
			return number
	return None


def phone_fields(doctype, mobile_field=None):
	"""Fieldnames to read a number from, best first. A configured Mobile Field
	(fieldname or label) wins when this DocType has it."""
	if mobile_field:
		configured = find_field(doctype, mobile_field)
		if configured:
			return [configured]
	ranked = []
	for df in frappe.get_meta(doctype).fields:
		if df.fieldtype not in _PHONE_FIELDTYPES:
			continue
		text = f"{df.fieldname} {df.label or ''}".lower()
		is_phone = df.fieldtype == "Phone" or df.options == "Phone"
		rank = next((i for i, words in enumerate(_PHONE_KEYWORDS) if any(w in text for w in words)), None)
		if rank is None and not is_phone:
			continue
		if "emergency" in text or "fax" in text:
			rank = len(_PHONE_KEYWORDS)
		ranked.append((rank if rank is not None else len(_PHONE_KEYWORDS) - 1, 0 if is_phone else 1, df.idx, df.fieldname))
	return [fieldname for *_rank, fieldname in sorted(ranked)]


def _field_number(doctype, name, mobile_field=None):
	fields = phone_fields(doctype, mobile_field)
	if not fields:
		return None
	values = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
	return next((values[f] for f in fields if values.get(f)), None)


def _primary_contact_number(doctype, name, mobile_field=None):
	if doctype == CONTACT:
		return None
	meta = frappe.get_meta(doctype)
	links = [df for df in meta.fields if df.fieldtype == "Link" and df.options == CONTACT]
	links.sort(key=lambda df: 0 if "primary" in df.fieldname else 1)
	for df in links:
		contact = frappe.db.get_value(doctype, name, df.fieldname)
		if contact:
			number = _field_number(CONTACT, contact, mobile_field)
			if number:
				return number
	return None


def _linked_contact_number(doctype, name, mobile_field=None):
	if doctype == CONTACT:
		return _field_number(CONTACT, name, mobile_field)
	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": CONTACT, "link_doctype": doctype, "link_name": name},
		pluck="parent",
	)
	if not contacts:
		return None
	ordered = frappe.get_all(
		CONTACT, filters={"name": ["in", contacts]}, order_by="is_primary_contact desc, creation asc", pluck="name"
	)
	for contact in ordered:
		number = _field_number(CONTACT, contact, mobile_field)
		if number:
			return number
	return None


def _user_linked_number(user, mobile_field=None):
	"""A user without a number on the User record: try their Employee / Contact."""
	for doctype in (EMPLOYEE, CONTACT):
		if not frappe.db.exists("DocType", doctype):
			continue
		record = _record_linked_to_user(doctype, user)
		if record:
			number = _field_number(doctype, record, mobile_field)
			if number:
				return number
	return None


def _users_with_role(role):
	from frappe.utils.user import get_users_with_role

	if not role or not frappe.db.exists("Role", role):
		raise ConfigurationError(_("Role {0} not found").format(role or "(none)"))
	return get_users_with_role(role)


def _workflow_approvers(doc):
	"""Users who can take the next workflow action on the document."""
	from frappe.model.workflow import get_workflow_name

	workflow_name = get_workflow_name(doc.doctype)
	if not workflow_name:
		raise ConfigurationError(_("{0} has no active Workflow").format(doc.doctype))
	workflow = frappe.get_cached_doc("Workflow", workflow_name)
	state = doc.get(workflow.workflow_state_field)
	users = []
	for transition in workflow.transitions:
		if transition.state != state:
			continue
		for user in get_users_with_role(transition.allowed):
			if user == doc.owner and not transition.allow_self_approval:
				continue
			if user not in users:
				users.append(user)
	return users


def get_users_with_role(role):
	from frappe.utils.user import get_users_with_role as _get

	return _get(role)


def _employee_of(record):
	doctype, name = record
	if doctype == EMPLOYEE:
		return name
	if doctype == USER and frappe.db.exists("DocType", EMPLOYEE):
		return _record_linked_to_user(EMPLOYEE, name)
	return None


def _passes_filter(row, employee, resolver):
	if not row.record_filter_field:
		return True
	if not employee:
		return False
	expected = resolver.get_value(row.document_filter_field)
	actual = frappe.db.get_value(EMPLOYEE, employee, row.record_filter_field)
	return bool(expected) and actual == expected
