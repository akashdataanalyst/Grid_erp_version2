"""Google Gemini provider, through Gemini's OpenAI-compatible endpoint
(https://generativelanguage.googleapis.com/v1beta/openai)."""

from grid_erp.grid_ai.ai.providers.openai import OpenAICompatibleProvider


class GeminiProvider(OpenAICompatibleProvider):
	name = "Gemini"

	@staticmethod
	def clean_model_id(model_id: str) -> str:
		# /models returns "models/gemini-2.5-flash"; chat requests take the bare id.
		return model_id.removeprefix("models/")

	@staticmethod
	def is_chat_model(model_id: str) -> bool:
		return model_id.startswith("gemini") and "embedding" not in model_id
