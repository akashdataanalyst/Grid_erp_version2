"""Generic ERP query engine.

    tool arguments (from the LLM)
        -> registry lookup (is this DocType enabled for AI?)
        -> DocType read permission
        -> query plan (whitelisted operation / fields / filters)
        -> field permlevel + record filter permission checks
        -> frappe.get_list as the session user (permission-filtered SQL)
        -> compact, aggregated result

Works for any configured DocType (standard, custom, other apps) with no per-DocType code.
"""

import json
import time
from urllib.parse import urlencode

import frappe

from grid_erp.grid_ai.exceptions import GridAIError, InvalidRequestError, NotConfiguredError
from grid_erp.grid_ai.tools import aggregations, permissions, registry
from grid_erp.grid_ai.tools.query_builder import QueryPlan, build_plan
from grid_erp.grid_ai.utils import log_request


def execute_tool(args: dict, *, conversation_id: str | None = None) -> dict:
	"""Run one structured ERP query for the current session user.

	Always returns a dict. On failure it contains `ok: False`, an `error` code and a user-safe
	`message`; it never contains tracebacks, SQL or configuration details.
	"""
	user = permissions.current_user()
	started = time.monotonic()
	doctype = args.get("doctype") if isinstance(args, dict) else None
	operation = args.get("operation") if isinstance(args, dict) else None

	try:
		if not isinstance(args, dict):
			raise InvalidRequestError(detail="arguments must be an object")

		spec = registry.get_tool(doctype)
		if not spec:
			raise NotConfiguredError(detail=f"{doctype!r} not configured")
		doctype = spec["doctype"]

		permissions.ensure_doctype_access(doctype, user)
		plan = build_plan(spec, args)
		permissions.ensure_fields_permitted(doctype, plan.referenced_fields, user)
		permissions.ensure_filter_values_permitted(doctype, plan.explicit_filters, user)

		rows = [{plan.aliases.get(k, k): v for k, v in row.items()} for row in _run(plan)]
		result = {
			"ok": True,
			"doctype": doctype,
			"label": spec["label"],
			"operation": plan.operation,
			"date_range": plan.date_info,
			"filters": [_describe_filter(f) for f in plan.explicit_filters if f[0] != (plan.date_info or {}).get("field")],
			"result": aggregations.shape_result(plan.operation, rows, plan.limit),
			"context": plan.describe(),
		}
		currency = _currency_hint(spec, plan)
		if currency:
			result["currency"] = currency
		if _is_empty(result["result"]):
			result["note"] = "No matching records for these filters."
		# For the chat page only (record link, table, chart); the orchestrator removes it
		# before the result goes to the model.
		result["view"] = _build_view(spec, plan, result)

		_log(user, conversation_id, doctype, plan.operation, "Success", None, started)
		return result

	except GridAIError as e:
		_log(user, conversation_id, doctype, operation, e.log_status, e.code, started)
		return {"ok": False, "error": e.code, "message": e.user_message}

	except frappe.PermissionError:
		# Raised by frappe.get_list itself: ERPNext denied access.
		frappe.clear_last_message()
		_log(user, conversation_id, doctype, operation, "Denied", "permission_denied", started)
		return {
			"ok": False,
			"error": "permission_denied",
			"message": "Aapko is data ko access karne ki permission nahi hai.",
		}

	except Exception as e:
		frappe.clear_last_message()
		timed_out = _is_timeout(e)
		code = "timeout" if timed_out else "query_failed"
		if not timed_out:
			frappe.log_error(title="Grid AI: query failed", message=f"{doctype} / {operation}\n{frappe.get_traceback()}")
		_log(user, conversation_id, doctype, operation, "Error", code, started)
		return {
			"ok": False,
			"error": code,
			"message": "Query bahut time le rahi thi, filter chhota karke try kariye."
			if timed_out
			else "Sorry, data retrieve karte time problem aayi.",
		}


def _run(plan: QueryPlan) -> list[dict]:
	"""Execute with Frappe permissions applied (never ignore_permissions) and a time limit."""
	previous = _set_statement_timeout(plan.max_query_time)
	try:
		return frappe.get_list(
			plan.doctype,
			fields=plan.fields,
			filters=plan.filters,
			or_filters=plan.or_filters or None,
			group_by=plan.group_by,
			order_by=plan.order_by or "",
			limit_page_length=plan.limit or 0,
		)
	finally:
		_set_statement_timeout(previous)


def _set_statement_timeout(seconds) -> float:
	"""MariaDB per-session statement timeout. Returns the previous value."""
	if frappe.db.db_type != "mariadb":
		return 0
	try:
		previous = frappe.db.sql("select @@session.max_statement_time")[0][0]
		frappe.db.sql("set session max_statement_time = %s", (float(seconds or 0),))
		return previous
	except Exception:
		return 0


def _is_timeout(e: Exception) -> bool:
	checker = getattr(frappe.db, "is_statement_timeout", None)
	if checker:
		try:
			return bool(checker(e))
		except Exception:
			pass
	return "max_statement_time" in str(e)


def _describe_filter(condition: list) -> str:
	fieldname, operator, value = condition
	return f"{fieldname} {operator} {value}"


def _currency_hint(spec: dict, plan: QueryPlan) -> str | None:
	if plan.value_field and spec["fields"].get(plan.value_field, {}).get("fieldtype") == "Currency":
		return frappe.db.get_default("currency") or None
	return None


MAX_VIEW_ROWS = 50
MAX_VIEW_CELL = 140
# Query operators -> operators of the desk list view filters.
LIST_OPERATORS = {"between": "Between", "like": "like", "not like": "not like", "in": "in", "not in": "not in"}


def _build_view(spec: dict, plan: QueryPlan, result: dict) -> dict:
	"""What the chat page shows under an answer: where the data came from, a link to the
	same records in the desk, and the rows of list / group results (for a table / chart)."""
	view = {
		"title": plan.describe(),
		"label": spec["label"],
		"operation": plan.operation,
		"route": _list_route(spec, plan),
	}
	rows = result["result"].get("rows")
	if rows:
		view["columns"] = list(rows[0].keys())
		view["rows"] = [
			{k: (v[:MAX_VIEW_CELL] if isinstance(v, str) else v) for k, v in row.items()}
			for row in rows[:MAX_VIEW_ROWS]
		]
		if plan.operation == "group_by":
			view["chart"] = {"label": plan.group_field, "value": "value" if plan.value_field else "record_count"}
	return view


def _list_route(spec: dict, plan: QueryPlan) -> str | None:
	"""Desk list URL with the same filters. None when a filter cannot be expressed there
	(line item filters), so the link never shows different records than the answer."""
	params = {}
	for fieldname, operator, value in plan.explicit_filters:
		if "." in fieldname:
			return None
		if operator == "=":
			params[fieldname] = value
		else:
			params[fieldname] = json.dumps([LIST_OPERATORS.get(operator, operator), value], default=str)
	if spec["is_submittable"] and not spec["include_drafts"]:
		params["docstatus"] = 1
	slug = plan.doctype.lower().replace(" ", "-")
	return f"/app/{slug}" + (f"?{urlencode(params)}" if params else "")


def _is_empty(result: dict) -> bool:
	if "count" in result:
		return not result["count"]
	if "matching_records" in result:
		return not result["matching_records"]
	return not result.get("rows")


def _log(user, conversation_id, doctype, operation, status, error_code, started):
	log_request(
		request_type="Tool",
		user=user,
		conversation=conversation_id,
		reference_doctype=doctype if isinstance(doctype, str) else None,
		operation=operation if isinstance(operation, str) else None,
		status=status,
		error_code=error_code,
		execution_ms=int((time.monotonic() - started) * 1000),
	)
