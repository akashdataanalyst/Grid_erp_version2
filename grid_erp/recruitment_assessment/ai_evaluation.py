from __future__ import annotations

"""AI-powered evaluation for open-ended assessment answers.

Uses Frappe's built-in AI integration (frappe.ai) if available, otherwise falls
back to a direct OpenAI API call via the key stored in Site Settings.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from .audit import log_security_event
from .service import RecruitmentAssessmentService


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_openai_key() -> str | None:
    """Read OpenAI API key from Site Settings (custom field) or Frappe AI config."""
    try:
        key = frappe.db.get_single_value("Assessment Settings", "openai_api_key")
        if key:
            return key
    except Exception:
        pass
    try:
        key = frappe.db.get_single_value("AI Settings", "openai_api_key")
        if key:
            return key
    except Exception:
        pass
    return None


def _call_openai(prompt: str, model: str = "gpt-4o-mini") -> str:
    """Make a chat-completion call to OpenAI and return the text reply."""
    import urllib.request
    import urllib.error

    api_key = _get_openai_key()
    if not api_key:
        frappe.throw(
            _(
                "OpenAI API key not configured. "
                "Please set it in Assessment Settings → OpenAI API Key."
            )
        )

    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert HR assessor. Evaluate the candidate's answer "
                        "strictly and objectively. Return JSON only."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        frappe.log_error(body, "OpenAI API Error")
        frappe.throw(_("AI evaluation failed: {0}").format(exc.reason))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ai_evaluate_answer(answer_doc, question_doc) -> dict:
    """Evaluate a single open-ended answer using AI.

    Returns a dict with:
        score         – numeric score awarded
        max_marks     – max marks for the question
        feedback      – AI feedback string
        confidence    – 0-1 float (how confident the model is)
        status        – "AI Evaluated"
    """
    question_text = question_doc.question_text or question_doc.question_title or ""
    candidate_answer = answer_doc.answer or ""
    max_marks = flt(question_doc.marks or 0)

    if not candidate_answer.strip():
        return {
            "score": 0.0,
            "max_marks": max_marks,
            "feedback": _("No answer provided."),
            "confidence": 1.0,
            "status": "AI Evaluated",
        }

    prompt = f"""
You are evaluating a candidate's answer for a recruitment assessment.

Question: {question_text}
Maximum Marks: {max_marks}
Candidate's Answer: {candidate_answer}

Please evaluate the answer and return a JSON object with these exact keys:
- "score": a number between 0 and {max_marks} (can be decimal)
- "feedback": a brief 1-3 sentence explanation of the score
- "confidence": a number between 0 and 1 indicating how confident you are in this score

Consider:
1. Accuracy and correctness of the answer
2. Completeness — does it cover all key points?
3. Clarity and conciseness
4. Relevance to the question

Return only the JSON object, no other text.
"""

    try:
        raw = _call_openai(prompt)
        result = json.loads(raw)
        score = min(max(flt(result.get("score", 0)), 0), max_marks)
        return {
            "score": score,
            "max_marks": max_marks,
            "feedback": result.get("feedback", ""),
            "confidence": min(max(flt(result.get("confidence", 0.5)), 0), 1),
            "status": "AI Evaluated",
        }
    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "AI Evaluation Error")
        return {
            "score": 0.0,
            "max_marks": max_marks,
            "feedback": _("AI evaluation failed: {0}").format(str(exc)),
            "confidence": 0.0,
            "status": "Pending Review",
        }


@frappe.whitelist()
def run_ai_evaluation_for_attempt(attempt_name: str) -> dict:
    """Evaluate all pending open-ended answers in an attempt using AI.

    Can be called from a button on the Assessment Attempt form.
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection({"System Manager", "HR Manager", "HR User", "Recruiter"}):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    attempt = frappe.get_doc("Assessment Attempt", attempt_name)
    evaluated = 0
    failed = 0

    for row in attempt.answers:
        if row.evaluation_status in {"Evaluated", "Auto Evaluated", "AI Evaluated"}:
            continue
        try:
            question = frappe.get_doc("Question Bank", row.question_bank)
        except Exception:
            continue

        if question.question_type in {"Single Choice MCQ", "Multiple Choice", "True/False"}:
            continue  # handled by standard auto-evaluation
        if question.question_type in {"Video Response", "File Upload", "Image-based Question"}:
            continue  # AI cannot watch videos / open files; HR scores these by hand

        result = ai_evaluate_answer(row, question)
        row.score = result["score"]
        row.ai_feedback = result.get("feedback", "")
        row.evaluation_status = result["status"]
        evaluated += 1

    attempt.save(ignore_permissions=True)

    # Recompute totals
    from .evaluation import aggregate_result
    total, maximum, percentage, passed, manual_pending = aggregate_result(attempt)
    attempt.db_set("total_score", total, update_modified=False)
    attempt.db_set("percentage", percentage, update_modified=False)
    attempt.db_set("result_status", RecruitmentAssessmentService.get_result_status(attempt, passed), update_modified=False)

    log_security_event(
        attempt_name,
        "AI Evaluation",
        f"AI evaluated {evaluated} answers; {failed} failed",
    )

    return {
        "evaluated": evaluated,
        "failed": failed,
        "total_score": total,
        "percentage": percentage,
        "passed": passed,
    }


@frappe.whitelist()
def get_ai_settings_status() -> dict:
    """Check whether AI evaluation is configured."""
    key = _get_openai_key()
    return {"configured": bool(key), "has_key": bool(key)}
