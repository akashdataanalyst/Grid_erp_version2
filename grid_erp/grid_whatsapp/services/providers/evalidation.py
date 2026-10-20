"""eValidation WhatsApp provider.

All eValidation-specific code lives in this file. The rest of the app talks to
it only through WhatsAppProvider.send() / parse_status_callback().

TODO (needs the eValidation API documentation)
----------------------------------------------
The request and response formats below are intentionally NOT implemented,
because they must come from eValidation's documentation, not be guessed.
Fill in the three methods marked TODO:

1. build_send_request(message)  -> the HTTP request for one template message
     - endpoint path (appended to WhatsApp Instance > API Base URL)
     - authentication: which header/param carries api_key / api_token
     - where instance_id and sender_number go, if the API needs them
     - recipient number format (+919876543210 or 919876543210)
     - template reference (name or id), language code
     - template parameters: named (message.variables) or positional (message.parameters)

2. parse_send_response(status_code, body) -> ProviderResult
     - which field holds the message id (store it so delivery callbacks can find the log)
     - which response codes mean: invalid number (raise InvalidRecipientError),
       template not approved/rejected (raise TemplateRejectedError),
       other permanent errors (raise ProviderError).
       HTTP 401/403, 429 and 5xx are already handled by http_request().

3. parse_status_callback(payload) -> list[StatusUpdate]   (optional)
     - only if eValidation can call a webhook with delivered/read receipts.
       The webhook URL to give eValidation is:
       https://<site>/api/method/grid_erp.grid_whatsapp.api.webhook.status_callback?instance=<WhatsApp Instance>
       with the instance's Webhook Secret in the X-Webhook-Secret header.

Until then every send fails with a clear "not configured" error (no retries),
and Test Mode in WhatsApp Settings can be used to exercise rules end to end.
"""

from frappe import _

from grid_erp.grid_whatsapp.services.errors import ConfigurationError
from grid_erp.grid_whatsapp.services.whatsapp_provider import (
	OutgoingMessage,
	ProviderResult,
	StatusUpdate,
	WhatsAppProvider,
)

NOT_CONFIGURED = (
	"The eValidation API request format has not been configured yet. "
	"A developer must implement {0} in grid_erp/grid_whatsapp/services/providers/evalidation.py "
	"from the eValidation API documentation. Use Test Mode in WhatsApp Settings meanwhile."
)


class EValidationProvider(WhatsAppProvider):
	def send(self, message: OutgoingMessage) -> ProviderResult:
		request = self.build_send_request(message)
		status, body = self.http_request(
			request.get("method", "POST"),
			self.url(request.get("path", "")),
			headers=request.get("headers"),
			params=request.get("params"),
			json=request.get("json"),
			data=request.get("data"),
		)
		return self.parse_send_response(status, body)

	def build_send_request(self, message: OutgoingMessage) -> dict:
		"""TODO: return {"method": ..., "path": ..., "headers": {...}, "json": {...}}.

		Available inputs:
		    self.instance.api_key / api_token / instance_id / sender_number
		    message.recipient, message.template_name, message.template_id,
		    message.language, message.variables (dict), message.parameters (list),
		    message.body (rendered text), message.reference (unique per message)
		"""
		raise ConfigurationError(_(NOT_CONFIGURED).format("EValidationProvider.build_send_request"))

	def parse_send_response(self, status_code: int, body) -> ProviderResult:
		"""TODO: return ProviderResult(message_id=..., response=body) or raise an error class."""
		raise ConfigurationError(_(NOT_CONFIGURED).format("EValidationProvider.parse_send_response"))

	def parse_status_callback(self, payload: dict) -> list[StatusUpdate]:
		"""TODO (optional): map an eValidation delivery callback to StatusUpdate objects."""
		raise ConfigurationError(_(NOT_CONFIGURED).format("EValidationProvider.parse_status_callback"))
