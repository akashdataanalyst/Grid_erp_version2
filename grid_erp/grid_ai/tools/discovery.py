"""Dynamic DocType discovery from Frappe metadata.

Finds every queryable DocType of every installed app (standard, custom, future) and registers
it in AI Tool Configuration as *disabled*. An administrator must review and enable it.
"""

import re

import frappe

# DocTypes that can never be exposed to the AI, whatever the configuration says.
BLOCKED_DOCTYPES = frozenset(
	{
		"User",
		"Has Role",
		"Role",
		"Role Profile",
		"User Permission",
		"Custom DocPerm",
		"DocPerm",
		"DocShare",
		"Session Default Settings",
		"Access Log",
		"Activity Log",
		"Error Log",
		"Scheduled Job Log",
		"Integration Request",
		"Webhook Request Log",
		"Email Account",
		"Email Queue",
		"Email Queue Recipient",
		"Unhandled Email",
		"OAuth Client",
		"OAuth Bearer Token",
		"OAuth Authorization Code",
		"OAuth Provider Settings",
		"Social Login Key",
		"User Social Login",
		"Connected App",
		"Token Cache",
		"LDAP Settings",
		"Server Script",
		"Client Script",
		"System Console",
		"Data Import",
		"Data Export",
		"Prepared Report",
		"Deleted Document",
		"Version",
		"Document Share Key",
		"Personal Data Deletion Request",
		"Personal Data Download Request",
		# Grid AI's own internals
		"AI Settings",
		"AI Tool Configuration",
		"AI Tool Field",
		"AI Conversation",
		"AI Chat Message",
		"AI Query Log",
		"AI User Settings",
		"AI Allowed Role",
	}
)

# Discovered and listed, but flagged: enabling requires explicit confirmation.
SENSITIVE_DOCTYPE_PATTERN = re.compile(
	r"salary|payroll|payslip|\bbank\b|password|\btoken|secret|credential|oauth|\bapi\b|"
	r"\bsettings\b|gratuity|\bloan\b|tax exemption|income tax|payment gateway|appraisal|"
	r"grievance|disciplinary|medical|\bhealth\b|employee checkin|full and final|\bkeys?\b",
	re.IGNORECASE,
)

# Field names that can never be exposed.
FORBIDDEN_FIELD_PATTERN = re.compile(
	r"password|passwd|secret|token|api_key|apikey|private_key|access_key|client_key|"
	r"(^|_)otp(_|$)|(^|_)salt(_|$)|_hash$|^hash|credential",
	re.IGNORECASE,
)

# Field names that are allowed but never suggested automatically.
SENSITIVE_FIELD_PATTERN = re.compile(
	r"bank|iban|ifsc|account_no|pan_number|aadhaar|aadhar|passport|ctc|salary|"
	r"date_of_birth|blood_group|health|marital|personal_email|cell_number|mobile|phone|"
	r"current_address|permanent_address|emergency",
	re.IGNORECASE,
)

FORBIDDEN_FIELDTYPES = frozenset(
	{
		"Password",
		"Table",
		"Table MultiSelect",
		"Section Break",
		"Column Break",
		"Tab Break",
		"Fold",
		"Heading",
		"HTML",
		"Button",
		"Image",
		"Attach",
		"Attach Image",
		"Signature",
		"Geolocation",
		"Code",
		"JSON",
	}
)
LONG_TEXT_FIELDTYPES = frozenset({"Text", "Long Text", "Text Editor", "HTML Editor", "Markdown Editor"})
NUMERIC_FIELDTYPES = frozenset({"Currency", "Float", "Int", "Percent", "Duration"})
DATE_FIELDTYPES = frozenset({"Date", "Datetime"})
GROUPABLE_FIELDTYPES = frozenset({"Link", "Select", "Data", "Check", "Date", "Rating"})
TEXT_SEARCH_FIELDTYPES = frozenset({"Data", "Link", "Select", "Small Text", "Dynamic Link"})

# Standard columns every DocType has. docstatus is handled by the engine itself.
STANDARD_FIELDS = {
	"name": {"fieldname": "name", "label": "ID", "fieldtype": "Data"},
	"owner": {"fieldname": "owner", "label": "Created By", "fieldtype": "Link", "options": "User"},
	"creation": {"fieldname": "creation", "label": "Created On", "fieldtype": "Datetime"},
	"modified": {"fieldname": "modified", "label": "Last Updated On", "fieldtype": "Datetime"},
}

AUTO_SUGGEST_LIMIT = 40


def get_block_reason(doctype: str) -> str | None:
	"""Return why a DocType can never be used by the AI, or None if it may be configured."""
	if doctype in BLOCKED_DOCTYPES:
		return "security-critical system DocType"
	row = frappe.db.get_value("DocType", doctype, ["istable", "issingle", "is_virtual"], as_dict=True)
	if not row:
		return "DocType not found"
	if row.istable:
		return "child tables are queried through their parent"
	if row.issingle:
		return "single (settings) DocTypes hold configuration, not records"
	if row.is_virtual:
		return "virtual DocTypes have no database table"
	return None


def is_sensitive_doctype(doctype: str) -> bool:
	return bool(SENSITIVE_DOCTYPE_PATTERN.search(doctype))


def describe_doctype(doctype: str) -> dict:
	row = frappe.db.get_value("DocType", doctype, ["module", "custom", "is_submittable"], as_dict=True)
	app = frappe.db.get_value("Module Def", row.module, "app_name") if row.module else None
	return {
		"source_app": app or "",
		"module": row.module or "",
		"is_custom": 1 if row.custom else 0,
		"is_submittable": 1 if row.is_submittable else 0,
		"is_sensitive": 1 if is_sensitive_doctype(doctype) else 0,
	}


def list_discoverable_doctypes() -> list[dict]:
	"""All DocTypes of all installed apps that could be configured for AI."""
	rows = frappe.get_all(
		"DocType",
		filters={"istable": 0, "issingle": 0, "is_virtual": 0},
		fields=["name", "module", "custom", "is_submittable"],
		order_by="name asc",
	)
	return [r for r in rows if r.name not in BLOCKED_DOCTYPES]


def sync_tool_configurations() -> int:
	"""Register every new DocType as a disabled AI Tool Configuration. Returns how many were created."""
	if not frappe.db.table_exists("AI Tool Configuration"):
		return 0

	existing = set(frappe.get_all("AI Tool Configuration", pluck="name"))
	created = 0
	for row in list_discoverable_doctypes():
		if row.name in existing:
			continue
		if _create_disabled_configuration(row.name):
			created += 1

	if created:
		from grid_erp.grid_ai.tools.registry import clear_registry_cache

		clear_registry_cache()
	return created


def on_doctype_created(doc, method=None):
	"""doc_events hook: register DocTypes created later (custom DocTypes, newly installed apps)."""
	if doc.istable or doc.issingle or doc.is_virtual or doc.name in BLOCKED_DOCTYPES:
		return
	try:
		if frappe.db.table_exists("AI Tool Configuration") and not frappe.db.exists(
			"AI Tool Configuration", doc.name
		):
			_create_disabled_configuration(doc.name)
	except Exception:
		# Discovery must never break DocType creation or app installation.
		frappe.log_error(title="Grid AI: DocType discovery failed")


def _create_disabled_configuration(doctype: str) -> bool:
	try:
		config = frappe.new_doc("AI Tool Configuration")
		config.reference_doctype = doctype
		config.label = doctype
		config.enabled = 0
		config.insert(ignore_permissions=True)
		return True
	except Exception:
		frappe.log_error(title=f"Grid AI: could not register {doctype}")
		return False


# ---------------------------------------------------------------- fields


def get_field_info(meta, fieldname: str) -> dict | None:
	"""Field metadata as a plain dict; supports standard columns like `name` and `creation`,
	and child table fields written as `<table field>.<child field>` (e.g. `items.item_code`)."""
	if fieldname in STANDARD_FIELDS:
		return dict(STANDARD_FIELDS[fieldname])
	if "." in fieldname:
		return _child_field_info(meta, fieldname)
	df = meta.get_field(fieldname)
	if not df:
		return None
	return _df_info(df, df.fieldname, df.label or df.fieldname)


def _child_field_info(meta, path: str) -> dict | None:
	table_fieldname, _, child_fieldname = path.partition(".")
	table_df = meta.get_field(table_fieldname)
	if not table_df or table_df.fieldtype != "Table" or not table_df.options or "." in child_fieldname:
		return None
	if table_df.options in BLOCKED_DOCTYPES:
		return None
	df = frappe.get_meta(table_df.options).get_field(child_fieldname)
	if not df:
		return None
	info = _df_info(df, path, f"{table_df.label or table_fieldname} › {df.label or child_fieldname}")
	info["table_field"] = table_fieldname
	info["child_doctype"] = table_df.options
	return info


def _df_info(df, fieldname: str, label: str) -> dict:
	return {
		"fieldname": fieldname,
		"label": label,
		"fieldtype": df.fieldtype,
		"options": df.options,
		"hidden": df.hidden,
		"in_list_view": df.in_list_view,
		"in_standard_filter": df.in_standard_filter,
		"bold": df.bold,
		"reqd": df.reqd,
	}


def get_field_block_reason(df: dict) -> str | None:
	if df["fieldtype"] in FORBIDDEN_FIELDTYPES:
		return f"{df['fieldtype']} fields are not allowed"
	if FORBIDDEN_FIELD_PATTERN.search(df["fieldname"]):
		return "looks like a secret / credential field"
	return None


def get_selectable_fields(doctype: str) -> list[dict]:
	"""Every field an admin may add (forbidden ones removed), including the fields of the
	DocType's child tables as `<table field>.<child field>`."""
	meta = frappe.get_meta(doctype)
	fields = [dict(STANDARD_FIELDS[f]) for f in STANDARD_FIELDS]
	for df in meta.fields:
		info = get_field_info(meta, df.fieldname)
		if info and not get_field_block_reason(info):
			fields.append(info)
	for table_df in meta.get_table_fields():
		if table_df.fieldtype != "Table" or table_df.options in BLOCKED_DOCTYPES:
			continue
		for df in frappe.get_meta(table_df.options).fields:
			info = get_field_info(meta, f"{table_df.fieldname}.{df.fieldname}")
			if info and not get_field_block_reason(info) and info["fieldtype"] not in LONG_TEXT_FIELDTYPES:
				fields.append(info)
	return fields


def suggest_fields(doctype: str) -> list[dict]:
	"""A conservative starting field list for a DocType. The admin reviews it before enabling."""
	meta = frappe.get_meta(doctype)
	title_fields = {meta.title_field} | set((meta.search_fields or "").replace(" ", "").split(","))

	candidates = []
	for info in get_selectable_fields(doctype):
		if info["fieldname"] in STANDARD_FIELDS or "." in info["fieldname"]:
			continue  # child table fields are only added deliberately by the admin
		if info.get("hidden") or info["fieldtype"] in LONG_TEXT_FIELDTYPES:
			continue
		if SENSITIVE_FIELD_PATTERN.search(info["fieldname"]):
			continue
		candidates.append(info)

	if len(candidates) > AUTO_SUGGEST_LIMIT:
		important = [
			c
			for c in candidates
			if c.get("in_list_view")
			or c.get("in_standard_filter")
			or c.get("bold")
			or c.get("reqd")
			or c["fieldname"] in title_fields
		]
		candidates = important[:AUTO_SUGGEST_LIMIT]

	rows = [_suggested_row(STANDARD_FIELDS[f]) for f in ("name", "creation", "owner")]
	rows += [_suggested_row(c) for c in candidates]
	return rows


def _suggested_row(info: dict) -> dict:
	fieldtype = info["fieldtype"]
	return {
		"fieldname": info["fieldname"],
		"label": info["label"],
		"fieldtype": fieldtype,
		"options": (info.get("options") or "")[:140] if fieldtype in ("Link", "Select") else "",
		"allow_display": 1,
		"allow_filter": 1,
		"allow_group_by": 1 if fieldtype in ("Link", "Select") else 0,
		"allow_aggregate": 1 if fieldtype in NUMERIC_FIELDTYPES else 0,
	}
