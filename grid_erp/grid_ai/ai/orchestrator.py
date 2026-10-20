"""AI orchestrator: conversation context -> LLM -> structured tool calls -> engine -> answer.

The orchestrator contains no provider-specific logic (see `grid_erp.grid_ai.ai.provider`) and no
DocType-specific logic (see `grid_erp.grid_ai.tools.engine`). The LLM can only *request* queries; every
request is validated and permission-checked by the engine as the session user.
"""

import json
import re
import time
from dataclasses import dataclass, field

import frappe

from grid_erp.grid_ai.ai.prompts import TOOL_NAME, build_system_prompt, build_tool_definition
from grid_erp.grid_ai.ai.provider import ProviderConfig, ProviderError, ToolCall, get_provider, get_provider_config
from grid_erp.grid_ai.tools import registry
from grid_erp.grid_ai.tools.engine import execute_tool

MAX_CALLS_PER_ROUND = 5
MAX_TOOL_RESULT_CHARS = 8000
REQUEST_BUDGET_SECONDS = 120  # keep replies timely; the background job's own timeout is higher
FALLBACK_ANSWER = "Sorry, abhi jawab generate nahi ho paya. Kripya dobara try kariye."


@dataclass
class OrchestratorResult:
	content: str
	context_note: str = ""
	tool_rounds: int = 0
	prompt_tokens: int = 0
	completion_tokens: int = 0
	tools_used: list = field(default_factory=list)
	views: list = field(default_factory=list)  # per query: source, record link, rows (chat page only)


class Orchestrator:
	def __init__(self, conversation_id: str, publisher=None, config: ProviderConfig | None = None):
		self.conversation_id = conversation_id
		self.publisher = publisher
		self.settings = frappe.get_cached_doc("AI Settings")
		self.user = frappe.session.user
		self.provider = get_provider(config or get_provider_config(self.user))

	def run(self, message: str, history: list[dict]) -> OrchestratorResult:
		tools = registry.get_tools_for_user(self.user)
		prompt_json = self.settings.tool_calling_mode == "Prompt JSON"
		try:
			return self._run(message, history, tools, prompt_json)
		except ProviderError as e:
			if e.tools_unsupported and not prompt_json and tools:
				# Model has no native function calling: retry with the prompt-based protocol.
				if self.publisher:
					self.publisher.reset()
				return self._run(message, history, tools, prompt_json=True)
			raise

	def _run(self, message: str, history: list[dict], tools: list[dict], prompt_json: bool) -> OrchestratorResult:
		result = OrchestratorResult(content="")
		system = build_system_prompt(self.settings, self.user, tools, prompt_json)
		messages = [{"role": "system", "content": system}, *history, {"role": "user", "content": message}]
		tool_defs = [build_tool_definition(tools)] if tools and not prompt_json else None
		stream = bool(self.settings.streaming_enabled) and not prompt_json
		max_rounds = self.settings.max_tool_rounds or 4
		deadline = time.monotonic() + REQUEST_BUDGET_SECONDS
		notes: list[str] = []

		final = None
		for _round in range(max_rounds + 1):
			out_of_budget = _round == max_rounds or time.monotonic() > deadline
			if out_of_budget:
				messages.append(
					{
						"role": "user",
						"content": "Answer now using only the tool results above. Do not request more data.",
					}
				)

			response = self._call(messages, tool_defs, stream)
			result.prompt_tokens += response.prompt_tokens
			result.completion_tokens += response.completion_tokens

			calls = response.tool_calls
			if not calls and prompt_json and tools:
				calls = _parse_prompt_json(response.content)

			if not calls or out_of_budget:
				final = response.content if not calls else None
				break

			result.tool_rounds += 1
			if self.publisher:
				self.publisher.status("ERP data check kar raha hoon...")

			calls = calls[:MAX_CALLS_PER_ROUND]
			if prompt_json:
				messages.append({"role": "assistant", "content": response.content})
			else:
				response.tool_calls = calls
				messages.append(response.assistant_message())

			for call in calls:
				tool_result = self._execute(call)
				view = tool_result.pop("view", None)
				if view:
					result.views.append(view)
				if tool_result.get("ok"):
					notes.append(tool_result.get("context") or "")
					result.tools_used.append(tool_result.get("doctype"))
				else:
					notes.append(f"{call.arguments.get('doctype')}: {tool_result.get('error')}")
				payload = _truncate(json.dumps(tool_result, default=str, ensure_ascii=False))
				if prompt_json:
					messages.append({"role": "user", "content": f"TOOL_RESULT {payload}"})
				else:
					messages.append({"role": "tool", "tool_call_id": call.id, "content": payload})

		result.content = (final or "").strip() or FALLBACK_ANSWER
		result.context_note = "; ".join(n for n in notes if n)[:500]
		return result

	def _call(self, messages, tool_defs, stream):
		if self.publisher:
			self.publisher.reset()
		if stream:
			on_token = self.publisher.token if self.publisher else None
			return self.provider.stream_response(messages, tool_defs, on_token=on_token)
		return self.provider.generate_response(messages, tool_defs)

	def _execute(self, call: ToolCall) -> dict:
		if call.name != TOOL_NAME:
			return {"ok": False, "error": "unknown_tool", "message": "Only query_erp_data is available."}
		if call.invalid:
			return {"ok": False, "error": "invalid_request", "message": "Tool arguments were not valid JSON."}
		return execute_tool(call.arguments, conversation_id=self.conversation_id)


def load_history(conversation_id: str, limit: int) -> list[dict]:
	"""Recent messages as LLM context. Assistant turns carry a value-free note of the queries
	behind them so follow-ups ("pichle month se compare karo") keep the same data source."""
	rows = frappe.get_all(
		"AI Chat Message",
		filters={"conversation": conversation_id, "status": "ok"},
		fields=["role", "content", "context_note"],
		order_by="creation desc",
		limit_page_length=limit,
	)
	history = []
	for row in reversed(rows):
		content = row.content or ""
		if row.role == "assistant" and row.context_note:
			content = f"{content}\n\n[Data used for this answer: {row.context_note}]"
		history.append({"role": row.role, "content": content})
	# Context must start with a user turn.
	while history and history[0]["role"] != "user":
		history.pop(0)
	return history


def _parse_prompt_json(content: str) -> list[ToolCall]:
	"""Find a {"tool": ..., "arguments": {...}} request in a Prompt-JSON-mode reply."""
	if not content or "{" not in content:
		return []
	fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
	candidate = fenced.group(1) if fenced else content[content.find("{") : content.rfind("}") + 1]
	try:
		data = json.loads(candidate)
	except ValueError:
		return []
	if not isinstance(data, dict) or data.get("tool") != TOOL_NAME:
		return []
	arguments = data.get("arguments")
	return [ToolCall(id="prompt_json", name=TOOL_NAME, arguments=arguments if isinstance(arguments, dict) else {}, invalid=not isinstance(arguments, dict))]


def _truncate(text: str) -> str:
	if len(text) <= MAX_TOOL_RESULT_CHARS:
		return text
	return text[:MAX_TOOL_RESULT_CHARS] + '..."} (truncated)'
