# Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
# For license information, please see license.txt

from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document

DEFAULT_BASE_URLS = {
	"OpenRouter": "https://openrouter.ai/api/v1",
	"Ollama": "http://localhost:11434/v1",
	"OpenAI": "https://api.openai.com/v1",
	"Claude": "https://api.anthropic.com/v1",
	"Gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

# Suggestions for the Model dropdown; the first one is the provider's default. Once the
# settings are saved with an API key, the provider's live /models list is shown too.
# Ollama has no fixed list: its models are whatever is pulled on the server.
MODEL_OPTIONS = {
	"OpenRouter": [
		"openrouter/free",
		"openrouter/auto",
		"openai/gpt-4o-mini",
		"google/gemini-2.5-flash",
		"meta-llama/llama-3.3-70b-instruct",
		"deepseek/deepseek-chat",
	],
	"Ollama": [],
	"OpenAI": ["gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-5", "gpt-5-mini", "o4-mini"],
	"Claude": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"],
	"Gemini": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"],
}

DEFAULT_MODELS = {provider: models[0] for provider, models in MODEL_OPTIONS.items() if models}

# Providers that take bare model ids; "vendor/model" ids belong to OpenRouter.
BARE_ID_PROVIDERS = ("OpenAI", "Claude", "Gemini")
LIVE_MODELS_CACHE_KEY = "grid_ai:live_models"
LIVE_MODELS_TTL = 3600

# (fieldname, minimum, maximum)
NUMERIC_BOUNDS = (
	("max_tokens", 64, 32000),
	("timeout", 5, 300),
	("max_tool_rounds", 1, 8),
	("max_conversation_messages", 2, 50),
	("max_message_length", 100, 10000),
	("rate_limit_per_minute", 1, 1000),
	("rate_limit_per_day", 1, 100000),
	("token_limit_per_user_per_day", 1000, 100000000),
	("log_retention_days", 1, 3650),
)


class AISettings(Document):
	def validate(self):
		if not self.api_base_url:
			self.api_base_url = DEFAULT_BASE_URLS.get(self.provider)
		self.api_base_url = (self.api_base_url or "").strip().rstrip("/")

		parsed = urlparse(self.api_base_url)
		if parsed.scheme not in ("http", "https") or not parsed.netloc:
			frappe.throw(_("API URL must be a valid http(s) URL."))

		self.https_proxy = (self.https_proxy or "").strip()
		if self.https_proxy:
			proxy = urlparse(self.https_proxy)
			if proxy.scheme not in ("http", "https") or not proxy.hostname or not proxy.port:
				frappe.throw(_("HTTPS Proxy must look like http://host:port"))

		self.validate_model()

		if self.temperature is not None and not (0 <= self.temperature <= 2):
			frappe.throw(_("Temperature must be between 0 and 2."))

		for fieldname, low, high in NUMERIC_BOUNDS:
			value = self.get(fieldname)
			if value and not (low <= value <= high):
				frappe.throw(
					_("{0} must be between {1} and {2}.").format(self.meta.get_label(fieldname), low, high)
				)

	def validate_model(self):
		self.model = check_model(self.provider, self.model)

	def on_update(self):
		from grid_erp.grid_ai.ai.provider import clear_settings_cache

		clear_settings_cache()
		frappe.cache.delete_keys(LIVE_MODELS_CACHE_KEY)


def check_model(provider: str, model: str | None) -> str:
	"""The cleaned model id. Throws if it is clearly a model of another provider."""
	model = (model or "").strip()
	if provider == "Gemini":
		model = model.removeprefix("models/")

	if model not in MODEL_OPTIONS.get(provider, []):
		for other, models in MODEL_OPTIONS.items():
			if other != provider and model in models:
				frappe.throw(
					_("Model {0} is a {1} model. Pick a {2} model from the list.").format(
						frappe.bold(model), other, provider
					)
				)

	if provider in BARE_ID_PROVIDERS and "/" in model:
		frappe.throw(
			_("Model {0} looks like an OpenRouter model id. Pick a {1} model from the list.").format(
				frappe.bold(model), provider
			)
		)
	if provider == "Claude" and not model.startswith("claude-"):
		frappe.throw(_("Claude model ids start with claude-, e.g. {0}.").format(DEFAULT_MODELS["Claude"]))
	return model


def is_free_model(provider: str, model: str) -> bool:
	"""Models that cost nothing on the provider (OpenRouter's free router and ':free' variants)."""
	return provider == "OpenRouter" and (model == "openrouter/free" or model.endswith(":free"))


def live_models(provider: str) -> list[str]:
	"""The saved provider's own model list, cached for an hour. [] if it cannot be fetched
	(not saved yet, no API key, provider down); the dropdown then shows MODEL_OPTIONS."""
	from grid_erp.grid_ai.ai.provider import get_provider_config

	config = get_provider_config()
	if config.provider != provider:
		return []
	return fetch_live_models(config, f"{LIVE_MODELS_CACHE_KEY}:{provider}")


def fetch_live_models(config, cache_key: str) -> list[str]:
	"""Model list of the provider in `config`, cached under `cache_key` for an hour."""
	from grid_erp.grid_ai.ai.provider import PROVIDER_CLASSES, ProviderError

	if config.provider not in PROVIDER_CLASSES:
		return []
	cached = frappe.cache.get_value(cache_key)
	if cached is not None:
		return cached

	config.timeout = min(config.timeout, 20)
	try:
		models = frappe.get_attr(PROVIDER_CLASSES[config.provider])(config).list_models()
	except ProviderError:
		return []
	except Exception:
		frappe.log_error(title="Grid AI: model list failed")
		return []

	frappe.cache.set_value(cache_key, models, expires_in_sec=LIVE_MODELS_TTL)
	return models


def combine_model_options(provider: str, live: list[str]) -> dict:
	"""Suggestions the provider also lists live first, then the rest of its live list.
	Without a live list, the suggestions alone."""
	suggested = MODEL_OPTIONS.get(provider, [])
	if live:
		models = [m for m in suggested if m in live] + [m for m in live if m not in suggested]
	else:
		models = list(suggested)

	default = DEFAULT_MODELS.get(provider)
	if default and default not in models:
		models.insert(0, default)
	return {"models": models, "default": default or (models[0] if models else ""), "live": bool(live)}


@frappe.whitelist()
def test_connection() -> dict:
	"""Check that the configured provider is reachable. System Manager only."""
	frappe.only_for("System Manager")

	from grid_erp.grid_ai.ai.provider import ProviderError, get_provider

	try:
		get_provider().validate_connection()
	except ProviderError as e:
		return {"ok": False, "message": e.user_message}
	except Exception:
		frappe.log_error(title="Grid AI: connection test failed")
		return {"ok": False, "message": _("Connection failed. See Error Log for details.")}

	return {"ok": True, "message": _("Connection successful.")}


@frappe.whitelist()
def get_provider_defaults(provider: str) -> dict:
	frappe.only_for("System Manager")
	return {
		"api_base_url": DEFAULT_BASE_URLS.get(provider, ""),
		"model": DEFAULT_MODELS.get(provider, ""),
	}


@frappe.whitelist()
def get_model_options(provider: str) -> dict:
	"""Models for the Model dropdown of AI Settings."""
	frappe.only_for("System Manager")
	return combine_model_options(provider, live_models(provider))
