"""Provider abstraction: the only boundary between the engine and a WhatsApp API.

The engine builds an OutgoingMessage and calls provider.send(); it never knows
which API is behind it. A provider class:

    class MyProvider(WhatsAppProvider):
        def send(self, message: OutgoingMessage) -> ProviderResult: ...
        def parse_status_callback(self, payload: dict) -> list[StatusUpdate]: ...   # optional

is registered in an app's hooks.py:

    whatsapp_providers = {"My Provider": "my_app.whatsapp.MyProvider"}

and then appears as WhatsApp Instance > Provider (add the option with a
Property Setter / Customize Form when the provider lives in another app).

Errors: raise the classes in grid_erp.grid_whatsapp.services.errors. http_request()
already maps timeouts, connection errors and HTTP status codes to them, which
decides whether the dispatcher retries."""

from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _

from grid_erp.grid_whatsapp.services.errors import (
	AuthenticationError,
	ConfigurationError,
	ProviderError,
	ProviderTimeoutError,
	ProviderUnavailableError,
)


@dataclass
class InstanceConfig:
	"""A WhatsApp Instance with its secrets decrypted, for provider use only."""

	name: str
	provider: str
	api_base_url: str | None = None
	api_key: str | None = None
	api_token: str | None = None
	webhook_secret: str | None = None
	instance_id: str | None = None
	sender_number: str | None = None
	timeout: int = 30
	verify_ssl: bool = True

	@property
	def secrets(self):
		"""Values that must never appear in logs."""
		return [s for s in (self.api_key, self.api_token, self.webhook_secret) if s]


@dataclass
class OutgoingMessage:
	recipient: str  # +<country code><number>
	body: str  # rendered text
	variables: dict  # variable name -> text, in template order
	template_name: str | None = None  # provider's template name
	template_id: str | None = None  # provider's template id
	language: str | None = None
	reference: str | None = None  # WhatsApp Notification Log name; unique per message

	@property
	def parameters(self):
		"""Variable values in template order, for APIs with positional parameters ({{1}}, {{2}})."""
		return list(self.variables.values())


@dataclass
class ProviderResult:
	message_id: str | None = None
	request_reference: str | None = None
	response: Any = None  # provider response as returned (redacted before it is logged)
	status: str = "Sent"  # or "Delivered" if the API confirms delivery synchronously


@dataclass
class StatusUpdate:
	"""A delivery receipt from a provider callback."""

	message_id: str
	status: str  # Sent / Delivered / Read / Failed
	timestamp: Any = None
	error: str | None = None
	raw: dict = field(default_factory=dict)


class WhatsAppProvider:
	def __init__(self, instance: InstanceConfig):
		self.instance = instance

	def send(self, message: OutgoingMessage) -> ProviderResult:
		raise NotImplementedError

	def parse_status_callback(self, payload: dict) -> list[StatusUpdate]:
		raise ConfigurationError(_("Provider {0} does not support status callbacks").format(self.instance.provider))

	# -- helpers for HTTP based providers -----------------------------------

	def url(self, path=""):
		base = (self.instance.api_base_url or "").rstrip("/")
		if not base:
			raise ConfigurationError(_("WhatsApp Instance {0} has no API Base URL").format(self.instance.name))
		return f"{base}/{path.lstrip('/')}" if path else base

	def http_request(self, method, url, *, headers=None, params=None, json=None, data=None):
		"""Send a request and return (status_code, parsed body). Raises the engine's
		error classes so retries and log messages are consistent across providers."""
		import requests

		try:
			response = requests.request(
				method,
				url,
				headers=headers,
				params=params,
				json=json,
				data=data,
				timeout=self.instance.timeout or 30,
				verify=bool(self.instance.verify_ssl),
			)
		except requests.Timeout:
			raise ProviderTimeoutError(
				_("{0} did not answer within {1} seconds").format(self.instance.provider, self.instance.timeout)
			) from None
		except requests.ConnectionError as e:
			raise ProviderUnavailableError(_("Could not connect to {0}: {1}").format(self.instance.provider, e)) from None
		except requests.RequestException as e:
			raise ProviderError(_("Request to {0} failed: {1}").format(self.instance.provider, e)) from None

		body = _parse_body(response)
		status = response.status_code
		if status in (401, 403):
			raise AuthenticationError(
				_("{0} rejected the credentials (HTTP {1}): {2}").format(self.instance.provider, status, _short(body))
			)
		if status == 429 or status >= 500:
			raise ProviderUnavailableError(
				_("{0} is unavailable (HTTP {1}): {2}").format(self.instance.provider, status, _short(body))
			)
		if status >= 400:
			raise ProviderError(_("{0} API error (HTTP {1}): {2}").format(self.instance.provider, status, _short(body)))
		return status, body


def _parse_body(response):
	try:
		return response.json()
	except ValueError:
		return response.text


def _short(body, length=500):
	text = body if isinstance(body, str) else frappe.as_json(body, indent=None)
	return text[:length]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def registered_providers():
	"""Provider name -> dotted class path, from every installed app's hooks."""
	providers = {}
	for name, paths in (frappe.get_hooks("whatsapp_providers") or {}).items():
		providers[name] = paths[-1] if isinstance(paths, list) else paths
	return providers


def load_instance(name):
	"""InstanceConfig for an active instance, or ConfigurationError."""
	if not name:
		raise ConfigurationError(_("No WhatsApp Instance selected and no default instance in WhatsApp Settings"))
	if not frappe.db.exists("WhatsApp Instance", name):
		raise ConfigurationError(_("WhatsApp Instance {0} not found").format(name))
	doc = frappe.get_doc("WhatsApp Instance", name)
	if not doc.active:
		raise ConfigurationError(_("WhatsApp Instance {0} is inactive").format(name))
	return InstanceConfig(
		name=doc.name,
		provider=doc.provider,
		api_base_url=doc.api_base_url,
		api_key=doc.get_password("api_key", raise_exception=False),
		api_token=doc.get_password("api_token", raise_exception=False),
		webhook_secret=doc.get_password("webhook_secret", raise_exception=False),
		instance_id=doc.instance_id,
		sender_number=doc.sender_number,
		timeout=doc.timeout or 30,
		verify_ssl=bool(doc.verify_ssl),
	)


def get_provider(instance: InstanceConfig) -> WhatsAppProvider:
	path = registered_providers().get(instance.provider)
	if not path:
		raise ConfigurationError(
			_("No provider named {0} is installed (WhatsApp Instance {1})").format(instance.provider, instance.name)
		)
	return frappe.get_attr(path)(instance)
