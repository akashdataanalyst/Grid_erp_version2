from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt, get_datetime, now_datetime, time_diff_in_seconds
from frappe.utils.password import get_decrypted_password

from .audit import log_assessment_audit, log_security_event
from .evaluation import aggregate_result, evaluate_answer
from .notifications import send_assessment_email, send_assessment_whatsapp
from .utils import generate_secret, hash_secret, minutes_from_now, set_job_applicant_values, verify_secret


class RecruitmentAssessmentService:
	ALLOWED_ASSIGNMENT_STATUSES = {"Open", "Replied", "Shortlisted", "Hold"}

	def create_assignment(
		self,
		job_applicant: str,
		template: str,
		validity_days: int | None = None,
		duration_minutes: int | None = None,
		passing_marks: float | None = None,
		negative_marking: float = 0,
		randomize_questions: int = 1,
		shuffle_options: int = 1,
		max_attempts: int = 1,
		webcam_recording: int = 0,
		webcam_snapshots: int = 0,
		microphone_recording: int = 0,
		screen_recording: int = 0,
		fullscreen_enforcement: int = 0,
		tab_switch_detection: int = 1,
		copy_paste_blocking: int = 1,
		right_click_blocking: int = 1,
		devtools_detection: int = 1,
		snapshot_interval_seconds: int = 60,
		screen_grace_period_seconds: int = 60,
		camera_grace_period_seconds: int = 60,
		microphone_failure_behavior: str = "Warn Only",
		allow_resume_after_crash: int = 1,
		auto_save: int = 1,
		auto_submit: int = 0,
		email_notifications: int = 1,
		whatsapp_notifications: int = 0,
		assessment_expiry_date: str | None = None,
		email_template: str | None = None,
		send_email: int = 1,
	) -> frappe.model.document.Document:
		applicant = frappe.get_doc("Job Applicant", job_applicant)
		template_doc = frappe.get_doc("Assessment Template", template)
		# Unset options (e.g. auto-assign on Shortlist) follow the template's own defaults.
		validity_days = cint(validity_days or template_doc.default_validity_days or 3)
		duration_minutes = cint(duration_minutes or template_doc.default_duration_minutes or 60)
		if passing_marks is None:
			passing_marks = flt(template_doc.default_passing_marks)
		send_email = cint(send_email)
		email_notifications = cint(email_notifications)

		if not template_doc.is_active:
			frappe.throw(_("Assessment Template {0} is inactive").format(template_doc.name))

		if applicant.status not in self.ALLOWED_ASSIGNMENT_STATUSES:
			frappe.throw(
				_("Assessment can only be assigned to active applicants. Applicant {0} is {1}.").format(
					applicant.name, applicant.status or _("Unknown")
				)
			)

		existing_assignment = frappe.get_all(
			"Assessment Assignment",
			filters={"job_applicant": applicant.name, "template": template_doc.name},
			fields=["name"],
			order_by="creation desc",
			limit=1,
		)
		if existing_assignment:
			frappe.throw(_("Assessment is already assigned to this applicant."))

		assignment = frappe.new_doc("Assessment Assignment")
		assignment.job_applicant = applicant.name
		assignment.applicant_name = applicant.applicant_name
		assignment.candidate_email = applicant.email_id
		assignment.candidate_phone = applicant.phone_number
		assignment.job_opening = applicant.job_title
		assignment.department = frappe.db.get_value("Job Opening", applicant.job_title, "department")
		assignment.designation = applicant.designation
		assignment.template = template_doc.name
		assignment.validity_days = validity_days
		assignment.valid_till = get_datetime(assessment_expiry_date) if assessment_expiry_date else add_days(now_datetime(), validity_days)
		assignment.duration_minutes = duration_minutes
		assignment.passing_marks = passing_marks
		assignment.negative_marking = negative_marking
		assignment.randomize_questions = randomize_questions
		assignment.shuffle_options = shuffle_options
		assignment.max_attempts = max_attempts
		assignment.webcam_recording = webcam_recording
		assignment.webcam_snapshots = webcam_snapshots
		assignment.microphone_recording = microphone_recording
		assignment.screen_recording = screen_recording
		assignment.fullscreen_enforcement = fullscreen_enforcement
		assignment.tab_switch_detection = tab_switch_detection
		assignment.copy_paste_blocking = copy_paste_blocking
		assignment.right_click_blocking = right_click_blocking
		assignment.devtools_detection = devtools_detection
		assignment.snapshot_interval_seconds = snapshot_interval_seconds
		assignment.screen_grace_period_seconds = screen_grace_period_seconds
		assignment.camera_grace_period_seconds = camera_grace_period_seconds
		assignment.microphone_failure_behavior = microphone_failure_behavior
		assignment.allow_resume_after_crash = allow_resume_after_crash
		assignment.auto_save = auto_save
		assignment.auto_submit = auto_submit
		assignment.email_notifications = email_notifications
		assignment.whatsapp_notifications = whatsapp_notifications
		if assignment.meta.has_field("email_template"):
			assignment.email_template = email_template
		if assignment.meta.has_field("assigned_on"):
			assignment.assigned_on = now_datetime()
		if assignment.meta.has_field("assigned_by"):
			assignment.assigned_by = frappe.session.user
		assignment.assignment_token = generate_secret(48)
		assignment.assignment_token_hash = hash_secret(assignment.assignment_token)
		assignment.temp_password = generate_secret(12)
		assignment.temp_password_hash = hash_secret(assignment.temp_password)
		assignment.portal_status = "Assigned"
		assignment.assessment_state = "Assigned"
		assignment.set("questions", self.get_template_question_rows(assignment, template_doc))
		assignment.insert(ignore_permissions=True)
		if send_email and assignment.email_notifications:
			self.send_invites(assignment, email_template=email_template)
		self.sync_job_applicant(assignment)
		return assignment

	def get_template_question_rows(self, assignment, template_doc):
		questions = []
		if template_doc.get("questions"):
			questions = self.get_questions_for_template(template_doc)
		else:
			for section in template_doc.sections:
				section_questions = self.get_questions_for_section(section)
				questions.extend(section_questions)
		if not questions:
			frappe.throw(_("Assessment Template {0} has no active questions.").format(template_doc.name))
		if assignment.randomize_questions:
			questions = self.randomize_questions(questions)
		rows = []
		for idx, question in enumerate(questions, start=1):
			question_name = question.get("name") if isinstance(question, dict) else question.name
			question_text = question.get("question_text") if isinstance(question, dict) else question.question_text
			question_type = question.get("question_type") if isinstance(question, dict) else question.question_type
			marks = question.get("marks") if isinstance(question, dict) else question.marks
			negative_marking = question.get("negative_marking") if isinstance(question, dict) else question.negative_marking
			options = question.get("options") if isinstance(question, dict) else question.options
			correct_answer = question.get("correct_answer") if isinstance(question, dict) else question.correct_answer
			coding_language = question.get("coding_language") if isinstance(question, dict) else question.coding_language
			sql_schema = question.get("sql_schema") if isinstance(question, dict) else question.sql_schema
			expected_output = question.get("expected_output") if isinstance(question, dict) else question.expected_output
			rows.append(
				{
					"idx": idx,
					"question_bank": question_name,
					"question_text": question_text,
					"question_type": question_type,
					"marks": marks,
					"negative_marking": negative_marking,
					"options_json": frappe.as_json(options),
					"correct_answer": correct_answer,
					"coding_language": coding_language,
					"sql_schema": sql_schema,
					"expected_output": expected_output,
				},
			)
		return rows

	def get_questions_for_template(self, template_doc):
		question_names = []
		for row in template_doc.get("questions", []):
			if row.question and row.question not in question_names:
				question_names.append(row.question)
		if not question_names:
			return []

		questions_by_name = {}
		for question in frappe.get_all(
			"Question Bank",
			filters={"name": ["in", question_names], "disabled": 0},
			pluck="name",
		):
			questions_by_name[question] = frappe.get_doc("Question Bank", question)
		return [questions_by_name[name] for name in question_names if name in questions_by_name]

	def get_questions_for_section(self, section):
		if section.source_type == "Question Bank":
			if section.question_bank:
				if frappe.db.get_value("Question Bank", section.question_bank, "disabled"):
					return []
				return [frappe.get_doc("Question Bank", section.question_bank)]
			return frappe.get_all(
				"Question Bank",
				filters={
					"assessment_section": section.section_name,
					"disabled": 0,
				},
				fields=["*"],
				order_by="idx asc, creation asc",
			)
		return []

	def randomize_questions(self, questions):
		return sorted(questions, key=lambda item: frappe.generate_hash(length=16))

	def send_invites(self, assignment, email_template: str | None = None):
		context = {
			"assignment_name": assignment.name,
			"candidate_name": assignment.applicant_name,
			"candidate_email": assignment.candidate_email,
			"portal_link": assignment.get_portal_link(),
			"temporary_password": assignment.get_secret("temp_password"),
			"valid_till": assignment.valid_till,
			"duration_minutes": assignment.duration_minutes,
		}
		if assignment.email_notifications:
			send_assessment_email(assignment, None, email_template, context)
		if assignment.whatsapp_notifications:
			send_assessment_whatsapp(assignment, f"Assessment assigned for {assignment.applicant_name}.")

	def sync_job_applicant(self, assignment):
		job_applicant = frappe.get_doc("Job Applicant", assignment.job_applicant)
		values = {
			"assessment_assignment_link": assignment.name,
			"assessment_state": assignment.portal_status,
			"assessment_status": assignment.portal_status,
			"assessment_assigned_on": getattr(assignment, "assigned_on", None) or assignment.creation,
			"assessment_assigned_by": getattr(assignment, "assigned_by", None) or assignment.owner,
			"assessment_link": assignment.get_portal_link(),
			"assessment_email_sent": getattr(assignment, "email_sent", 0),
		}
		if assignment.current_attempt:
			attempt = frappe.get_doc("Assessment Attempt", assignment.current_attempt)
			snapshots = frappe.get_all(
				"Assessment Snapshot",
				filters={"attempt": attempt.name},
				fields=["snapshot_file", "captured_on", "reason"],
				order_by="captured_on desc",
				limit=5,
			)
			snapshot_html = "<br>".join(
				f"<a href='{frappe.utils.escape_html(row.snapshot_file)}' target='_blank'>{frappe.utils.escape_html(row.reason or row.captured_on)}</a>"
				for row in snapshots
				if row.snapshot_file
			)
			values.update(
				{
					"assessment_attempt_status": attempt.status,
					"assessment_score": attempt.percentage,
					"assessment_violation_count": attempt.violation_count,
					"assessment_recording": attempt.screen_recording_file or attempt.camera_recording_file or attempt.microphone_recording_file,
					"assessment_snapshots": snapshot_html,
				}
			)
		set_job_applicant_values(job_applicant.name, values)

	def authenticate_candidate(self, assignment_name: str, candidate_id: str, token: str | None = None, password: str | None = None):
		assignment = frappe.get_doc("Assessment Assignment", assignment_name)
		if assignment.valid_till and assignment.valid_till < now_datetime():
			assignment.portal_status = "Expired"
			assignment.assessment_state = "Expired"
			assignment.save(ignore_permissions=True)
			frappe.throw(_("Assessment Expired"))
		if assignment.submitted:
			frappe.throw(_("Assessment already submitted"))
		if assignment.job_applicant != candidate_id and assignment.candidate_email != candidate_id:
			frappe.throw(_("Invalid candidate credentials"))

		token_used = bool(getattr(assignment, "token_used_on", None))
		token_ok = bool(token and not token_used and verify_secret(token, assignment.get_secret("assignment_token_hash")))
		password_ok = bool(password and verify_secret(password, assignment.get_secret("temp_password_hash")))
		if not (token_ok or password_ok):
			frappe.throw(_("Invalid access token or temporary password"))

		session_token = generate_secret(48)
		assignment.portal_session_token_hash = hash_secret(session_token)
		assignment.portal_session_expires_on = minutes_from_now(720)
		if token_ok and assignment.meta.has_field("token_used_on"):
			assignment.token_used_on = now_datetime()
		assignment.portal_status = "Pending"
		assignment.save(ignore_permissions=True)
		self.sync_job_applicant(assignment)
		return {
			"assignment": assignment.name,
			"session_token": session_token,
			"applicant_name": assignment.applicant_name,
			"valid_till": assignment.valid_till,
			"status": assignment.portal_status,
		}

	def validate_session(self, token: str):
		assignment_name = frappe.db.get_value(
			"Assessment Assignment",
			{"portal_session_token_hash": ["!=", ""], "docstatus": 0},
			"name",
		)
		if not assignment_name:
			frappe.throw(_("Session not found"))

	def get_assignment_by_session(self, session_token: str):
		# Only assignments with a live portal session can match; each check decrypts + verifies a hash.
		assignments = frappe.get_all(
			"Assessment Assignment",
			filters={"portal_session_expires_on": [">", now_datetime()]},
			pluck="name",
		)
		for name in assignments:
			stored_hash = get_decrypted_password(
				"Assessment Assignment", name, "portal_session_token_hash", raise_exception=False
			)
			if verify_secret(session_token, stored_hash):
				return frappe.get_doc("Assessment Assignment", name)
		frappe.throw(_("Invalid or expired session"))

	def create_consent_attempt(self, assignment_name: str, consent_accepted: bool, browser_info: dict | None = None):
		assignment = frappe.get_doc("Assessment Assignment", assignment_name)
		if assignment.valid_till and assignment.valid_till < now_datetime():
			assignment.portal_status = "Expired"
			assignment.save(ignore_permissions=True)
			frappe.throw(_("Assessment Expired"))
		if assignment.submitted:
			frappe.throw(_("Assessment already submitted"))

		browser_info = browser_info or {}
		attempt = frappe.new_doc("Assessment Attempt")
		attempt.assignment = assignment.name
		attempt.job_applicant = assignment.job_applicant
		attempt.template = assignment.template
		attempt.attempt_no = (frappe.db.count("Assessment Attempt", {"assignment": assignment.name}) or 0) + 1
		attempt.status = "Consent Accepted" if consent_accepted else "Consent Rejected"
		attempt.duration_minutes = assignment.duration_minutes
		attempt.passing_marks = assignment.passing_marks
		attempt.candidate_consent = 1 if consent_accepted else 0
		attempt.consent_timestamp = now_datetime()
		attempt.consent_ip = getattr(frappe.local, "request_ip", None)
		attempt.browser_name = cstr(browser_info.get("browser_name"))[:140]
		attempt.browser_version = cstr(browser_info.get("browser_version"))[:140]
		attempt.operating_system = cstr(browser_info.get("operating_system"))[:140]
		attempt.user_agent = cstr(browser_info.get("user_agent") or frappe.get_request_header("User-Agent"))
		if consent_accepted:
			attempt.resume_allowed = 1 if assignment.allow_resume_after_crash else 0
			for question in assignment.questions:
				attempt.append(
					"answers",
					{
						"question_bank": question.question_bank,
						"question_text": question.question_text,
						"question_type": question.question_type,
						"marks": question.marks,
						"answer": "",
						"evaluation_status": "Pending Review",
						"is_correct": 0,
						"is_objective": 0,
					},
				)
		attempt.insert(ignore_permissions=True)
		log_assessment_audit(
			attempt.name,
			"Consent Accepted" if consent_accepted else "Consent Rejected",
			remarks="Candidate accepted consent." if consent_accepted else "Candidate rejected consent.",
			payload=browser_info,
		)
		if consent_accepted:
			assignment.current_attempt = attempt.name
			assignment.portal_status = "Pending"
			assignment.assessment_state = "Pending"
			assignment.save(ignore_permissions=True)
			self.sync_job_applicant(assignment)
		return attempt

	def start_assessment(self, assignment_name: str):
		assignment = frappe.get_doc("Assessment Assignment", assignment_name)
		if assignment.valid_till and assignment.valid_till < now_datetime():
			assignment.portal_status = "Expired"
			assignment.save(ignore_permissions=True)
			frappe.throw(_("Assessment Expired"))
		if assignment.started:
			if assignment.current_attempt and assignment.allow_resume_after_crash and not assignment.submitted:
				attempt = frappe.get_doc("Assessment Attempt", assignment.current_attempt)
				attempt.resume_allowed = 1
				attempt.last_heartbeat_on = now_datetime()
				attempt.save(ignore_permissions=True)
				log_assessment_audit(attempt.name, "Assessment Resumed", remarks="Candidate resumed assessment.")
				return attempt
			frappe.throw(_("Assessment already started"))
		if assignment.submitted:
			frappe.throw(_("Assessment already submitted"))

		if not assignment.current_attempt:
			frappe.throw(_("Consent is required before starting the assessment."))
		attempt = frappe.get_doc("Assessment Attempt", assignment.current_attempt)
		if not attempt.candidate_consent:
			frappe.throw(_("Consent is required before starting the assessment."))
		if attempt.status not in {"Consent Accepted", "Started"}:
			frappe.throw(_("Assessment cannot be started for this attempt."))
		attempt.started_on = now_datetime()
		attempt.assessment_started_on = attempt.started_on
		attempt.duration_minutes = assignment.duration_minutes
		attempt.ends_on = minutes_from_now(assignment.duration_minutes)
		attempt.status = "Started"
		attempt.recording_status = "Recording"
		attempt.camera_status = "Active"
		attempt.microphone_status = "Active"
		attempt.screen_status = "Active"
		attempt.fullscreen_status = "Active"
		attempt.resume_allowed = 1 if assignment.allow_resume_after_crash else 0
		if not attempt.answers:
			for question in assignment.questions:
				attempt.append(
					"answers",
					{
						"question_bank": question.question_bank,
						"question_text": question.question_text,
						"question_type": question.question_type,
						"marks": question.marks,
						"answer": "",
						"evaluation_status": "Pending Review",
						"is_correct": 0,
						"is_objective": 0,
					},
				)
		attempt.save(ignore_permissions=True)
		assignment.started = 1
		assignment.portal_status = "Started"
		assignment.assessment_state = "Started"
		assignment.current_attempt = attempt.name
		assignment.save(ignore_permissions=True)
		self.sync_job_applicant(assignment)
		log_assessment_audit(attempt.name, "Assessment Started", remarks="Candidate started assessment.")
		return attempt

	def complete_attempt(self, assignment_name: str, attempt_name: str):
		result = self.submit(assignment_name, attempt_name)
		attempt = frappe.get_doc("Assessment Attempt", attempt_name)
		attempt.assessment_completed_on = attempt.submitted_on or now_datetime()
		attempt.recording_status = "Uploaded"
		attempt.camera_status = "Stopped"
		attempt.microphone_status = "Stopped"
		attempt.screen_status = "Stopped"
		attempt.fullscreen_status = "Exited"
		if attempt.started_on and attempt.submitted_on:
			attempt.time_taken_minutes = round(time_diff_in_seconds(attempt.submitted_on, attempt.started_on) / 60, 2)
		attempt.save(ignore_permissions=True)
		log_assessment_audit(attempt.name, "Recording Stopped", remarks="Recording stopped during submission.")
		return result

	def autosave(self, assignment_name: str, attempt_name: str, answers: list[dict], event: str | None = None):
		if frappe.db.get_value("Assessment Assignment", assignment_name, "submitted"):
			frappe.throw(_("Assessment already submitted"))

		# Update only answers that changed. File/video answers belong to upload_answer_file, so
		# autosave never touches those rows (it would race the upload and could wipe the file).
		rows = frappe.get_all(
			"Assessment Answer",
			filters={"parent": attempt_name, "parenttype": "Assessment Attempt"},
			fields=["name", "question_bank", "question_type", "answer"],
		)
		rows_by_question = {row.question_bank: row for row in rows}
		changed = 0
		for answer in answers:
			row = rows_by_question.get(answer.get("question_bank"))
			if not row or row.question_type in {"File Upload", "Image-based Question", "Video Response"}:
				continue
			value = answer.get("answer") or ""
			if (row.answer or "") != value:
				frappe.db.set_value("Assessment Answer", row.name, "answer", value, update_modified=False)
				changed += 1
		if changed:
			frappe.db.set_value("Assessment Attempt", attempt_name, "last_saved_on", now_datetime(), update_modified=False)
		return {"saved": True, "attempt": attempt_name, "changed": changed}

	def submit(self, assignment_name: str, attempt_name: str):
		assignment = frappe.get_doc("Assessment Assignment", assignment_name)
		attempt = frappe.get_doc("Assessment Attempt", attempt_name)
		if assignment.submitted:
			frappe.throw(_("Assessment already submitted"))
		self.evaluate_attempt(attempt)
		attempt.status = "Submitted"
		attempt.submitted_on = now_datetime()
		attempt.assessment_completed_on = attempt.submitted_on
		attempt.save(ignore_permissions=True)
		assignment.submitted = 1
		assignment.portal_status = "Submitted"
		assignment.assessment_state = "Submitted"
		assignment.save(ignore_permissions=True)
		self.sync_job_applicant(assignment)
		log_assessment_audit(attempt.name, "Assessment Submitted", remarks="Assessment submitted.")
		return self.build_result(attempt)

	def evaluate_attempt(self, attempt):
		score = 0.0
		max_score = 0.0
		for answer in attempt.answers:
			question = frappe.get_doc("Question Bank", answer.question_bank)
			max_score += flt(question.marks or 0)
			result = evaluate_answer(question, answer)
			answer.score = result["score"]
			answer.evaluation_status = "Auto Evaluated" if result["is_objective"] else "Pending Review"
			answer.is_correct = 1 if result["is_correct"] else 0
			answer.is_objective = 1 if result["is_objective"] else 0
			score += flt(answer.score)
		percentage = round((score / max_score) * 100, 2) if max_score else 0
		passed = percentage >= flt(attempt.passing_marks or 0)
		attempt.total_score = score
		attempt.maximum_score = max_score
		attempt.percentage = percentage
		attempt.result_status = self.get_result_status(attempt, passed)
		attempt.save(ignore_permissions=True)
		return attempt

	@staticmethod
	def get_result_status(attempt, passed: bool) -> str:
		# Video / written answers need HR review before a pass or fail means anything.
		if any(row.evaluation_status not in {"Evaluated", "Auto Evaluated", "AI Evaluated"} for row in attempt.answers):
			return "Pending Review"
		return "Passed" if passed else "Failed"

	def record_manual_score(self, attempt_name: str, question_bank: str, score: float, remarks: str | None = None):
		"""HR scores one reviewed answer; totals, result and applicant status follow."""
		attempt = frappe.get_doc("Assessment Attempt", attempt_name)
		row = next((answer for answer in attempt.answers if answer.question_bank == question_bank), None)
		if not row:
			frappe.throw(_("Question {0} is not part of this attempt.").format(question_bank))
		score = flt(score)
		if score < 0 or score > flt(row.marks):
			frappe.throw(_("Score must be between 0 and {0}.").format(flt(row.marks)))
		row.score = score
		row.evaluation_status = "Evaluated"
		row.remarks = remarks

		total, maximum, percentage, passed, manual_pending = aggregate_result(attempt)
		attempt.total_score = total
		attempt.maximum_score = maximum
		attempt.percentage = percentage
		attempt.result_status = self.get_result_status(attempt, passed)
		if not manual_pending:
			attempt.status = "Evaluated"
		attempt.save(ignore_permissions=True)

		result_name = frappe.db.get_value("Assessment Result", {"attempt": attempt.name}, "name", order_by="creation desc")
		if result_name:
			frappe.db.set_value(
				"Assessment Result",
				result_name,
				{
					"total_score": total,
					"maximum_score": maximum,
					"percentage": percentage,
					"pass_fail": attempt.result_status,
					"manual_pending_count": manual_pending,
				},
			)
		if not manual_pending:
			assignment = frappe.get_doc("Assessment Assignment", attempt.assignment)
			assignment.portal_status = attempt.result_status
			assignment.assessment_state = attempt.result_status
			assignment.save(ignore_permissions=True)
			self.sync_job_applicant(assignment)
		else:
			set_job_applicant_values(attempt.job_applicant, {"assessment_score": percentage})
		return {
			"total_score": total,
			"maximum_score": maximum,
			"percentage": percentage,
			"result_status": attempt.result_status,
			"manual_pending_count": manual_pending,
		}

	def finalize_expired_assignment(self, assignment_name: str):
		assignment = frappe.get_doc("Assessment Assignment", assignment_name)
		if assignment.submitted:
			return assignment
		if assignment.valid_till and assignment.valid_till < now_datetime():
			assignment.portal_status = "Expired"
			assignment.assessment_state = "Expired"
			assignment.save(ignore_permissions=True)
			self.sync_job_applicant(assignment)
		return assignment

	def build_result(self, attempt):
		total, maximum, percentage, passed, manual_pending = aggregate_result(attempt)
		result = frappe.new_doc("Assessment Result")
		result.assignment = attempt.assignment
		result.attempt = attempt.name
		result.job_applicant = attempt.job_applicant
		result.template = attempt.template
		result.total_score = total
		result.maximum_score = maximum
		result.percentage = percentage
		result.pass_fail = "Pending Review" if manual_pending else ("Passed" if passed else "Failed")
		result.manual_pending_count = manual_pending
		result.time_taken_minutes = attempt.time_taken_minutes or 0
		result.insert(ignore_permissions=True)
		return result

	def record_violation(
		self,
		assignment_name: str,
		attempt_name: str | None,
		event_type: str,
		payload: dict | None = None,
		severity: str = "Medium",
		remarks: str | None = None,
		screenshot: str | None = None,
	):
		event = log_security_event(assignment_name, attempt_name, event_type, payload, severity=severity, remarks=remarks, screenshot=screenshot)
		if attempt_name:
			status_updates = {}
			if event_type in {"Screen sharing stopped", "Screen Share Stopped"}:
				status_updates = {"screen_status": "Paused", "pause_reason": "Screen sharing stopped"}
			elif event_type in {"Camera disabled", "Camera disconnected", "Camera Stopped"}:
				status_updates = {"camera_status": "Stopped", "pause_reason": "Camera stopped"}
			elif event_type in {"Microphone muted", "Microphone disconnected", "Microphone Stopped"}:
				status_updates = {"microphone_status": "Warning"}
			elif event_type in {"Fullscreen exited", "Fullscreen Exited"}:
				status_updates = {"fullscreen_status": "Exited"}
			elif event_type in {"Network disconnected", "Internet Disconnected"}:
				status_updates = {"pause_reason": "Network disconnected"}
			if status_updates:
				frappe.db.set_value("Assessment Attempt", attempt_name, status_updates, update_modified=False)
			set_job_applicant_values(
				frappe.db.get_value("Assessment Attempt", attempt_name, "job_applicant"),
				{"assessment_violation_count": frappe.db.get_value("Assessment Attempt", attempt_name, "violation_count")},
			)
		return {"name": event.name, "violation_count": frappe.db.get_value("Assessment Attempt", attempt_name, "violation_count") if attempt_name else 0}

	def record_audit(self, assignment_name: str, attempt_name: str, event_type: str, remarks: str | None = None, payload: dict | None = None):
		attempt = frappe.get_doc("Assessment Attempt", attempt_name)
		if attempt.assignment != assignment_name:
			frappe.throw(_("Invalid attempt for this assignment"))
		return {"name": log_assessment_audit(attempt.name, event_type, remarks=remarks, payload=payload).name}

	def heartbeat(self, assignment_name: str, attempt_name: str, payload: dict | None = None):
		if frappe.db.get_value("Assessment Attempt", attempt_name, "assignment") != assignment_name:
			frappe.throw(_("Invalid attempt for this assignment"))
		payload = payload or {}
		values = {"last_heartbeat_on": now_datetime(), "heartbeat_json": frappe.as_json(payload)}
		for key, fieldname, on, off in [
			("recording_active", "recording_status", "Recording", "Failed"),
			("camera_active", "camera_status", "Active", "Stopped"),
			("microphone_active", "microphone_status", "Active", "Stopped"),
			("screen_active", "screen_status", "Active", "Stopped"),
			("fullscreen_active", "fullscreen_status", "Active", "Exited"),
		]:
			if payload.get(key) is not None:
				values[fieldname] = on if payload.get(key) else off
		frappe.db.set_value("Assessment Attempt", attempt_name, values, update_modified=False)
		return {"ok": True, "server_time": now_datetime(), "status": frappe.db.get_value("Assessment Attempt", attempt_name, "status")}

	def set_recording_file(self, attempt_name: str, recording_type: str, file_url: str):
		field_map = {
			"Screen": "screen_recording_file",
			"Webcam": "camera_recording_file",
			"Camera": "camera_recording_file",
			"Microphone": "microphone_recording_file",
		}
		fieldname = field_map.get(recording_type)
		if fieldname:
			frappe.db.set_value(
				"Assessment Attempt", attempt_name, {fieldname: file_url, "recording_status": "Uploaded"}, update_modified=False
			)
