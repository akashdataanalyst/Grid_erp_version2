"""Anthropic Claude provider, using the official `anthropic` Python SDK (Messages API).

- Always streams (`messages.stream` + `get_final_message`), which also avoids HTTP timeouts.
- Thinking is adaptive on current models; temperature is not sent (rejected by current models).
- Server-side refusal fallback is enabled for the models that support it.
- The full assistant content (thinking / tool_use / fallback blocks) is echoed back unchanged
  in tool loops through the message's `_native` key.
"""

import frappe

from grid_erp.grid_ai.ai.provider import BaseProvider, LLMResponse, ProviderError, TokenCallback, ToolCall

DEFAULT_MODEL = "claude-opus-5-5"
MIN_MAX_TOKENS = 16000  # thinking is always on; leave room for it
EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
EFFORT_MODEL_PREFIXES = (
	"claude-fable-5",
	"claude-mythos-5",
	"claude-opus-5",
	"claude-opus-4-8",
	"claude-opus-4-7",
	"claude-opus-4-6",
	"claude-opus-4-5",
	"claude-sonnet-5",
	"claude-sonnet-4-6",
	"claude-haiku-5",
)
JSON_RETRIES = 2


class ClaudeProvider(BaseProvider):
	name = "Claude"

	def _client(self):
		try:
			import anthropic
		except ImportError:
			raise ProviderError(
				"Claude provider ke liye 'anthropic' package install nahi hai.", detail="anthropic not installed"
			) from None

		base_url = self.config.base_url or ""
		if base_url.endswith("/v1"):
			base_url = base_url[: -len("/v1")]
		options = {}
		if self.config.proxy:
			# DefaultHttpxClient keeps the SDK's default timeouts and connection limits.
			options["http_client"] = anthropic.DefaultHttpxClient(proxy=self.config.proxy)
		return anthropic, anthropic.Anthropic(
			api_key=self.config.api_key,
			base_url=base_url or None,
			timeout=float(self.config.timeout),
			max_retries=2,
			**options,
		)

	def generate_response(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
		return self.stream_response(messages, tools, on_token=None)

	def stream_response(
		self, messages: list[dict], tools: list[dict] | None = None, on_token: TokenCallback | None = None
	) -> LLMResponse:
		anthropic, client = self._client()
		system, claude_messages = _to_claude_messages(messages)
		request = self._request(system, claude_messages, tools)

		final = None
		for attempt in range(JSON_RETRIES + 1):
			try:
				with client.beta.messages.stream(**request) as stream:
					for event in stream:
						if event.type == "text" and on_token:
							on_token(event.text)
					final = stream.get_final_message()
				break
			except ValueError:
				# Tool input JSON the SDK could not parse at all: re-issue the turn (bounded).
				if attempt == JSON_RETRIES:
					raise ProviderError(detail="unparseable tool input") from None
			except anthropic.AuthenticationError:
				raise ProviderError(
					"AI provider ki API key galat hai ya expire ho gayi hai. Administrator se contact kariye.",
					detail="claude 401",
				) from None
			except anthropic.PermissionDeniedError:
				raise ProviderError("AI provider ne request allow nahi ki.", detail="claude 403") from None
			except anthropic.NotFoundError:
				raise ProviderError(
					"Configured AI model nahi mila. Administrator AI Settings check karein.", detail="claude 404"
				) from None
			except anthropic.RateLimitError:
				raise ProviderError("AI service abhi busy hai. Thodi der baad try kariye.", detail="claude 429") from None
			except anthropic.BadRequestError as e:
				frappe.log_error(title="Grid AI: Claude bad request", message=str(e.message)[:600])
				raise ProviderError(detail="claude 400") from None
			except anthropic.APIStatusError as e:
				frappe.log_error(title=f"Grid AI: Claude HTTP {e.status_code}", message=str(e.message)[:600])
				raise ProviderError(detail=f"claude {e.status_code}") from None
			except anthropic.APITimeoutError:
				raise ProviderError("AI service ne time par jawab nahi diya.", detail="claude timeout") from None
			except anthropic.APIConnectionError:
				raise ProviderError("AI service se connect nahi ho paya.", detail="claude connection") from None

		return _to_llm_response(final)

	def _request(self, system: str, messages: list[dict], tools: list[dict] | None) -> dict:
		model = self.config.model or DEFAULT_MODEL
		request = {
			"model": model,
			"max_tokens": max(self.config.max_tokens or 0, MIN_MAX_TOKENS),
			"messages": messages,
		}
		if system:
			# Cache tools + system prompt (rules and data catalog): every tool round and every
			# follow-up resends them unchanged, so later calls read them from cache.
			request["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
		if tools:
			request["tools"] = [_to_claude_tool(t) for t in tools]
		if model.startswith(EFFORT_MODEL_PREFIXES):
			request["output_config"] = {"effort": EFFORT}
		if model in FALLBACK_MODELS:
			request["betas"] = [FALLBACK_BETA]
			request["fallbacks"] = "default"
		return request

	def validate_connection(self) -> bool:
		anthropic, client = self._client()
		try:
			client.models.retrieve(self.config.model or DEFAULT_MODEL)
		except anthropic.AuthenticationError:
			raise ProviderError("API key galat hai.", detail="claude 401") from None
		except anthropic.NotFoundError:
			raise ProviderError("Model nahi mila.", detail="claude 404") from None
		except anthropic.APIError:
			raise ProviderError("Claude API se connect nahi ho paya.", detail="claude validate") from None
		return True

	def list_models(self) -> list[str]:
		anthropic, client = self._client()
		try:
			return [model.id for model in client.models.list()]
		except anthropic.AuthenticationError:
			raise ProviderError("API key galat hai.", detail="claude 401") from None
		except anthropic.APIError:
			raise ProviderError("Claude API se connect nahi ho paya.", detail="claude models") from None


def _to_claude_tool(tool: dict) -> dict:
	fn = tool.get("function") or tool
	return {
		"name": fn["name"],
		"description": fn.get("description", ""),
		"input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
		"eager_input_streaming": True,
	}


def _to_claude_messages(messages: list[dict]) -> tuple[str, list[dict]]:
	"""Internal OpenAI-style messages -> (system, Claude messages).

	All tool results of one turn go into a single user message, as the API expects.
	"""
	system_parts = []
	result: list[dict] = []
	pending_results: list[dict] = []

	def flush_results():
		if pending_results:
			result.append({"role": "user", "content": list(pending_results)})
			pending_results.clear()

	for message in messages:
		role = message.get("role")
		if role == "system":
			system_parts.append(message.get("content") or "")
			continue
		if role == "tool":
			pending_results.append(
				{
					"type": "tool_result",
					"tool_use_id": message.get("tool_call_id"),
					"content": message.get("content") or "",
				}
			)
			continue

		flush_results()
		if role == "assistant":
			if message.get("_native"):
				result.append({"role": "assistant", "content": message["_native"]})
				continue
			blocks = []
			if message.get("content"):
				blocks.append({"type": "text", "text": message["content"]})
			for call in message.get("tool_calls") or []:
				fn = call.get("function") or {}
				blocks.append(
					{
						"type": "tool_use",
						"id": call.get("id"),
						"name": fn.get("name"),
						"input": frappe.parse_json(fn.get("arguments") or "{}"),
					}
				)
			if blocks:
				result.append({"role": "assistant", "content": blocks})
		else:
			result.append({"role": "user", "content": message.get("content") or ""})

	flush_results()
	return "\n\n".join(p for p in system_parts if p), result


def _to_llm_response(final) -> LLMResponse:
	if final is None:
		raise ProviderError(detail="no response")

	if final.stop_reason == "refusal":
		return LLMResponse(
			content="Main is request mein madad nahi kar sakta. Kripya ERP data se related sawal poochiye.",
			finish_reason="refusal",
		)

	text = "".join(block.text for block in final.content if block.type == "text")
	tool_uses = [block for block in final.content if block.type == "tool_use"]
	usage = final.usage

	if tool_uses and final.stop_reason == "max_tokens":
		# A truncated tool input parses as a partial object: never run it.
		return LLMResponse(
			content="Jawab bahut lamba ho gaya. Kripya sawal thoda chhota karke poochiye.",
			finish_reason="max_tokens",
		)

	tool_calls = []
	for block in tool_uses:
		valid = isinstance(block.input, dict)
		tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input if valid else {}, invalid=not valid))

	return LLMResponse(
		content=text,
		tool_calls=tool_calls,
		finish_reason=final.stop_reason,
		prompt_tokens=getattr(usage, "input_tokens", 0) or 0,
		completion_tokens=getattr(usage, "output_tokens", 0) or 0,
		native=final.to_dict()["content"] if tool_calls else None,
	)
