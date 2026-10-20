"""Error classes used across the engine.

PermanentError: retrying cannot help (bad number, missing variable, rejected
template, wrong credentials, configuration error). The message fails at once.
TransientError: may succeed later (timeout, connection error, provider 5xx/429).
The dispatcher retries these according to WhatsApp Settings."""


class WhatsAppError(Exception):
	"""Base class. The message is shown to administrators in WhatsApp Notification Log."""

	retryable = False


class PermanentError(WhatsAppError):
	retryable = False


class TransientError(WhatsAppError):
	retryable = True


class ConfigurationError(PermanentError):
	"""Rule, template, instance or provider is missing, disabled or incomplete."""


class ConditionError(PermanentError):
	"""Condition expression is invalid or could not be evaluated."""


class VariableError(PermanentError):
	"""A required template variable has no value."""


class InvalidRecipientError(PermanentError):
	"""Mobile number is missing or not a valid number."""


class AuthenticationError(PermanentError):
	"""Provider rejected the credentials."""


class TemplateRejectedError(PermanentError):
	"""Provider rejected the template (not approved, wrong parameters, wrong language)."""


class ProviderError(PermanentError):
	"""Provider returned a client error that will not change on retry."""


class ProviderUnavailableError(TransientError):
	"""Provider returned a server error or rate limit."""


class ProviderTimeoutError(TransientError):
	"""Provider did not answer in time."""
