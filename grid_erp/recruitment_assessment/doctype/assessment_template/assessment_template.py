from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from grid_erp.recruitment_assessment.base import BaseAssessmentDocument


class AssessmentTemplate(BaseAssessmentDocument):
	def validate(self):
		self.is_active = 1 if self.is_active is None else self.is_active
		self.sync_question_rows()
		self.validate_duplicate_questions()
		self.update_question_totals()

	def on_submit(self):
		self.validate_ready_for_submission()

	def sync_question_rows(self):
		for row in self.get("questions", []):
			if not row.question:
				continue
			question = frappe.db.get_value(
				"Question Bank",
				row.question,
				["question_text", "category", "difficulty", "marks", "correct_answer", "question_type"],
				as_dict=True,
			)
			if not question:
				frappe.throw(_("Question {0} does not exist.").format(row.question))
			row.question_text = question.question_text
			row.category = question.category
			row.difficulty = question.difficulty
			row.marks = flt(question.marks)
			row.correct_answer = question.correct_answer
			row.question_type = question.question_type

	def validate_duplicate_questions(self):
		seen = set()
		duplicates = []
		for row in self.get("questions", []):
			if not row.question:
				continue
			if row.question in seen:
				duplicates.append(row.question)
			seen.add(row.question)
		if duplicates:
			frappe.throw(_("Duplicate questions are not allowed: {0}").format(", ".join(sorted(set(duplicates)))))

	def update_question_totals(self):
		question_rows = [row for row in self.get("questions", []) if row.question]
		self.total_questions = len(question_rows)
		self.total_marks = sum(flt(row.marks) for row in question_rows)
		self.question_categories = ", ".join(
			sorted({row.category for row in question_rows if row.category})
		)

	def validate_ready_for_submission(self):
		if not self.get("questions"):
			frappe.throw(_("Assessment Template cannot be submitted without questions."))
		self.validate_duplicate_questions()
		self.update_question_totals()
		if flt(self.total_marks) <= 0:
			frappe.throw(_("Assessment Template cannot be submitted when total marks are zero."))
