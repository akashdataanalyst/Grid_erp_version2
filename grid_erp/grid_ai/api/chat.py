"""Chat API for the AI Assistant page.

A message is answered in a background job, not in the web request: an answer can take a
minute or more (several model calls and ERP queries), and a web worker held that long blocks
other users of the whole ERP. `send_message` stores the user's message and an empty
("pending") reply, queues the job and returns at once. The job runs as the same user (Frappe
background jobs keep the session user, so every permission check still applies), streams the
reply over realtime and fills the stored reply. The page also polls `get_message`, so a reply
is shown even if realtime is unavailable.

Security notes:
- The user is always `frappe.session.user`. These endpoints take no user / role / permission /
  API key parameters; anything extra sent by the client is ignored by Frappe.
- Conversations are only readable by their owner.
- Errors returned to the browser are short and generic; details go to the Error Log.
"""

import json
import re
import time

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from grid_erp.grid_ai.ai.orchestrator import Orchestrator, load_history
from grid_erp.grid_ai.ai.provider import get_provider_config
from grid_erp.grid_ai.exceptions import GridAIError
from grid_erp.grid_ai.tools import permissions
from grid_erp.grid_ai.utils import add_token_usage, check_rate_limit, check_token_budget, log_request

STREAM_EVENT = "grid_ai_stream"
CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
GENERIC_ERROR = "Sorry, data retrieve karte time problem aayi."
STALE_ERROR = "Sorry, jawab time par nahi aa paya. Kripya dobara try kariye."
JOB_QUEUE = "short"
JOB_TIMEOUT = 300  # seconds; the orchestrator itself stops asking for data well before this
FEEDBACK_VALUES = ("Up", "Down", "")
SUGGESTIONS = (
	# (DocType, field the suggestion needs or None, question)
	("Sales Invoice", None, "Is month total sales kitni hui?"),
	("Sales Invoice", "items.item_code", "Is month sabse zyada kaunsa item bika?"),
	("Purchase Invoice", None, "Sabse zyada purchase kis supplier se hui?"),
	("Lead", None, "Aaj kitne leads create hue?"),
	("Attendance", None, "Kal kitne employees absent the?"),
	("Sales Order", None, "Is month kitne sales orders aaye?"),
	("Bin", None, "Kis item ka stock sabse zyada hai?"),
	("Employee", None, "Department-wise kitne employees hain?"),
	("Customer", None, "Is month kitne naye customers bane?"),
)


@frappe.whitelist(methods=["POST"])
def send_message(message: str, conversation_id: str | None = None, client_request_id: str | None = None) -> dict:
	"""Store the user's message and queue the answer. The reply comes over realtime
	(`grid_ai_stream`) and through `get_message`."""
	user = permissions.current_user()
	_ensure_access()
	settings = frappe.get_cached_doc("AI Settings")

	message = (message or "").strip()
	if not message:
		frappe.throw("Message khaali hai.")
	if len(message) > (settings.max_message_length or 2000):
		frappe.throw(f"Message bahut lamba hai (max {settings.max_message_length or 2000} characters).")
	if client_request_id and not CLIENT_ID_PATTERN.match(client_request_id):
		client_request_id = None

	started = time.time()
	try:
		check_rate_limit(user, settings.rate_limit_per_minute or 10, settings.rate_limit_per_day or 300)
		# The daily token limit protects the company key; users on their own key pay themselves.
		if not get_provider_config(user).own_key:
			check_token_budget(user, settings.token_limit_per_user_per_day or 0)
	except GridAIError as e:
		_log_chat(user, conversation_id, settings, e.log_status, e.code, started)
		return {"ok": False, "error": e.code, "message": e.user_message}

	conversation = _get_or_create_conversation(conversation_id, user, message)
	history = load_history(conversation.name, settings.max_conversation_messages or 12)
	_add_message(conversation.name, "user", message)
	reply = _add_message(conversation.name, "assistant", "", status="pending")

	frappe.enqueue(
		"grid_erp.grid_ai.api.chat.answer_message",
		queue=JOB_QUEUE,
		timeout=JOB_TIMEOUT,
		enqueue_after_commit=True,
		reply_id=reply.name,
		conversation_id=conversation.name,
		text=message,
		history=history,
		client_request_id=client_request_id,
		started=started,
	)
	return _response(conversation, reply, ok=True)


def answer_message(
	reply_id: str,
	conversation_id: str,
	text: str,
	history: list,
	client_request_id: str | None,
	started: float,
):
	"""Background job: answer one message as the user who sent it."""
	user = permissions.current_user()
	settings = frappe.get_cached_doc("AI Settings")
	conversation = frappe._dict(name=conversation_id)
	publisher = StreamPublisher(user, conversation_id, client_request_id, bool(settings.streaming_enabled))
	config = get_provider_config(user)  # the user's own provider / model when they chose one

	try:
		permissions.ensure_can_use_assistant(user)  # access may have changed while queued
		result = Orchestrator(conversation_id, publisher, config).run(text, history)
	except GridAIError as e:
		frappe.db.rollback()
		_finish(reply_id, e.user_message, "error")
		# `detail` is written by grid_ai itself (status codes, error kinds), never secrets or ERP data.
		error_code = f"{e.code}: {e.detail}" if e.detail else e.code
		_log_chat(user, conversation_id, config, e.log_status, error_code[:140], started)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Grid AI: chat failed")
		_finish(reply_id, GENERIC_ERROR, "error")
		_log_chat(user, conversation_id, config, "Error", "internal_error", started)
	else:
		_finish(reply_id, result.content, "ok", context_note=result.context_note, views=result.views)
		if not config.own_key:
			add_token_usage(user, result.prompt_tokens + result.completion_tokens)
		_log_chat(
			user,
			conversation_id,
			config,
			"Success",
			None,
			started,
			tool_rounds=result.tool_rounds,
			prompt_tokens=result.prompt_tokens,
			completion_tokens=result.completion_tokens,
		)
	_touch(conversation)
	frappe.db.commit()
	publisher.flush()
	publisher.done(_message_payload(frappe.get_doc("AI Chat Message", reply_id)))


@frappe.whitelist()
def get_status() -> dict:
	"""What the chat page needs to render. Never includes provider keys or configuration."""
	user = permissions.current_user()
	can_use = permissions.can_use_assistant(user)
	enabled = bool(frappe.db.get_single_value("AI Settings", "enabled"))
	is_admin = "System Manager" in frappe.get_roles(user)
	status = {
		"can_use": can_use,
		"enabled": enabled,
		"is_admin": is_admin,
		"max_message_length": frappe.db.get_single_value("AI Settings", "max_message_length") or 2000,
		"suggestions": _suggestions(user) if can_use and enabled else [],
	}
	if can_use and enabled:
		config = get_provider_config(user)
		# Names only, never keys or URLs.
		status["model"] = {"provider": config.provider, "model": config.model, "own": config.user_choice}
	return status


@frappe.whitelist()
def get_conversations(limit: int = 50) -> list[dict]:
	_ensure_access()
	return frappe.get_all(
		"AI Conversation",
		filters={"user": permissions.current_user()},
		fields=["name", "title", "last_message_at"],
		order_by="last_message_at desc",
		limit_page_length=min(int(limit or 50), 200),
	)


@frappe.whitelist()
def get_messages(conversation_id: str) -> list[dict]:
	_ensure_access()
	conversation = _get_owned_conversation(conversation_id)
	rows = frappe.get_all(
		"AI Chat Message",
		filters={"conversation": conversation.name},
		fields=["name", "role", "content", "status", "views", "feedback", "creation"],
		order_by="creation asc",
		limit_page_length=500,
	)
	return [_message_payload(row) for row in rows]


@frappe.whitelist()
def get_message(message_id: str) -> dict:
	"""One message of the user's own conversations (the page polls this for pending replies)."""
	_ensure_access()
	row = _get_owned_message(message_id)
	return _message_payload(row)


@frappe.whitelist(methods=["POST"])
def set_feedback(message_id: str, feedback: str) -> dict:
	"""Thumbs up / down on an assistant reply ("" clears it)."""
	_ensure_access()
	if feedback not in FEEDBACK_VALUES:
		_throw("Invalid feedback.", frappe.ValidationError)
	row = _get_owned_message(message_id)
	if row.role != "assistant" or row.status != "ok":
		_throw("Feedback sirf AI ke jawab par diya ja sakta hai.", frappe.ValidationError)
	frappe.db.set_value("AI Chat Message", row.name, "feedback", feedback, update_modified=False)
	return {"name": row.name, "feedback": feedback}


@frappe.whitelist(methods=["POST"])
def rename_conversation(conversation_id: str, title: str) -> dict:
	_ensure_access()
	conversation = _get_owned_conversation(conversation_id)
	title = (title or "").strip()[:140]
	if title:
		frappe.db.set_value("AI Conversation", conversation.name, "title", title)
	return {"name": conversation.name, "title": title or conversation.title}


@frappe.whitelist(methods=["POST"])
def delete_conversation(conversation_id: str) -> dict:
	_ensure_access()
	conversation = _get_owned_conversation(conversation_id)
	frappe.delete_doc("AI Conversation", conversation.name, ignore_permissions=True)
	return {"deleted": conversation.name}


# ---------------------------------------------------------------- helpers


def _ensure_access():
	try:
		permissions.ensure_can_use_assistant()
	except GridAIError as e:
		_throw(e.user_message, frappe.PermissionError)


def _throw(message: str, exc):
	"""Raise an expected error without a traceback in the response, whatever the site setting."""
	frappe.local.flags.disable_traceback = True
	frappe.throw(message, exc)


def _get_owned_conversation(conversation_id: str):
	"""Load a conversation that belongs to the session user. Others' conversations look missing."""
	row = frappe.db.get_value(
		"AI Conversation", {"name": conversation_id or "", "user": permissions.current_user()}, ["name", "title"], as_dict=True
	)
	if not row:
		_throw("Conversation nahi mili.", frappe.DoesNotExistError)
	return row


def _get_owned_message(message_id: str):
	row = frappe.db.get_value(
		"AI Chat Message",
		message_id or "",
		["name", "conversation", "role", "content", "status", "views", "feedback", "creation"],
		as_dict=True,
	)
	if not row:
		_throw("Message nahi mila.", frappe.DoesNotExistError)
	_get_owned_conversation(row.conversation)
	return row


def _get_or_create_conversation(conversation_id: str | None, user: str, first_message: str):
	if conversation_id:
		return _get_owned_conversation(conversation_id)
	doc = frappe.new_doc("AI Conversation")
	doc.title = _make_title(first_message)
	doc.user = user
	doc.last_message_at = now_datetime()
	doc.insert(ignore_permissions=True)
	return frappe._dict(name=doc.name, title=doc.title)


def _make_title(message: str) -> str:
	title = re.sub(r"\s+", " ", message).strip()
	return title[:60] + ("…" if len(title) > 60 else "")


def _add_message(conversation: str, role: str, content: str, status: str = "ok"):
	doc = frappe.new_doc("AI Chat Message")
	doc.conversation = conversation
	doc.role = role
	doc.content = content
	doc.status = status
	doc.insert(ignore_permissions=True)
	return doc


def _finish(reply_id: str, content: str, status: str, context_note: str | None = None, views: list | None = None):
	frappe.db.set_value(
		"AI Chat Message",
		reply_id,
		{
			"content": content,
			"status": status,
			"context_note": context_note,
			"views": json.dumps(views, default=str, ensure_ascii=False) if views else None,
		},
	)


def _touch(conversation):
	frappe.db.set_value("AI Conversation", conversation.name, "last_message_at", now_datetime(), update_modified=False)


def _message_payload(row) -> dict:
	status = row.status
	content = row.content
	# A reply still pending long after its job's timeout will never arrive (worker restarted).
	if status == "pending" and get_datetime(row.creation) < add_to_date(now_datetime(), seconds=-(JOB_TIMEOUT + 60)):
		status, content = "error", STALE_ERROR
	return {
		"name": row.name,
		"role": row.role,
		"content": content,
		"status": status,
		"views": json.loads(row.views) if row.get("views") else [],
		"feedback": row.get("feedback") or "",
	}


def _response(conversation, reply, ok: bool) -> dict:
	return {"ok": ok, "conversation": {"name": conversation.name, "title": conversation.title}, "message": _message_payload(reply)}


def _suggestions(user: str) -> list[str]:
	"""Example questions for the data sources this user can actually query."""
	from grid_erp.grid_ai.tools.registry import get_tools_for_user

	tools = {spec["doctype"]: spec for spec in get_tools_for_user(user)}
	questions = [
		question
		for doctype, needs_field, question in SUGGESTIONS
		if doctype in tools and (not needs_field or needs_field in tools[doctype]["fields"])
	]
	return questions[:4]


def _log_chat(user, conversation, source, status, error_code, started, **extra):
	"""`source` is AI Settings or the ProviderConfig actually used (both have provider / model)."""
	log_request(
		request_type="Chat",
		user=user,
		conversation=conversation,
		status=status,
		error_code=error_code,
		provider=source.provider,
		model=source.model,
		execution_ms=int((time.time() - started) * 1000),
		**extra,
	)


class StreamPublisher:
	"""Pushes partial reply text (when streaming is on), status lines and the final reply to the
	user's browser over Frappe realtime. Text is sent in small batches."""

	FLUSH_SECONDS = 0.12
	FLUSH_CHARS = 60

	def __init__(self, user: str, conversation: str, client_request_id: str | None, stream_tokens: bool = True):
		self.user = user
		self.conversation = conversation
		self.request_id = client_request_id
		self.stream_tokens = stream_tokens
		self.buffer: list[str] = []
		self.last_flush = time.monotonic()

	def token(self, text: str):
		if not self.stream_tokens:
			return
		self.buffer.append(text)
		if time.monotonic() - self.last_flush >= self.FLUSH_SECONDS or sum(map(len, self.buffer)) >= self.FLUSH_CHARS:
			self.flush()

	def flush(self):
		if self.buffer:
			self._publish({"type": "token", "text": "".join(self.buffer)})
			self.buffer = []
		self.last_flush = time.monotonic()

	def reset(self):
		"""A new model round starts: discard text shown from the previous round."""
		self.buffer = []
		self._publish({"type": "reset"})

	def status(self, text: str):
		self.flush()
		self._publish({"type": "status", "text": text})

	def done(self, message: dict):
		self._publish({"type": "done", "message": message})

	def _publish(self, payload: dict):
		payload.update({"conversation": self.conversation, "request_id": self.request_id})
		try:
			frappe.publish_realtime(STREAM_EVENT, payload, user=self.user, after_commit=False)
		except Exception:
			pass  # realtime is best-effort; the page also polls get_message
