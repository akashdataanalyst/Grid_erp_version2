"""Ollama provider, through Ollama's OpenAI-compatible endpoint (http://HOST:11434/v1).

No API key is required. Tool calling depends on the model; if the model does not support
tools, set AI Settings > Tool Calling Mode to "Prompt JSON".
"""

from grid_erp.grid_ai.ai.providers.openai import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
	name = "Ollama"
