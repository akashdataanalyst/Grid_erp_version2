"""Resolve a WhatsApp Template's variables for a document and render its message.

Placeholders are {{variable_name}}. They are replaced by plain text substitution:
templates are not Jinja and cannot run code. Each placeholder must be defined in
the template's Variables table, which says where its value comes from."""

import re
from dataclasses import dataclass, field

import frappe
from frappe import _
from frappe.utils import get_url, get_url_to_form, now_datetime, nowdate

from grid_erp.grid_whatsapp.services.errors import ConfigurationError, VariableError
from grid_erp.grid_whatsapp.services.field_resolver import PathError, Resolver, validate_path

PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

SOURCE_DOCUMENT_FIELD = "Document Field"
SOURCE_STATIC = "Static Value"
SOURCE_SYSTEM = "System Value"

SYSTEM_VALUES = {
	"today": lambda doc: frappe.format_value(nowdate(), {"fieldtype": "Date"}),
	"now": lambda doc: frappe.format_value(now_datetime(), {"fieldtype": "Datetime"}),
	"document_name": lambda doc: doc.name,
	"document_type": lambda doc: _(doc.doctype),
	"document_url": lambda doc: get_url_to_form(doc.doctype, doc.name),
	"site_url": lambda doc: get_url(),
	"current_user": lambda doc: frappe.utils.get_fullname(frappe.session.user),
}


@dataclass
class RenderedMessage:
	template: str
	provider_template_name: str | None
	provider_template_id: str | None
	language: str | None
	body: str
	# Variable name -> text, in the order of the template's Variables table, which is
	# the parameter order for providers that take positional template parameters.
	variables: dict = field(default_factory=dict)


def placeholders(body):
	"""Variable names used in a message body, in order of first use."""
	seen = []
	for name in PLACEHOLDER.findall(body or ""):
		if name not in seen:
			seen.append(name)
	return seen


def validate_template(template, doctype=None):
	"""Configuration checks run when a template (or a rule using it) is saved."""
	names = []
	for row in template.variables:
		name = (row.variable_name or "").strip()
		if not VARIABLE_NAME.match(name):
			raise ConfigurationError(
				_("Row {0}: variable name {1!r} may only contain letters, digits and _").format(row.idx, name)
			)
		if name in names:
			raise ConfigurationError(_("Row {0}: variable {1} is defined twice").format(row.idx, name))
		names.append(name)

		if row.source_type == SOURCE_DOCUMENT_FIELD:
			if not row.source_field:
				raise ConfigurationError(_("Row {0}: Source Field is required for {1}").format(row.idx, name))
			if doctype:
				try:
					validate_path(doctype, row.source_field)
				except PathError as e:
					raise ConfigurationError(_("Variable {0}: {1}").format(name, e)) from None
		elif row.source_type == SOURCE_SYSTEM:
			if row.source_field not in SYSTEM_VALUES:
				raise ConfigurationError(
					_("Row {0}: System Value must be one of {1}").format(row.idx, ", ".join(SYSTEM_VALUES))
				)
		elif row.source_type == SOURCE_STATIC:
			if row.required and not row.default_value:
				raise ConfigurationError(_("Row {0}: Static Value needs a Default Value").format(row.idx))

	undefined = [name for name in placeholders(template.message_body) if name not in names]
	if undefined:
		raise ConfigurationError(
			_("Message uses {0} but the Variables table does not define {1}").format(
				", ".join("{{" + n + "}}" for n in undefined), "it" if len(undefined) == 1 else "them"
			)
		)


def resolve_variables(template, doc, resolver=None):
	"""Variable name -> text for this document. Raises VariableError for a missing required value."""
	resolver = resolver or Resolver(doc)
	values = {}
	missing = []
	for row in template.variables:
		value = _resolve_one(row, doc, resolver)
		if value in (None, "") and row.default_value not in (None, ""):
			value = row.default_value
		value = "" if value is None else str(value)
		if not value and row.required:
			missing.append(row.variable_name)
		values[row.variable_name] = value
	if missing:
		raise VariableError(
			_("Required variable(s) {0} have no value in {1} {2}").format(", ".join(missing), doc.doctype, doc.name)
		)
	return values


def _resolve_one(row, doc, resolver):
	if row.source_type == SOURCE_STATIC:
		return row.default_value
	if row.source_type == SOURCE_SYSTEM:
		getter = SYSTEM_VALUES.get(row.source_field)
		if not getter:
			raise ConfigurationError(_("Unknown System Value {0}").format(row.source_field))
		return getter(doc)
	try:
		return resolver.get_formatted(row.source_field, raw=row.raw_value)
	except PathError as e:
		raise ConfigurationError(_("Variable {0}: {1}").format(row.variable_name, e)) from None


def render_body(body, values):
	return PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), body or "")


def render(template, doc, resolver=None):
	if isinstance(template, str):
		template = get_template(template)
	values = resolve_variables(template, doc, resolver)
	return RenderedMessage(
		template=template.name,
		provider_template_name=template.provider_template_name,
		provider_template_id=template.provider_template_id,
		language=template.language,
		body=render_body(template.message_body, values),
		variables=values,
	)


def get_template(name):
	if not name or not frappe.db.exists("WhatsApp Template", name):
		raise ConfigurationError(_("WhatsApp Template {0} not found").format(name or "(none)"))
	template = frappe.get_cached_doc("WhatsApp Template", name)
	if not template.active:
		raise ConfigurationError(_("WhatsApp Template {0} is not active").format(name))
	return template
