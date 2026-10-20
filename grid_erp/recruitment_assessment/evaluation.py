from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cstr, flt

from .constants import QUESTION_TYPES


def normalize_answer(answer, question_type: str):
	if question_type == "Multiple Choice":
		if isinstance(answer, str):
			try:
				return sorted(json.loads(answer))
			except Exception:
				return sorted([part.strip() for part in answer.split(",") if part.strip()])
		return sorted(answer or [])
	return cstr(answer or "").strip()


def is_objective(question_type: str) -> bool:
	return question_type in {"Single Choice MCQ", "Multiple Choice", "True/False"}


def evaluate_answer(question, answer_doc) -> dict:
	question_type = question.question_type
	max_marks = flt(question.marks or 0)
	answer = normalize_answer(answer_doc.answer, question_type)
	correct_answer = normalize_answer(question.correct_answer, question_type)
	is_correct = False
	score = 0.0

	if question_type == "Single Choice MCQ":
		is_correct = cstr(answer) == cstr(correct_answer)
	elif question_type == "True/False":
		is_correct = cstr(answer).lower() == cstr(correct_answer).lower()
	elif question_type == "Multiple Choice":
		is_correct = set(answer) == set(correct_answer)

	if is_objective(question_type):
		score = max_marks if is_correct else -(flt(question.negative_marking or 0))
		score = max(score, 0)

	return {
		"is_objective": is_objective(question_type),
		"is_correct": is_correct,
		"score": score,
		"status": "Evaluated" if is_objective(question_type) else "Pending Review",
	}


def aggregate_result(attempt):
	total = 0.0
	maximum = 0.0
	manual_pending = 0
	for row in attempt.answers:
		maximum += flt(row.marks or 0)
		total += flt(row.score or 0)
		if row.evaluation_status not in {"Evaluated", "Auto Evaluated", "AI Evaluated"}:
			manual_pending += 1

	percentage = round((total / maximum) * 100, 2) if maximum else 0.0
	passed = percentage >= flt(attempt.passing_marks or 0)
	return total, maximum, percentage, passed, manual_pending

