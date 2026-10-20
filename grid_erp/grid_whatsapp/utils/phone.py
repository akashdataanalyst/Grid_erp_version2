"""Phone number normalisation, independent of any one country.

normalize("+91 98765-43210")      -> "+919876543210"
normalize("098765 43210", "91")   -> "+919876543210"   (trunk 0 dropped, code added)
normalize("0044 20 7946 0958")    -> "+442079460958"   (00 international prefix)
normalize("919876543210", "91")   -> "+919876543210"   (already carries the code)
"""

import re

from grid_erp.grid_whatsapp.services.errors import InvalidRecipientError

# E.164 allows at most 15 digits; real subscriber numbers have at least 7-8.
MIN_DIGITS = 8
MAX_DIGITS = 15
_SEPARATORS = re.compile(r"[\s\-().\/]")


def normalize(number, default_country_code=None):
	"""Return the number as +<country code><subscriber number>, or raise InvalidRecipientError."""
	original = number
	number = _SEPARATORS.sub("", str(number or "").strip())
	if not number:
		raise InvalidRecipientError("Recipient mobile number is empty")

	if number.startswith("+"):
		digits = number[1:]
	elif number.startswith("00"):
		digits = number[2:]
	else:
		digits = _with_country_code(number, _clean_code(default_country_code), original)

	if not digits.isdigit():
		raise InvalidRecipientError(f"Invalid mobile number {original!r}: only digits, spaces, +, -, ( and ) are allowed")
	if not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
		raise InvalidRecipientError(
			f"Invalid mobile number {original!r}: expected {MIN_DIGITS}-{MAX_DIGITS} digits including the country code"
		)
	return "+" + digits


def _with_country_code(number, code, original):
	if not number.isdigit():
		raise InvalidRecipientError(f"Invalid mobile number {original!r}")
	if not code:
		raise InvalidRecipientError(
			f"Mobile number {original!r} has no country code. Write it as +<country code><number> "
			"or set Default Country Code in WhatsApp Settings."
		)
	# Already carries the country code (e.g. 919876543210 with code 91). Requiring the
	# result to be longer than 10 digits keeps 10-digit national numbers that happen to
	# start with the code digits (e.g. Indian 91xxxxxxxx) from being misread.
	if number.startswith(code) and len(number) > 10 and len(number) - len(code) >= MIN_DIGITS - 1:
		return number
	# National trunk prefix ("0" in most countries) is dropped before adding the code.
	return code + number.lstrip("0")


def _clean_code(code):
	return re.sub(r"\D", "", str(code or ""))


def is_valid(number, default_country_code=None):
	try:
		normalize(number, default_country_code)
		return True
	except InvalidRecipientError:
		return False
