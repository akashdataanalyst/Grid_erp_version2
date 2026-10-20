"""Server-side date range resolution.

Periods are always resolved against the ERPNext server date, never the browser's date.
"""

import datetime

import frappe
from frappe.utils import add_days, add_months, get_first_day, get_first_day_of_week, get_last_day, getdate

from grid_erp.grid_ai.exceptions import InvalidRequestError

PERIODS = (
	"today",
	"yesterday",
	"this_week",
	"last_week",
	"this_month",
	"last_month",
	"this_quarter",
	"last_quarter",
	"this_year",
	"last_year",
	"this_financial_year",
	"last_financial_year",
	"last_7_days",
	"last_30_days",
)

# Hindi / Hinglish phrases the model may pass through unchanged
PERIOD_ALIASES = {
	"aaj": "today",
	"kal": "yesterday",
	"is week": "this_week",
	"is hafte": "this_week",
	"pichle week": "last_week",
	"pichla week": "last_week",
	"is month": "this_month",
	"is mahine": "this_month",
	"pichle month": "last_month",
	"pichla month": "last_month",
	"pichle mahine": "last_month",
	"is saal": "this_year",
	"pichle saal": "last_year",
	"fy": "this_financial_year",
	"current financial year": "this_financial_year",
	"current_financial_year": "this_financial_year",
	"financial_year": "this_financial_year",
	"current_month": "this_month",
	"current_week": "this_week",
	"current_year": "this_year",
	"previous_month": "last_month",
	"previous_week": "last_week",
	"previous_year": "last_year",
}


def normalize_period(period: str) -> str:
	key = (period or "").strip().lower()
	key = PERIOD_ALIASES.get(key, key).replace(" ", "_")
	if key not in PERIODS:
		raise InvalidRequestError(detail=f"unknown period {period!r}")
	return key


def resolve_period(period: str, today: datetime.date | None = None) -> tuple[datetime.date, datetime.date]:
	"""Return the inclusive (from_date, to_date) for a named period."""
	period = normalize_period(period)
	today = getdate(today or frappe.utils.today())

	if period == "today":
		return today, today
	if period == "yesterday":
		day = add_days(today, -1)
		return day, day
	if period == "this_week":
		return getdate(get_first_day_of_week(today)), today
	if period == "last_week":
		start = add_days(getdate(get_first_day_of_week(today)), -7)
		return start, add_days(start, 6)
	if period == "this_month":
		return get_first_day(today), today
	if period == "last_month":
		previous = add_months(today, -1)
		return get_first_day(previous), get_last_day(previous)
	if period == "this_quarter":
		return _quarter_start(today), today
	if period == "last_quarter":
		start = add_months(_quarter_start(today), -3)
		return start, add_days(add_months(start, 3), -1)
	if period == "this_year":
		return datetime.date(today.year, 1, 1), today
	if period == "last_year":
		return datetime.date(today.year - 1, 1, 1), datetime.date(today.year - 1, 12, 31)
	if period == "this_financial_year":
		start, end = _fiscal_year(today)
		return start, min(end, today)
	if period == "last_financial_year":
		start, _end = _fiscal_year(today)
		return _fiscal_year(add_days(start, -1))
	if period == "last_7_days":
		return add_days(today, -6), today
	if period == "last_30_days":
		return add_days(today, -29), today

	raise InvalidRequestError(detail=f"unhandled period {period!r}")


def resolve_range(date_range: dict) -> tuple[datetime.date, datetime.date, str]:
	"""Resolve {"period": ...} or {"from": ..., "to": ...}. Returns (from, to, label)."""
	if date_range.get("period"):
		start, end = resolve_period(date_range["period"])
		return start, end, normalize_period(date_range["period"])

	raw_from, raw_to = date_range.get("from"), date_range.get("to")
	if not raw_from and not raw_to:
		raise InvalidRequestError(detail="date_range needs a period or from/to")
	try:
		start = getdate(raw_from) if raw_from else datetime.date(1900, 1, 1)
		end = getdate(raw_to) if raw_to else getdate(frappe.utils.today())
	except Exception:
		raise InvalidRequestError(detail="invalid date in date_range") from None
	if start > end:
		start, end = end, start
	return start, end, "custom"


def _quarter_start(day: datetime.date) -> datetime.date:
	return datetime.date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def _fiscal_year(day: datetime.date) -> tuple[datetime.date, datetime.date]:
	"""ERPNext Fiscal Year when available, else the Indian April-March year."""
	if "erpnext" in frappe.get_installed_apps():
		try:
			from erpnext.accounts.utils import get_fiscal_year

			fy = get_fiscal_year(day, as_dict=True, raise_on_missing=False)
			if fy:
				return getdate(fy.year_start_date), getdate(fy.year_end_date)
		except Exception:
			pass
	start_year = day.year if day.month >= 4 else day.year - 1
	return datetime.date(start_year, 4, 1), datetime.date(start_year + 1, 3, 31)
