"""Query plan validation: the whitelist that stands between the model's tool calls and SQL.

Plain unittest cases that only build plans (no records are read or written), so they can run
against any site: `bench --site <site> run-tests --module grid_erp.grid_ai.tests.test_query_builder`, or with unittest after
`frappe.connect()`.
"""

import copy
import unittest

from grid_erp.grid_ai.exceptions import InvalidRequestError, NotConfiguredError
from grid_erp.grid_ai.tools.engine import _list_route
from grid_erp.grid_ai.tools.query_builder import build_plan


def field(fieldtype, display=True, filter=True, group_by=False, aggregate=False, options="", child_doctype=None):
	return {
		"label": "",
		"fieldtype": fieldtype,
		"options": options,
		"display": display,
		"filter": filter,
		"group_by": group_by,
		"aggregate": aggregate,
		"child_doctype": child_doctype,
	}


SPEC = {
	"doctype": "Sales Invoice",
	"label": "Sales Invoice",
	"description": "",
	"operations": ["count", "list", "search", "sum", "average", "min", "max", "group_by"],
	"fields": {
		"name": field("Data"),
		"posting_date": field("Date", group_by=True),
		"customer": field("Link", group_by=True, options="Customer"),
		"status": field("Select", group_by=True),
		"grand_total": field("Currency", aggregate=True),
		"remarks": field("Data", filter=False),
		"items.item_code": field("Link", group_by=True, options="Item", child_doctype="Sales Invoice Item"),
		"items.amount": field("Currency", aggregate=True, child_doctype="Sales Invoice Item"),
		"taxes.tax_amount": field("Currency", aggregate=True, child_doctype="Sales Taxes and Charges"),
	},
	"default_date_field": "posting_date",
	"is_submittable": True,
	"include_drafts": False,
	"max_records": 20,
	"max_query_time": 10,
}


def spec(**changes):
	s = copy.deepcopy(SPEC)
	s.update(changes)
	return s


class TestOperations(unittest.TestCase):
	def test_unknown_operation_is_rejected(self):
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "delete"})

	def test_operation_not_enabled_is_rejected(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(spec(operations=["count"]), {"operation": "sum", "field": "grand_total"})

	def test_aliases(self):
		self.assertEqual(build_plan(SPEC, {"operation": "total", "field": "grand_total"}).operation, "sum")
		self.assertEqual(build_plan(SPEC, {"operation": "avg", "field": "grand_total"}).operation, "average")

	def test_aggregate_needs_aggregatable_field(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(SPEC, {"operation": "sum", "field": "customer"})

	def test_sum(self):
		plan = build_plan(SPEC, {"operation": "sum", "field": "grand_total"})
		self.assertEqual(plan.fields[0], {"SUM": "grand_total", "as": "value"})
		self.assertEqual(plan.value_field, "grand_total")

	def test_submitted_only(self):
		plan = build_plan(SPEC, {"operation": "count"})
		self.assertIn(["docstatus", "=", 1], plan.filters)
		plan = build_plan(spec(include_drafts=True), {"operation": "count"})
		self.assertIn(["docstatus", "!=", 2], plan.filters)


class TestFilters(unittest.TestCase):
	def test_unconfigured_field_is_rejected(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(SPEC, {"operation": "count", "filters": [{"field": "secret", "operator": "=", "value": 1}]})

	def test_field_without_filter_permission_is_rejected(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(SPEC, {"operation": "count", "filters": [{"field": "remarks", "operator": "=", "value": "x"}]})

	def test_operator_aliases_and_invalid_operator(self):
		plan = build_plan(SPEC, {"operation": "count", "filters": [["grand_total", "gte", 100]]})
		self.assertIn(["grand_total", ">=", 100], plan.explicit_filters)
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "count", "filters": [["grand_total", "; drop", 1]]})

	def test_like_is_wrapped(self):
		plan = build_plan(SPEC, {"operation": "count", "filters": {"customer": ["like", "abc"]}})
		self.assertIn(["customer", "like", "%abc%"], plan.explicit_filters)

	def test_value_limits(self):
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "count", "filters": [["customer", "in", [str(i) for i in range(51)]]]})
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "count", "filters": [["customer", "=", "x" * 141]]})
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "count", "filters": [["customer", "=", {"$ne": 1}]]})

	def test_too_many_filters(self):
		filters = [["grand_total", ">", i] for i in range(11)]
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "count", "filters": filters})

	def test_date_range(self):
		plan = build_plan(SPEC, {"operation": "count", "date_range": {"period": "this_month"}})
		self.assertEqual(plan.date_info["field"], "posting_date")
		self.assertTrue(any(f[0] == "posting_date" and f[1] == "between" for f in plan.filters))

	def test_period_word_as_value_becomes_date_range(self):
		plan = build_plan(SPEC, {"operation": "count", "filters": [["posting_date", "=", "last_month"]]})
		self.assertEqual(plan.date_info["period"], "last_month")


class TestListAndSearch(unittest.TestCase):
	def test_limit_is_clamped(self):
		self.assertEqual(build_plan(SPEC, {"operation": "list"}).limit, 10)
		self.assertEqual(build_plan(SPEC, {"operation": "list", "limit": 500}).limit, 20)
		self.assertEqual(build_plan(SPEC, {"operation": "list", "limit": "x"}).limit, 10)

	def test_default_list_has_no_line_fields(self):
		plan = build_plan(SPEC, {"operation": "list"})
		self.assertFalse(any("." in f for f in plan.fields))
		self.assertIsNone(plan.child_table)

	def test_search_strips_wildcards_and_uses_document_fields(self):
		plan = build_plan(SPEC, {"operation": "search", "search_text": "%ab%"})
		self.assertTrue(all(f[2] == "%ab%" for f in plan.or_filters))
		self.assertFalse(any("." in f[0] for f in plan.or_filters))
		with self.assertRaises(InvalidRequestError):
			build_plan(SPEC, {"operation": "search", "search_text": "%a%"})

	def test_sort_by_unknown_field_is_rejected(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(SPEC, {"operation": "list", "order_by": "secret"})


class TestLineItems(unittest.TestCase):
	def test_item_wise_sum(self):
		plan = build_plan(
			SPEC, {"operation": "group_by", "group_by": "items.item_code", "aggregate": "sum", "field": "items.amount"}
		)
		self.assertEqual(plan.child_table, "items")
		self.assertEqual(plan.group_by, "`tabSales Invoice Item`.item_code")
		self.assertIn("items.item_code as items__item_code", plan.fields)
		self.assertIn({"SUM": "`tabSales Invoice Item`.amount", "as": "value"}, plan.fields)
		self.assertEqual(plan.aliases, {"items__item_code": "items.item_code"})
		self.assertIn(["items.parentfield", "=", "items"], plan.filters)

	def test_document_total_with_line_filter_is_rejected(self):
		with self.assertRaises(NotConfiguredError):
			build_plan(
				SPEC,
				{"operation": "sum", "field": "grand_total", "filters": [["items.item_code", "=", "ITEM-1"]]},
			)

	def test_two_line_tables_are_rejected(self):
		with self.assertRaises(InvalidRequestError):
			build_plan(
				SPEC,
				{
					"operation": "group_by",
					"group_by": "items.item_code",
					"aggregate": "sum",
					"field": "taxes.tax_amount",
				},
			)

	def test_line_sum_without_line_filter_still_joins_on_parentfield(self):
		plan = build_plan(SPEC, {"operation": "sum", "field": "items.amount"})
		self.assertIn(["items.parentfield", "=", "items"], plan.filters)


class TestListRoute(unittest.TestCase):
	def test_route_has_same_filters(self):
		plan = build_plan(SPEC, {"operation": "count", "filters": [["customer", "=", "ABC"]]})
		route = _list_route(SPEC, plan)
		self.assertTrue(route.startswith("/app/sales-invoice?"))
		self.assertIn("customer=ABC", route)
		self.assertIn("docstatus=1", route)

	def test_no_route_for_line_filters(self):
		plan = build_plan(SPEC, {"operation": "count", "filters": [["items.item_code", "=", "X"]]})
		self.assertIsNone(_list_route(SPEC, plan))
