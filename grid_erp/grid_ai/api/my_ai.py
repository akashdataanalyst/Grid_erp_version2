"""The user's own AI provider / model / key ("AI Model" on the AI Assistant page).

Every endpoint acts on `frappe.session.user` only. A saved API key is never returned: the page
only learns whether one exists. See grid_erp.grid_ai.ai.user_choice for the rules.
"""

import frappe
from frappe import _

from grid_erp.grid_ai.ai.provider import ProviderError, get_provider, get_provider_config
from grid_erp.grid_ai.ai.user_choice import (
	POLICY_FREE,
	USER_PROVIDERS,
	company_key_allowed,
	get_own_key,
	get_saved_choice,
	why_not_allowed,
)
from grid_erp.grid_ai.api.chat import _ensure_access
from grid_erp.grid_ai.exceptions import GridAIError
from grid_erp.grid_ai.doctype.ai_settings.ai_settings import (
	DEFAULT_BASE_URLS,
	check_model,
	combine_model_options,
	fetch_live_models,
	live_models,
)
from grid_erp.grid_ai.tools.permissions import current_user
from grid_erp.grid_ai.utils import check_rate_limit

KEY_PAGES = {
	"OpenRouter": "https://openrouter.ai/keys",
	"OpenAI": "https://platform.openai.com/api-keys",
	"Claude": "https://console.anthropic.com/settings/keys",
	"Gemini": "https://aistudio.google.com/apikey",
}
MAX_KEY_LENGTH = 512


@frappe.whitelist()
def get_my_ai_settings() -> dict:
	"""Everything the AI Model dialog shows."""
	_ensure_access()
	user = current_user()
	settings = frappe.get_cached_doc("AI Settings")
	choice = get_saved_choice(user) or frappe._dict()
	has_key = bool(choice and get_own_key(user))
	effective = get_provider_config(user)

	notice = None
	if choice.get("use_own") and not effective.user_choice:
		notice = why_not_allowed(settings, choice.provider, choice.model, has_key) or _(
			"Aapki choice abhi use nahi ho pa rahi, isliye company default chal raha hai."
		)

	return {
		"allowed": bool(settings.allow_user_provider),
		"company": {"provider": settings.provider, "model": settings.model},
		"company_key_policy": settings.company_key_models or POLICY_FREE,
		"mine": {
			"use_own": bool(choice.get("use_own")),
			"provider": choice.get("provider") or "",
			"model": choice.get("model") or "",
			"has_key": has_key,
		},
		"effective": {"provider": effective.provider, "model": effective.model, "own": effective.user_choice},
		"notice": notice,
		"providers": list(USER_PROVIDERS),
		"key_pages": KEY_PAGES,
	}


@frappe.whitelist()
def get_my_model_options(provider: str) -> dict:
	"""Models for the dialog's dropdown. The live list comes from the user's own key when they
	saved one for this provider, else from the company key (company provider only)."""
	_ensure_access()
	if provider not in USER_PROVIDERS:
		return {"models": [], "default": "", "live": False}
	user = current_user()
	settings = frappe.get_cached_doc("AI Settings")
	choice = get_saved_choice(user)

	live = []
	own_key = get_own_key(user) if choice and choice.provider == provider else None
	if own_key:
		config = get_provider_config()
		config.provider, config.api_key = provider, own_key
		config.base_url = DEFAULT_BASE_URLS[provider].rstrip("/")
		live = fetch_live_models(config, f"grid_ai:live_models:user:{user}:{provider}")
	elif provider == settings.provider:
		live = live_models(provider)

	options = combine_model_options(provider, live)
	if provider == settings.provider:
		# Default to the company model, and say which models run without the user's own key.
		options["default"] = settings.model
		if settings.model not in options["models"]:
			options["models"].insert(0, settings.model)
		options["company_key_models"] = [m for m in options["models"] if company_key_allowed(settings, provider, m)]
	return options


@frappe.whitelist(methods=["POST"])
def save_my_ai_settings(
	use_own: int | str = 0,
	provider: str | None = None,
	model: str | None = None,
	api_key: str | None = None,
	remove_key: int | str = 0,
) -> dict:
	"""Save the user's choice. With use_own = 0 the company default is used (the choice and
	key stay saved, so switching back is one click)."""
	_ensure_access()
	user = current_user()
	settings = frappe.get_cached_doc("AI Settings")
	use_own = frappe.utils.cint(use_own)
	api_key = (api_key or "").strip()
	if len(api_key) > MAX_KEY_LENGTH:
		frappe.throw(_("API key bahut lambi hai."))

	if frappe.db.exists("AI User Settings", user):
		doc = frappe.get_doc("AI User Settings", user)
	else:
		doc = frappe.new_doc("AI User Settings")
		doc.user = user

	if use_own:
		if not settings.allow_user_provider:
			frappe.throw(_("Administrator ne apna provider chunne ka option band rakha hai."))
		if provider not in USER_PROVIDERS:
			frappe.throw(_("Provider chuniye: {0}").format(", ".join(USER_PROVIDERS)))
		model = check_model(provider, model)

		old_key = get_own_key(user) if not doc.is_new() else None
		if frappe.utils.cint(remove_key) or (doc.provider and doc.provider != provider and not api_key):
			old_key = None  # a key belongs to one provider: switching provider needs that provider's key
		key = api_key or old_key

		reason = why_not_allowed(settings, provider, model, bool(key))
		if reason:
			frappe.throw(reason)

		doc.provider = provider
		doc.model = model
		doc.api_key = key or ""

	doc.use_own = use_own
	doc.flags.ignore_permissions = True
	doc.save() if not doc.is_new() else doc.insert()
	return get_my_ai_settings()


@frappe.whitelist(methods=["POST"])
def test_my_connection() -> dict:
	"""Try the provider / model / key this user's questions will use."""
	_ensure_access()
	user = current_user()
	settings = frappe.get_cached_doc("AI Settings")
	try:
		check_rate_limit(user, settings.rate_limit_per_minute or 10, settings.rate_limit_per_day or 300)
		config = get_provider_config(user)
		get_provider(config).validate_connection()
	except ProviderError as e:
		return {"ok": False, "message": e.user_message}
	except GridAIError as e:
		return {"ok": False, "message": e.user_message}
	except Exception:
		frappe.log_error(title="Grid AI: user connection test failed")
		return {"ok": False, "message": _("Connection test fail hua. Administrator Error Log dekh sakte hain.")}
	return {"ok": True, "message": _("Connection sahi hai: {0} · {1}").format(config.provider, config.model)}
