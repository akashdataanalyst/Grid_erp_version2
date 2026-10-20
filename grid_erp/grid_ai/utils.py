"""Shared helpers: audit logging, rate limiting, housekeeping."""

import frappe
from frappe.utils import add_days, now_datetime

from grid_erp.grid_ai.exceptions import RateLimitError, TokenLimitError


def log_request(**values):
	"""Write an AI Query Log row. Only metadata: never prompts, API keys or ERP values.

	Logging failures must never break the user's request.
	"""
	try:
		log = frappe.new_doc("AI Query Log")
		log.update({k: v for k, v in values.items() if v is not None})
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(title="Grid AI: could not write query log")


def check_rate_limit(user: str, per_minute: int, per_day: int):
	"""Per-user request limits, counted in Redis. Raises RateLimitError when exceeded."""
	now = now_datetime()
	windows = (
		(f"grid_ai:rl:m:{user}:{now:%Y%m%d%H%M}", per_minute, 120),
		(f"grid_ai:rl:d:{user}:{now:%Y%m%d}", per_day, 172800),
	)
	for key, limit, ttl in windows:
		if not limit:
			continue
		redis_key = frappe.cache.make_key(key)
		count = frappe.cache.incrby(redis_key, 1)
		if count == 1:
			frappe.cache.expire(redis_key, ttl)
		if count > limit:
			raise RateLimitError()


def _token_key(user: str) -> str:
	return frappe.cache.make_key(f"grid_ai:tok:d:{user}:{now_datetime():%Y%m%d}")


def check_token_budget(user: str, per_day: int):
	"""Raise TokenLimitError once the user has used `per_day` tokens today (0 = no limit)."""
	if per_day and int(frappe.cache.get(_token_key(user)) or 0) >= per_day:
		raise TokenLimitError()


def add_token_usage(user: str, tokens: int):
	if tokens <= 0:
		return
	key = _token_key(user)
	if frappe.cache.incrby(key, tokens) == tokens:
		frappe.cache.expire(key, 172800)


def cleanup_old_logs():
	"""Daily job: delete AI Query Log rows older than the configured retention."""
	days = frappe.db.get_single_value("AI Settings", "log_retention_days") or 90
	frappe.db.delete("AI Query Log", {"creation": ("<", add_days(now_datetime(), -days))})
	frappe.db.commit()
