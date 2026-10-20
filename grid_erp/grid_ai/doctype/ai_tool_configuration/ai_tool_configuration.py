# Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from grid_erp.grid_ai.tools import discovery
from grid_erp.grid_ai.tools.aggregations import OPERATION_FLAGS

MAX_RECORDS_LIMIT = 500
MAX_QUERY_TIME_LIMIT = 60


class AIToolConfiguration(Document):
	def validate(self):
		dt = self.reference_doctype
		if not frappe.db.exists("DocType", dt):
			frappe.throw(_("DocType {0} does not exist.").format(dt))

		reason = discovery.get_block_reason(dt)
		if reason:
			frappe.throw(_("{0} cannot be used by the AI Assistant: {1}").format(dt, reason))

		info = discovery.describe_doctype(dt)
		self.source_app = info["source_app"]
		self.module = info["module"]
		self.is_custom = info["is_custom"]
		self.is_submittable = info["is_submittable"]
		self.is_sensitive = info["is_sensitive"]
		if not self.label:
			self.label = dt

		self.max_records = min(max(self.max_records or 20, 1), MAX_RECORDS_LIMIT)
		self.max_query_time = min(max(self.max_query_time or 10, 1), MAX_QUERY_TIME_LIMIT)

		self.validate_fields()
		self.validate_default_date_field()
		if self.enabled:
			self.validate_enable()

	def validate_fields(self):
		meta = frappe.get_meta(self.reference_doctype)
		seen = set()
		for row in self.fields:
			row.fieldname = (row.fieldname or "").strip()
			if row.fieldname in seen:
				frappe.throw(_("Row {0}: field {1} is listed twice.").format(row.idx, row.fieldname))
			seen.add(row.fieldname)

			df = discovery.get_field_info(meta, row.fieldname)
			if not df:
				frappe.throw(
					_("Row {0}: {1} is not a field of {2}.").format(row.idx, row.fieldname, meta.name)
				)

			reason = discovery.get_field_block_reason(df)
			if reason:
				frappe.throw(_("Row {0}: field {1} cannot be used: {2}").format(row.idx, row.fieldname, reason))

			row.label = df["label"]
			row.fieldtype = df["fieldtype"]
			row.options = (df.get("options") or "")[:140] if df["fieldtype"] in ("Link", "Select") else ""

			if row.allow_aggregate and df["fieldtype"] not in discovery.NUMERIC_FIELDTYPES:
				frappe.throw(
					_("Row {0}: {1} is not numeric, so it cannot be aggregated.").format(row.idx, row.fieldname)
				)
			if row.allow_group_by and df["fieldtype"] not in discovery.GROUPABLE_FIELDTYPES:
				frappe.throw(
					_("Row {0}: grouping by a {1} field is not supported.").format(row.idx, df["fieldtype"])
				)

	def validate_default_date_field(self):
		if not self.default_date_field:
			return
		meta = frappe.get_meta(self.reference_doctype)
		df = discovery.get_field_info(meta, self.default_date_field)
		if not df or df["fieldtype"] not in discovery.DATE_FIELDTYPES or "." in self.default_date_field:
			frappe.throw(_("Default Date Field must be a Date or Datetime field of this DocType."))
		allowed = {row.fieldname for row in self.fields if row.allow_filter}
		if self.default_date_field not in allowed:
			frappe.throw(_("Default Date Field must also be in the Fields table with Filter allowed."))

	def validate_enable(self):
		if self.is_sensitive and not self.confirm_sensitive:
			frappe.throw(
				_(
					"{0} is marked sensitive. Tick the review confirmation before enabling it for the AI Assistant."
				).format(self.reference_doctype)
			)

		if not any(self.get(flag) for flag in set(OPERATION_FLAGS.values())):
			frappe.throw(_("Select at least one allowed operation before enabling."))

		needs_aggregate = self.allow_sum or self.allow_average or self.allow_min_max
		if needs_aggregate and not any(row.allow_aggregate for row in self.fields):
			frappe.throw(_("Sum / Average / Min / Max need at least one field with Aggregate allowed."))
		if self.allow_group_by and not any(row.allow_group_by for row in self.fields):
			frappe.throw(_("Group By needs at least one field with Group By allowed."))
		if (self.allow_list or self.allow_search) and not any(row.allow_display for row in self.fields):
			frappe.throw(_("List / Search need at least one field with Display allowed."))

	def on_update(self):
		from grid_erp.grid_ai.tools.registry import clear_registry_cache

		clear_registry_cache()

	def on_trash(self):
		from grid_erp.grid_ai.tools.registry import clear_registry_cache

		clear_registry_cache()


@frappe.whitelist()
def suggest_fields(doctype: str) -> list[dict]:
	"""Suggested AI Tool Field rows for a DocType. System Manager only."""
	frappe.only_for("System Manager")
	return discovery.suggest_fields(doctype)


@frappe.whitelist()
def get_field_options(doctype: str) -> list[dict]:
	"""All fields an admin may add manually (sensitive/forbidden fields excluded)."""
	frappe.only_for("System Manager")
	return discovery.get_selectable_fields(doctype)


@frappe.whitelist(methods=["POST"])
def discover_doctypes() -> dict:
	"""Create disabled configurations for any DocTypes not yet registered. System Manager only."""
	frappe.only_for("System Manager")
	created = discovery.sync_tool_configurations()
	return {"created": created}
