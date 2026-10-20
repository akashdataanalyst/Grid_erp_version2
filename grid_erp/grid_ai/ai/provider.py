"""Provider-independent LLM interface.

Every provider takes messages in one internal format (OpenAI-style chat messages:
system / user / assistant with `tool_calls` / tool) plus OpenAI-style tool definitions,
and returns an `LLMResponse`. The orchestrator only talks to this interface; all
provider-specific HTTP/SDK logic lives in `grid_erp.grid_ai.ai.providers.*`.

Assistant messages may carry a private `_native` key holding the provider's own
representation of that turn (e.g. Claude content blocks incl. thinking); providers that
do not understand it ignore it, and it is never stored or sent to the browser.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

import frappe

from grid_erp.grid_ai.exceptions import GridAIError

PROVIDER_CLASSES = {
	"OpenRouter": "grid_erp.grid_ai.ai.providers.openrouter.OpenRouterProvider",
	"Ollama": "grid_erp.grid_ai.ai.providers.ollama.OllamaProvider",
	"OpenAI": "grid_erp.grid_ai.ai.providers.openai.OpenAIProvider",
	"Claude": "grid_erp.grid_ai.ai.providers.claude.ClaudeProvider",
	"Gemini": "grid_erp.grid_ai.ai.providers.gemini.GeminiProvider",
}


class ProviderError(GridAIError):
	code = "provider_error"
	log_status = "Error"
	default_message = "AI service se jawab nahi mila. Thodi der baad try kariye."

	def __init__(self, user_message: str | None = None, *, detail: str | None = None, tools_unsupported=False):
		super().__init__(user_message, detail=detail)
		self.tools_unsupported = tools_unsupported


@dataclass
class ProviderConfig:
	provider: str
	base_url: str
	api_key: str | None
	model: str
	temperature: float
	max_tokens: int
	timeout: int
	proxy: str | None = None
	own_key: bool = False  # the user's own API key, not the company's
	user_choice: bool = False  # the user's own provider / model instead of the company default


@dataclass
class ToolCall:
	id: str
	name: str
	arguments: dict
	invalid: bool = False


@dataclass
class LLMResponse:
	content: str = ""
	tool_calls: list[ToolCall] = field(default_factory=list)
	finish_reason: str | None = None
	prompt_tokens: int = 0
	completion_tokens: int = 0
	native: object = None  # provider-specific assistant turn, echoed back via `_native`

	def assistant_message(self) -> dict:
		"""This turn as an internal-format assistant message (for the next tool round)."""
		message = {"role": "assistant", "content": self.content or ""}
		if self.tool_calls:
			message["tool_calls"] = [
				{
					"id": call.id,
					"type": "function",
					"function": {"name": call.name, "arguments": frappe.as_json(call.arguments, indent=None)},
				}
				for call in self.tool_calls
			]
		if self.native is not None:
			message["_native"] = self.native
		return message


TokenCallback = Callable[[str], None]


class BaseProvider(ABC):
	"""Common interface for all LLM providers."""

	name = "base"

	def __init__(self, config: ProviderConfig):
		self.config = config

	@abstractmethod
	def generate_response(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
		"""Return the complete response for a conversation."""

	def stream_response(
		self, messages: list[dict], tools: list[dict] | None = None, on_token: TokenCallback | None = None
	) -> LLMResponse:
		"""Stream text tokens to `on_token`, returning the complete response.

		Default: no real streaming, the whole answer is delivered as one chunk.
		"""
		response = self.generate_response(messages, tools)
		if on_token and response.content and not response.tool_calls:
			on_token(response.content)
		return response

	@abstractmethod
	def validate_connection(self) -> bool:
		"""Raise ProviderError if the provider is not usable with the current settings."""

	def list_models(self) -> list[str]:
		"""Model ids the provider offers with the current settings; [] if it cannot list them."""
		return []


def get_provider_config(user: str | None = None) -> ProviderConfig:
	"""The company default from AI Settings; with `user`, that user's own provider / model /
	key instead when they chose one and it is allowed (see grid_erp.grid_ai.ai.user_choice)."""
	settings = frappe.get_cached_doc("AI Settings")
	config = ProviderConfig(
		provider=settings.provider or "OpenRouter",
		base_url=(settings.api_base_url or "").rstrip("/"),
		api_key=settings.get_password("api_key", raise_exception=False) or None,
		model=(settings.model or "").strip(),
		temperature=settings.temperature if settings.temperature is not None else 0.1,
		max_tokens=settings.max_tokens or 1500,
		timeout=settings.timeout or 60,
		proxy=(settings.https_proxy or "").strip() or None,
	)
	if user:
		from grid_erp.grid_ai.ai.user_choice import apply

		config.user_choice = apply(config, settings, user)
	return config


def get_provider(config: ProviderConfig | None = None) -> BaseProvider:
	config = config or get_provider_config()
	path = PROVIDER_CLASSES.get(config.provider)
	if not path:
		raise ProviderError("AI provider configured nahi hai.", detail=f"unknown provider {config.provider}")
	if not config.model:
		raise ProviderError("AI model configured nahi hai. Administrator AI Settings check karein.")
	if not config.api_key and config.provider != "Ollama":
		raise ProviderError(
			"AI provider ki API key set nahi hai. AI Model settings ya Administrator se check karein.",
			detail=f"no api key for {config.provider}",
		)
	return frappe.get_attr(path)(config)


def clear_settings_cache():
	frappe.clear_document_cache("AI Settings", "AI Settings")
