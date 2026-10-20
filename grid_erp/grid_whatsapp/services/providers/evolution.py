"""Evolution API (v2) WhatsApp provider.

Same request the Google Apps Script sends:

    POST {API Base URL}/message/sendText/{Instance ID}
    header  apikey: {API Key}
    body    {"number": "919876543210", "text": "..."}

WhatsApp Instance fields used:
    API Base URL  e.g. http://<evolution-server>:8081
    Instance ID   the Evolution instance name, e.g. <instance-name>
    API Key       Evolution API key (sent in the "apikey" header)

Evolution sends plain text, so the rendered WhatsApp Template body is sent as is;
Provider Template Name / ID are not needed.

Delivery receipts (optional): in Evolution, set the instance webhook to
    https://<site>/api/method/grid_erp.grid_whatsapp.api.webhook.status_callback?instance=<WhatsApp Instance>
with header X-Webhook-Secret: <Webhook Secret> and the MESSAGES_UPDATE event."""

from grid_erp.grid_whatsapp.services.errors import ConfigurationError, InvalidRecipientError, ProviderError
from grid_erp.grid_whatsapp.services.whatsapp_provider import (
	OutgoingMessage,
	ProviderResult,
	StatusUpdate,
	WhatsAppProvider,
)

# Evolution / Baileys message status -> log status
STATUS_MAP = {
	"SERVER_ACK": "Sent",
	"DELIVERY_ACK": "Delivered",
	"READ": "Read",
	"PLAYED": "Read",
	"ERROR": "Failed",
}


class EvolutionProvider(WhatsAppProvider):
	def send(self, message: OutgoingMessage) -> ProviderResult:
		if not self.instance.instance_id:
			raise ConfigurationError(f"WhatsApp Instance {self.instance.name} has no Instance ID (Evolution instance name)")
		if not self.instance.api_key:
			raise ConfigurationError(f"WhatsApp Instance {self.instance.name} has no API Key")

		_status, body = self.http_request(
			"POST",
			self.url(f"message/sendText/{self.instance.instance_id}"),
			headers={"apikey": self.instance.api_key, "Content-Type": "application/json"},
			json={"number": message.recipient.lstrip("+"), "text": message.body},
		)
		if not isinstance(body, dict):
			raise ProviderError(f"Evolution API returned an unexpected response: {str(body)[:300]}")

		key = body.get("key") or {}
		message_id = key.get("id")
		if not message_id:
			if "exists" in str(body).lower():
				raise InvalidRecipientError(f"Number {message.recipient} is not on WhatsApp")
			raise ProviderError(f"Evolution API did not return a message id: {str(body)[:300]}")
		return ProviderResult(message_id=message_id, request_reference=key.get("remoteJid"), response=body)

	def parse_status_callback(self, payload: dict) -> list[StatusUpdate]:
		if (payload.get("event") or "").lower().replace("_", ".") != "messages.update":
			return []
		items = payload.get("data")
		items = items if isinstance(items, list) else [items or {}]
		updates = []
		for item in items:
			message_id = item.get("keyId") or (item.get("key") or {}).get("id")
			status = STATUS_MAP.get(str(item.get("status") or "").upper())
			if message_id and status:
				updates.append(StatusUpdate(message_id=message_id, status=status, raw=item))
		return updates
