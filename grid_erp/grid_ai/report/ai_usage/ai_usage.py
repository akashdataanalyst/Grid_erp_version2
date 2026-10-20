# Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
# For license information, please see license.txt

"""AI Assistant usage per user (or per day): questions, failures, tokens, answer time.
Built from AI Query Log, which holds metadata only (no prompts or ERP values)."""

import frappe
from frappe import _
from frappe.query_builder.functions import Avg, Count, Sum
from frappe.utils import add_days, getdate, today
from pypika.functions import Date
from pypika.terms import Case


def execute(filters=None):
	filters = frappe._dict(filters or {})
	group_by_day = filters.get("group_by") == "Day"
	return _columns(group_by_day), _data(filters, group_by_day), None, _chart(filters) if group_by_day else None


def _columns(group_by_day: bool) -> list[dict]:
	first = (
		{"fieldname": "day", "label": _("Day"), "fieldtype": "Date", "width": 110}
		if group_by_day
		else {"fieldname": "user", "label": _("User"), "fieldtype": "Link", "options": "User", "width": 220}
	)
	return [
		first,
		{"fieldname": "questions", "label": _("Questions"), "fieldtype": "Int", "width": 100},
		{"fieldname": "failed", "label": _("Failed / Denied"), "fieldtype": "Int", "width": 120},
		{"fieldname": "prompt_tokens", "label": _("Prompt Tokens"), "fieldtype": "Int", "width": 130},
		{"fieldname": "completion_tokens", "label": _("Answer Tokens"), "fieldtype": "Int", "width": 130},
		{"fieldname": "total_tokens", "label": _("Total Tokens"), "fieldtype": "Int", "width": 130},
		{"fieldname": "avg_seconds", "label": _("Avg Answer Time (s)"), "fieldtype": "Float", "precision": 1, "width": 150},
	]


def _data(filters, group_by_day: bool) -> list[dict]:
	log = frappe.qb.DocType("AI Query Log")
	key = Date(log.creation) if group_by_day else log.user
	failed = Sum(Case().when(log.status != "Success", 1).else_(0))
	query = (
		frappe.qb.from_(log)
		.select(
			key.as_("day" if group_by_day else "user"),
			Count(log.name).as_("questions"),
			failed.as_("failed"),
			Sum(log.prompt_tokens).as_("prompt_tokens"),
			Sum(log.completion_tokens).as_("completion_tokens"),
			Avg(log.execution_ms).as_("avg_ms"),
		)
		.where(log.request_type == "Chat")
		.where(Date(log.creation) >= getdate(filters.get("from_date") or add_days(today(), -30)))
		.where(Date(log.creation) <= getdate(filters.get("to_date") or today()))
		.groupby(key)
		.orderby(key)
	)
	if filters.get("user"):
		query = query.where(log.user == filters.user)

	rows = query.run(as_dict=True)
	for row in rows:
		row.prompt_tokens = int(row.prompt_tokens or 0)
		row.completion_tokens = int(row.completion_tokens or 0)
		row.total_tokens = row.prompt_tokens + row.completion_tokens
		row.avg_seconds = round((row.pop("avg_ms") or 0) / 1000, 1)
	return rows


def _chart(filters) -> dict | None:
	rows = _data(filters, group_by_day=True)
	if not rows:
		return None
	return {
		"data": {
			"labels": [str(r.day) for r in rows],
			"datasets": [{"name": _("Total Tokens"), "values": [r.total_tokens for r in rows]}],
		},
		"type": "bar",
	}
