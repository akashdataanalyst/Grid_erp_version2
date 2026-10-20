from __future__ import annotations

from types import SimpleNamespace

from grid_erp.recruitment_assessment.evaluation import evaluate_answer, normalize_answer


def test_normalize_answer_multiple_choice():
	assert normalize_answer('["A","B"]', "Multiple Choice") == ["A", "B"]


def test_evaluate_single_choice():
	question = SimpleNamespace(question_type="Single Choice MCQ", marks=5, negative_marking=1, correct_answer="A")
	answer = SimpleNamespace(answer="A")
	result = evaluate_answer(question, answer)
	assert result["is_correct"] is True
	assert result["score"] == 5

