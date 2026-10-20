"""Read values from a document through configured field paths, using DocType metadata.

A path is a dot-separated list of fieldnames:

    grand_total                 field on the document
    supplier.supplier_name      follow a Link field into the linked record
    supplier.primary_contact.mobile_no
    items.item_code             every row of a child table (returns a list)
    items.0.item_code           one row of a child table (0 = first row)

Link and Dynamic Link fields are followed into the linked record; Table fields
expand to their rows. Password fields can never be read. Missing links, empty
values and out-of-range rows resolve to nothing instead of raising."""

import re

import frappe
from frappe import _
from frappe.model import default_fields, table_fields

from grid_erp.grid_whatsapp.services.errors import ConfigurationError

MAX_DEPTH = 6
_PART = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_]*|\d+)$")
STANDARD_FIELDS = set(default_fields) | {"amended_from"}
BLOCKED_FIELDTYPES = {"Password"}


class PathError(ConfigurationError):
	pass


def split_path(path):
	parts = [p.strip() for p in str(path or "").strip().split(".")]
	if not parts or not parts[0]:
		raise PathError(_("Field path is empty"))
	if len(parts) > MAX_DEPTH:
		raise PathError(_("Field path {0} is too deep (maximum {1} parts)").format(path, MAX_DEPTH))
	for part in parts:
		if not _PART.match(part):
			raise PathError(_("Invalid field path {0}: {1!r} is not a fieldname").format(path, part))
	if parts[0].isdigit():
		raise PathError(_("Invalid field path {0}: it must start with a fieldname").format(path))
	return parts


def get_field(doctype, fieldname, path=None):
	"""Return the docfield (or a stand-in for standard fields) or raise PathError."""
	meta = frappe.get_meta(doctype)
	df = meta.get_field(fieldname)
	if df:
		if df.fieldtype in BLOCKED_FIELDTYPES:
			raise PathError(_("Field {0} of {1} is a password field and cannot be used").format(fieldname, doctype))
		return df
	if fieldname in STANDARD_FIELDS:
		return frappe._dict(fieldname=fieldname, fieldtype="Data", label=fieldname, options=None)
	raise PathError(
		_("Field {0} does not exist in {1}{2}").format(
			fieldname, doctype, f" (path {path})" if path and path != fieldname else ""
		)
	)


def find_field(doctype, name_or_label):
	"""Fieldname for a configured value that may be a fieldname or a label (case-insensitive)."""
	meta = frappe.get_meta(doctype)
	value = (name_or_label or "").strip()
	if meta.get_field(value) or value in STANDARD_FIELDS:
		return value
	lowered = value.lower()
	for df in meta.fields:
		if (df.label or "").strip().lower() == lowered or df.fieldname == lowered.replace(" ", "_"):
			return df.fieldname
	return None


def validate_path(doctype, path, check_permission=True):
	"""Check a path against metadata when it is configured. Returns the final docfield.

	With check_permission, the current user must be able to read every DocType the
	path passes through, so a rule cannot be used to read data its author cannot see."""
	parts = split_path(path)
	current = doctype
	df = None
	for index, part in enumerate(parts):
		if part.isdigit():
			if not df or df.fieldtype not in table_fields:
				raise PathError(_("Invalid field path {0}: a row number must follow a table field").format(path))
			continue
		if current is None:
			# Beyond a Dynamic Link the target DocType is only known at run time.
			return None
		if check_permission and not frappe.has_permission(current, "read"):
			raise PathError(_("You do not have permission to read {0} (field path {1})").format(current, path))
		df = get_field(current, part, path)
		last = index == len(parts) - 1
		if last:
			return df
		if df.fieldtype == "Link":
			current = df.options
		elif df.fieldtype in table_fields:
			current = df.options
		elif df.fieldtype == "Dynamic Link":
			current = None
		else:
			raise PathError(
				_("Invalid field path {0}: {1} is a {2} field, so it cannot be followed by .{3}").format(
					path, part, df.fieldtype, parts[index + 1]
				)
			)
	return df


class Resolver:
	"""Resolves paths against one document. Linked records are loaded once per resolver."""

	def __init__(self, doc):
		self.doc = doc
		self._docs = {}

	def get_doc(self, doctype, name):
		key = (doctype, name)
		if key not in self._docs:
			try:
				self._docs[key] = frappe.get_doc(doctype, name)
			except frappe.DoesNotExistError:
				self._docs[key] = None
		return self._docs[key]

	def resolve(self, path, doc=None):
		"""Return (values, docfield). values is a list: empty when nothing was found,
		several entries when the path runs through a child table."""
		values, df, _multiple = self._resolve(path, doc)
		return values, df

	def _resolve(self, path, doc=None):
		parts = split_path(path)
		values = [doc or self.doc]
		df = None
		multiple = False
		for index, part in enumerate(parts):
			last = index == len(parts) - 1
			if part.isdigit():
				position = int(part)
				values = [values[position]] if position < len(values) else []
				multiple = False
				continue

			next_values = []
			for record in values:
				if record is None:
					continue
				df = get_field(record.doctype, part, path)
				value = record.get(part)
				if last:
					next_values.append(value)
				elif df.fieldtype in table_fields:
					next_values.extend(value or [])
					multiple = True
				elif df.fieldtype in ("Link", "Dynamic Link"):
					target = df.options if df.fieldtype == "Link" else record.get(df.options)
					if value and target:
						next_values.append(self.get_doc(target, value))
				else:
					raise PathError(
						_("Invalid field path {0}: {1} is a {2} field, so it cannot be followed by .{3}").format(
							path, part, df.fieldtype, parts[index + 1]
						)
					)
			values = next_values
		return [v for v in values if v is not None], df, multiple

	def get_value(self, path, doc=None):
		"""Raw value: a list for paths through a child table (items.qty), otherwise
		a single value or None."""
		values, _df, multiple = self._resolve(path, doc)
		values = [v for v in values if v not in (None, "")]
		if multiple:
			return values
		return values[0] if values else None

	def get_formatted(self, path, raw=False):
		"""Display text: currency, dates and numbers formatted as in the desk."""
		values, df = self.resolve(path)
		values = [v for v in values if v not in (None, "")]
		if not values:
			return ""
		if raw or not df:
			return ", ".join(str(v) for v in values)
		return ", ".join(_format(v, df, self.doc) for v in values)


def _format(value, df, doc):
	try:
		return frappe.format_value(value, df=df, doc=doc, translated=True) or str(value)
	except Exception:
		return str(value)
