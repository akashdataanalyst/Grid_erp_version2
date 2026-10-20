"""Which provider / model / key a user's questions run on. No database access: the saved
choice and key are patched in."""

import unittest
from unittest import mock

import frappe

from grid_erp.grid_ai.ai import user_choice
from grid_erp.grid_ai.ai.provider import ProviderConfig


def settings(policy="Free models only", allow=1, provider="OpenRouter", model="openrouter/free"):
	return frappe._dict(allow_user_provider=allow, company_key_models=policy, provider=provider, model=model)


def company_config():
	return ProviderConfig(
		provider="OpenRouter",
		base_url="https://openrouter.ai/api/v1",
		api_key="company-key",
		model="openrouter/free",
		temperature=0.1,
		max_tokens=1500,
		timeout=60,
	)


class TestCompanyKeyPolicy(unittest.TestCase):
	def test_free_models_only(self):
		s = settings()
		self.assertTrue(user_choice.company_key_allowed(s, "OpenRouter", "openrouter/free"))
		self.assertTrue(user_choice.company_key_allowed(s, "OpenRouter", "meta-llama/llama-3.3-70b-instruct:free"))
		self.assertFalse(user_choice.company_key_allowed(s, "OpenRouter", "openai/gpt-4o-mini"))
		self.assertFalse(user_choice.company_key_allowed(s, "OpenAI", "gpt-4o-mini"))

	def test_any_model(self):
		s = settings(policy="Any model")
		self.assertTrue(user_choice.company_key_allowed(s, "OpenRouter", "openai/gpt-4o-mini"))
		self.assertFalse(user_choice.company_key_allowed(s, "Claude", "claude-opus-5-5"))

	def test_not_allowed_still_allows_the_company_model(self):
		s = settings(policy="Not allowed", model="openai/gpt-4o-mini")
		self.assertTrue(user_choice.company_key_allowed(s, "OpenRouter", "openai/gpt-4o-mini"))
		self.assertFalse(user_choice.company_key_allowed(s, "OpenRouter", "openrouter/free"))

	def test_reasons(self):
		s = settings()
		self.assertIsNone(user_choice.why_not_allowed(s, "Claude", "claude-opus-5-5", has_own_key=True))
		self.assertIsNotNone(user_choice.why_not_allowed(s, "Claude", "claude-opus-5-5", has_own_key=False))
		self.assertIsNotNone(user_choice.why_not_allowed(s, "Ollama", "llama3.1", has_own_key=True))
		self.assertIsNotNone(user_choice.why_not_allowed(settings(allow=0), "Claude", "claude-opus-5-5", True))


class TestApply(unittest.TestCase):
	def run_apply(self, choice, own_key, s=None):
		config = company_config()
		with (
			mock.patch.object(user_choice, "get_saved_choice", return_value=choice),
			mock.patch.object(user_choice, "get_own_key", return_value=own_key),
		):
			switched = user_choice.apply(config, s or settings(), "someone@example.com")
		return switched, config

	def test_no_choice_keeps_company_default(self):
		switched, config = self.run_apply(None, None)
		self.assertFalse(switched)
		self.assertEqual((config.provider, config.api_key), ("OpenRouter", "company-key"))

	def test_own_provider_with_own_key_uses_official_url(self):
		choice = frappe._dict(use_own=1, provider="Claude", model="claude-sonnet-5-5")
		switched, config = self.run_apply(choice, "user-key")
		self.assertTrue(switched)
		self.assertEqual(config.provider, "Claude")
		self.assertEqual(config.api_key, "user-key")
		self.assertTrue(config.own_key)
		self.assertEqual(config.base_url, "https://api.anthropic.com/v1")

	def test_free_model_on_company_key(self):
		choice = frappe._dict(use_own=1, provider="OpenRouter", model="deepseek/deepseek-chat:free")
		switched, config = self.run_apply(choice, None)
		self.assertTrue(switched)
		self.assertEqual(config.model, "deepseek/deepseek-chat:free")
		self.assertEqual(config.api_key, "company-key")
		self.assertFalse(config.own_key)

	def test_disallowed_choice_falls_back_to_company_default(self):
		choice = frappe._dict(use_own=1, provider="OpenRouter", model="openai/gpt-4o-mini")
		switched, config = self.run_apply(choice, None)  # paid model, no own key, free-only policy
		self.assertFalse(switched)
		self.assertEqual(config.model, "openrouter/free")

	def test_switched_off_by_admin(self):
		choice = frappe._dict(use_own=1, provider="Claude", model="claude-sonnet-5-5")
		switched, config = self.run_apply(choice, "user-key", settings(allow=0))
		self.assertFalse(switched)
		self.assertEqual(config.api_key, "company-key")
