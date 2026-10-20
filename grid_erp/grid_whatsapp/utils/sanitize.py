"""Keep credentials out of WhatsApp Notification Log.

Provider responses and error texts are stored for troubleshooting. Before they
are saved, values under secret-looking keys are masked, known secret values
(the instance's API key/token) are replaced wherever they appear, and the text
is truncated."""

import json
import re

MASK = "********"
MAX_LENGTH = 10_000
_SECRET_KEY = re.compile(r"(pass(word)?|secret|token|api[_-]?key|authori[sz]ation|credential|signature)", re.I)


def redact(value, secrets=()):
	"""Return a JSON-safe copy of value with secrets masked."""
	secrets = [s for s in secrets if s and len(str(s)) >= 4]
	return _redact(value, secrets)


def _redact(value, secrets):
	if isinstance(value, dict):
		return {
			key: MASK if _SECRET_KEY.search(str(key)) and item not in (None, "") else _redact(item, secrets)
			for key, item in value.items()
		}
	if isinstance(value, list | tuple):
		return [_redact(item, secrets) for item in value]
	if isinstance(value, str):
		for secret in secrets:
			value = value.replace(str(secret), MASK)
		return value
	if value is None or isinstance(value, bool | int | float):
		return value
	return _redact(str(value), secrets)


def to_log_text(value, secrets=()):
	"""Redacted, truncated text for a log field."""
	if value in (None, ""):
		return ""
	value = redact(value, secrets)
	text = value if isinstance(value, str) else json.dumps(value, indent=1, default=str, ensure_ascii=False)
	if len(text) > MAX_LENGTH:
		text = text[:MAX_LENGTH] + "\n... (truncated)"
	return text
