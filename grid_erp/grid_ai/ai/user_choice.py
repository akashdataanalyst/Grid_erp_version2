"""A user's own AI provider / model / API key on top of the company default.

AI Settings holds the company default (provider, model, key) that every user gets. If AI
Settings allows it, a user can pick another provider and model on the AI Assistant page
(stored in AI User Settings). Which API key is then used:

    the user's own key                       -> always allowed
    no own key, same provider as the company -> the company key, if "Other Models on Company Key"
                                                allows that model (free models only / any / not allowed)
    no own key, another provider             -> not possible: a key is needed

Users can only use the providers' official API URLs (no custom URL: a user must not be able
to make the server call arbitrary addresses), and the company's proxy and limits still apply.
If a saved choice stops being allowed (the admin changed the rules), the company default is
used and the AI Model dialog shows that.
"""

import frappe
from frappe import _

USER_PROVIDERS = ("OpenRouter", "OpenAI", "Claude", "Gemini")
POLICY_FREE = "Free models only"
POLICY_ANY = "Any model"
POLICY_NONE = "Not allowed"


def get_saved_choice(user: str):
	"""The user's AI User Settings row (without the key), or None."""
	if not user or not frappe.db.exists("AI User Settings", user):
		return None
	return frappe.db.get_value("AI User Settings", user, ["use_own", "provider", "model"], as_dict=True)


def get_own_key(user: str) -> str | None:
	from frappe.utils.password import get_decrypted_password

	return get_decrypted_password("AI User Settings", user, "api_key", raise_exception=False) or None


def company_key_allowed(settings, provider: str, model: str) -> bool:
	"""May a user without their own key run `provider` / `model` on the company key?"""
	from grid_erp.grid_ai.doctype.ai_settings.ai_settings import is_free_model

	if provider != settings.provider:
		return False
	if model == settings.model:
		return True  # the company default itself
	policy = settings.company_key_models or POLICY_FREE
	if policy == POLICY_ANY:
		return True
	if policy == POLICY_FREE:
		return is_free_model(provider, model)
	return False


def why_not_allowed(settings, provider: str, model: str, has_own_key: bool) -> str | None:
	"""None if the choice can be used, else a short reason for the user."""
	if not settings.allow_user_provider:
		return _("Administrator ne apna provider chunne ka option band rakha hai.")
	if provider not in USER_PROVIDERS:
		return _("Provider {0} users ke liye available nahi hai.").format(provider)
	if not model:
		return _("Model chuniye.")
	if has_own_key or company_key_allowed(settings, provider, model):
		return None
	if provider == settings.provider:
		policy = settings.company_key_models or POLICY_FREE
		if policy == POLICY_FREE:
			return _("Company ki key par sirf free models chal sakte hain. Is model ke liye apni {0} API key daaliye.").format(provider)
		return _("Is model ke liye apni {0} API key daaliye.").format(provider)
	return _("{0} use karne ke liye apni {0} API key daaliye.").format(provider)


def apply(config, settings, user: str) -> bool:
	"""Switch `config` (the company default) to the user's own choice when it is allowed.
	Returns True if it was switched."""
	from grid_erp.grid_ai.doctype.ai_settings.ai_settings import DEFAULT_BASE_URLS

	if not settings.allow_user_provider:
		return False
	choice = get_saved_choice(user)
	if not choice or not choice.use_own or not choice.provider or not choice.model:
		return False

	own_key = get_own_key(user)
	if why_not_allowed(settings, choice.provider, choice.model, bool(own_key)):
		return False

	config.provider = choice.provider
	config.model = choice.model
	if own_key:
		config.api_key = own_key
		config.own_key = True
	if choice.provider != settings.provider or own_key:
		# Official URL of the chosen provider; the company URL only for the company provider.
		config.base_url = DEFAULT_BASE_URLS[choice.provider].rstrip("/")
	return True
