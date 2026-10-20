"""Safe evaluation of rule conditions. User text is parsed, never executed.

The expression is parsed with Python's `ast` module and only the node types
below are interpreted by this module; anything else (function calls,
subscripts, lambdas, comprehensions, imports, dunder access...) is rejected
when the rule is saved and again at run time.

Supported syntax
----------------
Values      field names and dot paths: docstatus, grand_total, supplier.supplier_group,
            items.item_code (a list of every row's value)
            previous.<field>: the value before this save (None for new documents)
            literals: "text", 'text', 10, 2.5, True, False, None, [list], (tuple)
Comparison  ==  !=  <  <=  >  >=  in  not in  is None  is not None
            chains work: 100000 < grand_total <= 500000
Logic       and  or  not  ( )
Arithmetic  +  -  *  /  //  %   (e.g. grand_total - advance_paid > 1000)

Dates are compared with date strings ("2026-04-01") as dates. A missing field
or empty link resolves to None instead of raising.
"""

import ast
import datetime
import operator
from decimal import Decimal

from frappe import _
from frappe.utils import get_datetime, getdate

from grid_erp.grid_whatsapp.services.errors import ConditionError
from grid_erp.grid_whatsapp.services.field_resolver import PathError, Resolver, validate_path

MAX_LENGTH = 1000
PREVIOUS = "previous"

_COMPARE = {
	ast.Eq: operator.eq,
	ast.NotEq: operator.ne,
	ast.Lt: operator.lt,
	ast.LtE: operator.le,
	ast.Gt: operator.gt,
	ast.GtE: operator.ge,
	ast.In: lambda a, b: a in b,
	ast.NotIn: lambda a, b: a not in b,
	ast.Is: operator.is_,
	ast.IsNot: operator.is_not,
}
_BINARY = {
	ast.Add: operator.add,
	ast.Sub: operator.sub,
	ast.Mult: operator.mul,
	ast.Div: operator.truediv,
	ast.FloorDiv: operator.floordiv,
	ast.Mod: operator.mod,
}
_UNARY = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}


def parse(expression):
	"""Parse and structurally validate an expression. Returns the AST."""
	expression = (expression or "").strip()
	if not expression:
		raise ConditionError(_("Condition is empty"))
	if len(expression) > MAX_LENGTH:
		raise ConditionError(_("Condition is longer than {0} characters").format(MAX_LENGTH))
	try:
		tree = ast.parse(expression, mode="eval")
	except SyntaxError as e:
		raise ConditionError(_("Condition has a syntax error: {0}").format(e.msg)) from None
	_check(tree.body)
	return tree


def _check(node):
	if isinstance(node, ast.BoolOp | ast.Compare | ast.BinOp | ast.UnaryOp):
		ops = (
			[node.op]
			if isinstance(node, ast.BoolOp | ast.BinOp | ast.UnaryOp)
			else node.ops
		)
		for op in ops:
			if not isinstance(op, ast.And | ast.Or) and type(op) not in _COMPARE | _BINARY | _UNARY:
				raise ConditionError(_("Operator {0} is not allowed in conditions").format(type(op).__name__))
		children = (
			node.values
			if isinstance(node, ast.BoolOp)
			else [node.left, *node.comparators]
			if isinstance(node, ast.Compare)
			else [node.left, node.right]
			if isinstance(node, ast.BinOp)
			else [node.operand]
		)
		for child in children:
			_check(child)
	elif isinstance(node, ast.List | ast.Tuple | ast.Set):
		for child in node.elts:
			_check(child)
	elif isinstance(node, ast.Constant):
		if not isinstance(node.value, str | int | float | bool | type(None)):
			raise ConditionError(_("Unsupported value {0!r} in condition").format(node.value))
	elif isinstance(node, ast.Name | ast.Attribute):
		_path_of(node)
	else:
		raise ConditionError(
			_("{0} is not allowed in conditions. Use fields, values, comparisons and and/or/not.").format(
				_describe(node)
			)
		)


def _describe(node):
	if isinstance(node, ast.Call):
		return _("A function call")
	if isinstance(node, ast.Subscript):
		return _("Indexing with [ ]")
	return type(node).__name__


def _path_of(node):
	"""Dot path for Name/Attribute chains, e.g. supplier.supplier_group."""
	parts = []
	while isinstance(node, ast.Attribute):
		parts.append(node.attr)
		node = node.value
	if not isinstance(node, ast.Name):
		raise ConditionError(_("Only field names can be followed by a dot"))
	parts.append(node.id)
	path = ".".join(reversed(parts))
	if "__" in path:
		raise ConditionError(_("Field names with double underscores are not allowed"))
	return path


def field_paths(tree):
	"""All field paths used in the expression (previous.x reported as x)."""
	paths = []
	for node in ast.walk(tree):
		if isinstance(node, ast.Attribute | ast.Name) and not _is_inner_attribute(tree, node):
			path = _path_of(node)
			if path.startswith(PREVIOUS + "."):
				path = path[len(PREVIOUS) + 1 :]
			if path != PREVIOUS:
				paths.append(path)
	return paths


def _is_inner_attribute(tree, target):
	for node in ast.walk(tree):
		if isinstance(node, ast.Attribute) and node.value is target:
			return True
	return False


def validate(expression, doctype):
	"""Validate syntax and every field path against the DocType. Used when a rule is saved."""
	tree = parse(expression)
	for path in field_paths(tree):
		if path in ("True", "False", "None"):
			continue
		try:
			validate_path(doctype, path)
		except PathError as e:
			raise ConditionError(_("Condition: {0}").format(e)) from None
	return tree


def evaluate(expression, doc, previous=None, resolver=None):
	"""Return True/False for the document. Raises ConditionError for invalid conditions."""
	tree = parse(expression)
	context = _Context(resolver or Resolver(doc), doc, previous)
	try:
		return bool(context.eval(tree.body))
	except ConditionError:
		raise
	except PathError as e:
		raise ConditionError(_("Condition: {0}").format(e)) from None
	except (TypeError, ValueError, ZeroDivisionError, ArithmeticError) as e:
		raise ConditionError(_("Condition could not be evaluated: {0}").format(e)) from None


class _Context:
	def __init__(self, resolver, doc, previous):
		self.resolver = resolver
		self.doc = doc
		self.previous = previous

	def eval(self, node):
		if isinstance(node, ast.BoolOp):
			if isinstance(node.op, ast.And):
				result = True
				for value in node.values:
					result = self.eval(value)
					if not result:
						return result
				return result
			result = False
			for value in node.values:
				result = self.eval(value)
				if result:
					return result
			return result

		if isinstance(node, ast.Compare):
			left = self.eval(node.left)
			for op, comparator in zip(node.ops, node.comparators, strict=True):
				right = self.eval(comparator)
				if not _compare(op, left, right):
					return False
				left = right
			return True

		if isinstance(node, ast.BinOp):
			left, right = _numeric_pair(self.eval(node.left), self.eval(node.right))
			return _BINARY[type(node.op)](left, right)

		if isinstance(node, ast.UnaryOp):
			return _UNARY[type(node.op)](self.eval(node.operand))

		if isinstance(node, ast.Constant):
			return node.value

		if isinstance(node, ast.List | ast.Tuple | ast.Set):
			return [self.eval(element) for element in node.elts]

		if isinstance(node, ast.Name | ast.Attribute):
			return self.lookup(_path_of(node))

		raise ConditionError(_("{0} is not allowed in conditions").format(_describe(node)))

	def lookup(self, path):
		if path in ("True", "False", "None"):
			return {"True": True, "False": False, "None": None}[path]
		if path == PREVIOUS:
			raise ConditionError(_("Use previous.<fieldname>, e.g. previous.status"))
		if path.startswith(PREVIOUS + "."):
			if not self.previous:
				return None
			return self.resolver.get_value(path[len(PREVIOUS) + 1 :], doc=self.previous)
		return self.resolver.get_value(path)


def _compare(op, left, right):
	if type(op) in (ast.In, ast.NotIn):
		if right is None:
			right = []
		if isinstance(right, str):
			return _COMPARE[type(op)](str(left or ""), right)
		right = [_coerce_like(item, left) for item in right]
		return _COMPARE[type(op)](left, right)
	if type(op) in (ast.Is, ast.IsNot):
		return _COMPARE[type(op)](left, right)
	left, right = _coerce_pair(left, right)
	if left is None or right is None:
		# None only equals None; ordering against None is False instead of a TypeError.
		if type(op) is ast.Eq:
			return left is right
		if type(op) is ast.NotEq:
			return left is not right
		return False
	return _COMPARE[type(op)](left, right)


def _coerce_pair(left, right):
	return _coerce_like(left, right), _coerce_like(right, left)


def _coerce_like(value, other):
	"""Make value comparable with other: numbers with numbers, dates with date strings."""
	if value is None or other is None:
		return value
	if isinstance(value, Decimal):
		value = float(value)
	if isinstance(other, datetime.datetime) and isinstance(value, str | datetime.date):
		return get_datetime(value)
	if isinstance(other, datetime.date) and isinstance(value, str):
		return getdate(value)
	if isinstance(other, int | float | Decimal) and not isinstance(other, bool) and isinstance(value, str):
		try:
			return float(value)
		except ValueError:
			return value
	return value


def _numeric_pair(left, right):
	def number(value):
		if value is None:
			return 0
		if isinstance(value, Decimal):
			return float(value)
		if isinstance(value, str):
			try:
				return float(value)
			except ValueError:
				raise ConditionError(_("{0!r} is not a number").format(value)) from None
		return value

	return number(left), number(right)
