from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cstr, flt, now_datetime
from frappe.utils.password import passlibctx


def generate_secret(length: int = 32) -> str:
	return frappe.generate_hash(length=length)


def hash_secret(secret: str) -> str:
	return passlibctx.hash(secret)


def verify_secret(secret: str, secret_hash: str | None) -> bool:
	if not secret_hash:
		return False
	try:
		return passlibctx.verify(secret, secret_hash)
	except Exception:
		return False


def retry_on_conflict(fn, attempts: int = 6):
	"""Run fn, retrying on MariaDB write conflicts.

	The candidate portal sends overlapping requests (heartbeat, violations, uploads) for one
	Assessment Attempt; MariaDB rejects a stale write with 1020 / deadlock. Each retry starts a
	fresh transaction, so fn must re-read what it needs.
	"""
	import random
	import time

	from frappe.exceptions import QueryDeadlockError

	for attempt_no in range(1, attempts + 1):
		try:
			return fn()
		except QueryDeadlockError:
			if attempt_no == attempts:
				raise
			frappe.db.rollback()
			# Jitter so parallel requests don't collide again on the same beat.
			time.sleep(random.uniform(0.05, 0.25) * attempt_no)


def set_job_applicant_values(job_applicant: str, values: dict):
	"""Mirror assessment progress onto Job Applicant, skipping display-only fields (HTML etc.) with no DB column."""
	from frappe.model import no_value_fields

	meta = frappe.get_meta("Job Applicant")
	values = {
		fieldname: value
		for fieldname, value in values.items()
		if (df := meta.get_field(fieldname)) and df.fieldtype not in no_value_fields
	}
	if values:
		frappe.db.set_value("Job Applicant", job_applicant, values, update_modified=False)


def utcnow():
	return now_datetime()


def days_from_now(days: int):
	return add_days(now_datetime(), days)


def minutes_from_now(minutes: int):
	return add_to_date(now_datetime(), minutes=minutes)


def json_loads(value: Any, default=None):
	if not value:
		return default if default is not None else []
	if isinstance(value, (list, dict)):
		return value
	try:
		return frappe.parse_json(value)
	except Exception:
		return default if default is not None else []


def get_doc_link(doctype: str, name: str) -> str:
	return frappe.utils.get_link_to_form(doctype, name)


def clamp_score(score: float, negative_marking: float | None = None) -> float:
	score = flt(score)
	if negative_marking:
		return max(score, 0)
	return score


def safe_text(value: Any) -> str:
	return cstr(value or "").strip()


@dataclass
class CandidateSession:
	assignment: str
	contact: str | None
	token: str

