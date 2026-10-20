"""OpenAI-compatible Chat Completions provider.

Used directly for OpenAI and as the base for OpenRouter, Ollama (/v1) and Gemini's
OpenAI-compatible endpoint.
"""

import json
import re

import frappe
import requests

from grid_erp.grid_ai.ai.provider import BaseProvider, LLMResponse, ProviderError, TokenCallback, ToolCall

SECRET_LIKE = re.compile(r"(sk|or|key|tok)[-_][A-Za-z0-9_\-]{8,}", re.IGNORECASE)


class OpenAICompatibleProvider(BaseProvider):
	name = "OpenAI"

	# ------------------------------------------------------------ requests

	def headers(self) -> dict:
		headers = {"Content-Type": "application/json"}
		if self.config.api_key:
			headers["Authorization"] = f"Bearer {self.config.api_key}"
		return headers

	def endpoint(self) -> str:
		return f"{self.config.base_url}/chat/completions"

	def payload(self, messages: list[dict], tools: list[dict] | None, stream: bool) -> dict:
		payload = {
			"model": self.config.model,
			"messages": [self._clean_message(m) for m in messages],
			"temperature": self.config.temperature,
			"max_tokens": self.config.max_tokens,
		}
		if tools:
			payload["tools"] = tools
			payload["tool_choice"] = "auto"
		if stream:
			payload["stream"] = True
		return payload

	@staticmethod
	def _clean_message(message: dict) -> dict:
		return {k: v for k, v in message.items() if not k.startswith("_")}

	def _post(self, payload: dict, stream: bool = False) -> requests.Response:
		try:
			response = requests.post(
				self.endpoint(),
				headers=self.headers(),
				json=payload,
				timeout=(10, self.config.timeout),
				stream=stream,
				proxies={"http": self.config.proxy, "https": self.config.proxy} if self.config.proxy else None,
			)
		except requests.Timeout:
			raise ProviderError(
				"AI service ne time par jawab nahi diya. Thodi der baad try kariye.", detail="timeout"
			) from None
		except requests.RequestException as e:
			raise ProviderError(
				"AI service se connect nahi ho paya.", detail=f"connection error: {type(e).__name__}"
			) from None

		if response.status_code >= 400:
			self._raise_http_error(response, tools_requested=bool(payload.get("tools")))
		return response

	def _raise_http_error(self, response: requests.Response, tools_requested: bool):
		status = response.status_code
		try:
			body = response.text[:600]
		except Exception:
			body = ""
		body = SECRET_LIKE.sub("[redacted]", body)
		frappe.log_error(title=f"Grid AI: {self.name} HTTP {status}", message=body)

		lowered = body.lower()
		if status in (401, 403):
			raise ProviderError(
				"AI provider ki API key galat hai ya expire ho gayi hai. Administrator se contact kariye.",
				detail=f"http {status}",
			)
		if status == 404:
			raise ProviderError("Configured AI model nahi mila. Administrator AI Settings check karein.", detail="http 404")
		if status == 429:
			raise ProviderError("AI service abhi busy hai. Thodi der baad try kariye.", detail="http 429")
		if tools_requested and ("tool" in lowered or "function" in lowered):
			raise ProviderError(detail=f"http {status}: tools unsupported", tools_unsupported=True)
		raise ProviderError(detail=f"http {status}")

	# ------------------------------------------------------------ responses

	def generate_response(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
		response = self._post(self.payload(messages, tools, stream=False))
		try:
			data = response.json()
		except ValueError:
			raise ProviderError(detail="non-JSON response") from None
		if data.get("error"):
			self._raise_body_error(data["error"], tools)
		return self._parse_completion(data)

	def stream_response(
		self, messages: list[dict], tools: list[dict] | None = None, on_token: TokenCallback | None = None
	) -> LLMResponse:
		response = self._post(self.payload(messages, tools, stream=True), stream=True)
		text_parts: list[str] = []
		calls: dict[int, dict] = {}
		result = LLMResponse()

		try:
			for event in _iter_sse(response):
				if event.get("error"):
					self._raise_body_error(event["error"], tools)
				usage = event.get("usage") or {}
				if usage:
					result.prompt_tokens = usage.get("prompt_tokens") or 0
					result.completion_tokens = usage.get("completion_tokens") or 0
				for choice in event.get("choices") or []:
					delta = choice.get("delta") or {}
					text = delta.get("content")
					if text:
						text_parts.append(text)
						if on_token:
							on_token(text)
					for tc in delta.get("tool_calls") or []:
						slot = calls.setdefault(tc.get("index", 0), {"id": None, "name": "", "arguments": ""})
						if tc.get("id"):
							slot["id"] = tc["id"]
						fn = tc.get("function") or {}
						if fn.get("name") and fn["name"] != slot["name"]:
							slot["name"] += fn["name"]
						slot["arguments"] += fn.get("arguments") or ""
					if choice.get("finish_reason"):
						result.finish_reason = choice["finish_reason"]
		except requests.RequestException:
			raise ProviderError("AI service se connection toot gaya.", detail="stream interrupted") from None
		finally:
			response.close()

		result.content = "".join(text_parts)
		result.tool_calls = [
			_tool_call(slot["id"] or f"call_{index}", slot["name"], slot["arguments"])
			for index, slot in sorted(calls.items())
			if slot["name"]
		]
		return result

	def _parse_completion(self, data: dict) -> LLMResponse:
		choice = (data.get("choices") or [{}])[0]
		message = choice.get("message") or {}
		content = message.get("content") or ""
		if isinstance(content, list):  # some providers return content parts
			content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
		usage = data.get("usage") or {}
		return LLMResponse(
			content=content,
			tool_calls=[
				_tool_call(
					tc.get("id") or f"call_{i}",
					(tc.get("function") or {}).get("name") or "",
					(tc.get("function") or {}).get("arguments"),
				)
				for i, tc in enumerate(message.get("tool_calls") or [])
			],
			finish_reason=choice.get("finish_reason"),
			prompt_tokens=usage.get("prompt_tokens") or 0,
			completion_tokens=usage.get("completion_tokens") or 0,
		)

	def _raise_body_error(self, error, tools):
		message = error.get("message", "") if isinstance(error, dict) else str(error)
		frappe.log_error(title=f"Grid AI: {self.name} error", message=SECRET_LIKE.sub("[redacted]", message[:600]))
		lowered = message.lower()
		raise ProviderError(
			detail="provider error in body",
			tools_unsupported=bool(tools) and ("tool" in lowered or "function" in lowered),
		)

	def validate_connection(self) -> bool:
		original = self.config.max_tokens
		self.config.max_tokens = 16
		try:
			self.generate_response([{"role": "user", "content": "Reply with the word OK."}])
		finally:
			self.config.max_tokens = original
		return True

	def list_models(self) -> list[str]:
		try:
			response = requests.get(
				f"{self.config.base_url}/models",
				headers=self.headers(),
				timeout=(10, self.config.timeout),
				proxies={"http": self.config.proxy, "https": self.config.proxy} if self.config.proxy else None,
			)
		except requests.RequestException as e:
			raise ProviderError(
				"AI service se connect nahi ho paya.", detail=f"models connection error: {type(e).__name__}"
			) from None
		if response.status_code >= 400:
			raise ProviderError("Model list nahi mil payi.", detail=f"models HTTP {response.status_code}")
		try:
			data = response.json().get("data") or []
		except (ValueError, AttributeError):
			raise ProviderError("Model list nahi mil payi.", detail="models invalid JSON") from None
		ids = {self.clean_model_id(m["id"]) for m in data if isinstance(m, dict) and m.get("id")}
		return sorted(i for i in ids if self.is_chat_model(i))

	@staticmethod
	def clean_model_id(model_id: str) -> str:
		return model_id

	@staticmethod
	def is_chat_model(model_id: str) -> bool:
		return True


# OpenAI's /models also lists embedding, audio, image and moderation models.
OPENAI_CHAT_PREFIXES = ("gpt-", "chatgpt-", "o1", "o3", "o4")
OPENAI_NON_CHAT = ("audio", "realtime", "tts", "transcribe", "image", "embedding", "search", "moderation")


class OpenAIProvider(OpenAICompatibleProvider):
	name = "OpenAI"

	@staticmethod
	def is_chat_model(model_id: str) -> bool:
		return model_id.startswith(OPENAI_CHAT_PREFIXES) and not any(w in model_id for w in OPENAI_NON_CHAT)


def _iter_sse(response: requests.Response):
	"""Yield JSON events from a Server-Sent Events stream until [DONE]."""
	for raw in response.iter_lines(decode_unicode=True):
		if not raw or not raw.startswith("data:"):
			continue  # blank lines, comments (": keep-alive"), event names
		data = raw[5:].strip()
		if data == "[DONE]":
			break
		try:
			yield json.loads(data)
		except ValueError:
			continue


def _tool_call(call_id: str, name: str, raw_arguments) -> ToolCall:
	if isinstance(raw_arguments, dict):
		return ToolCall(id=call_id, name=name, arguments=raw_arguments)
	try:
		arguments = json.loads(raw_arguments or "{}")
		if not isinstance(arguments, dict):
			raise ValueError
		return ToolCall(id=call_id, name=name, arguments=arguments)
	except ValueError:
		return ToolCall(id=call_id, name=name, arguments={}, invalid=True)
