from __future__ import annotations

import base64
import json
import re
import random

import frappe
from frappe import _
from frappe.utils import flt, now_datetime
from frappe.utils.file_manager import save_file

from .audit import log_assessment_audit, log_security_event
from .integration import assign_assessment_to_applicants
from .notifications import send_assessment_email as queue_assessment_email
from .service import RecruitmentAssessmentService
from .workflow import build_assessment_summary_html
from .utils import retry_on_conflict, verify_secret

service = RecruitmentAssessmentService()


def _parse_json(value, default=None):
	if not value:
		return default if default is not None else {}
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except Exception:
		return default if default is not None else {}


def _normalize_list(value):
	if not value:
		return []
	if isinstance(value, str):
		try:
			parsed = json.loads(value)
		except Exception:
			parsed = [part.strip() for part in value.split(",") if part.strip()]
		else:
			if isinstance(parsed, str):
				parsed = [parsed]
		return list(parsed) if isinstance(parsed, list) else [parsed]
	if isinstance(value, list):
		return value
	return [value]


def _get_session_token():
	return frappe.get_request_header("X-Assessment-Token") or frappe.form_dict.get("session_token") or ""


def _get_assignment_from_session():
	token = _get_session_token()
	if not token:
		frappe.throw(_("Missing assessment session token"))
	return service.get_assignment_by_session(token)


def _client_ip():
	return getattr(frappe.local, "request_ip", None)


def _require_hr_role():
	roles = set(frappe.get_roles(frappe.session.user))
	if not roles.intersection({"System Manager", "HR Manager", "HR User", "Recruiter"}):
		frappe.throw(_("Not permitted"), frappe.PermissionError)


def _parse_int(value, default=0):
	try:
		return int(value)
	except Exception:
		return default


def _parse_bool(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return bool(value)
	return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _save_uploaded_file(attempt: str, file_key: str, fallback_filename: str, data_url: str | None = None):
	file_obj = frappe.request.files.get(file_key) if getattr(frappe.local, "request", None) else None
	if file_obj:
		content = file_obj.stream.read()
		filename = file_obj.filename or fallback_filename
	else:
		if not data_url:
			frappe.throw(_("Missing upload file"))
		match = re.match(r"^data:(?P<mime>[-\w.]+/[-\w.+]+);base64,(?P<data>.+)$", data_url)
		content = base64.b64decode(match.group("data") if match else data_url)
		filename = fallback_filename
	return save_file(filename, content, "Assessment Attempt", attempt, is_private=1)


def _require_attempt_for_assignment(assignment, attempt: str):
	if not attempt:
		frappe.throw(_("Missing attempt"))
	attempt_doc = frappe.get_doc("Assessment Attempt", attempt)
	if attempt_doc.assignment != assignment.name:
		frappe.throw(_("Invalid attempt for this session"))
	return attempt_doc


def _parse_filters(search: str | None = None, status: str | None = None, designation: str | None = None):
	filters = {
		"status": status or "Open",
		"assessment_state": ["!=", "Assigned"],
	}
	if designation:
		filters["designation"] = designation
	or_filters = []
	if search:
		like = f"%{search.strip()}%"
		or_filters = [
			["applicant_name", "like", like],
			["email_id", "like", like],
			["designation", "like", like],
		]
	return filters, or_filters


def _serialize_job_applicant_row(row):
	return {
		"name": row.name,
		"applicant_name": row.applicant_name,
		"email_id": row.email_id,
		"designation": row.designation,
		"status": row.status,
		"assessment_state": row.assessment_state or "Not Assigned",
		"applied_on": row.creation,
	}


def _build_assignment_payload(assignment):
	job_applicant = frappe.get_doc("Job Applicant", assignment.job_applicant)
	return {
		"name": assignment.name,
		"job_applicant": assignment.job_applicant,
		"template": assignment.template,
		"portal_status": assignment.portal_status,
		"assessment_state": assignment.portal_status,
		"portal_link": assignment.get_portal_link(),
		"email_sent": getattr(assignment, "email_sent", 0),
		"summary_html": build_assessment_summary_html(job_applicant, assignment, assignment.template),
	}


def _get_template_for_write(template_name: str):
	_require_hr_role()
	template = frappe.get_doc("Assessment Template", template_name)
	template.check_permission("write")
	return template


def _question_payload(question_name: str):
	question = frappe.get_doc("Question Bank", question_name)
	return {
		"name": question.name,
		"question_title": question.question_title,
		"question_text": question.question_text,
		"category": getattr(question, "category", None),
		"difficulty": question.difficulty,
		"marks": question.marks,
		"correct_answer": question.correct_answer,
		"question_type": question.question_type,
		"options": [
			{
				"option_text": row.option_text,
				"is_correct": row.is_correct,
			}
			for row in question.get("options", [])
		],
	}


@frappe.whitelist()
def search_question_bank(search: str | None = None, category: str | None = None, difficulty: str | None = None, question_type: str | None = None):
	_require_hr_role()
	filters = {"disabled": 0}
	if category:
		filters["category"] = ["like", f"%{category}%"]
	if difficulty:
		filters["difficulty"] = difficulty
	if question_type:
		filters["question_type"] = question_type
	or_filters = None
	if search:
		like = f"%{search}%"
		or_filters = [["question_title", "like", like], ["question_text", "like", like], ["name", "like", like]]
	args = {
		"doctype": "Question Bank",
		"filters": filters,
		"fields": ["name", "question_title", "question_text", "category", "difficulty", "marks", "question_type"],
		"order_by": "modified desc",
		"limit_page_length": 50,
	}
	if or_filters:
		args["or_filters"] = or_filters
	return frappe.get_all(**args)


@frappe.whitelist()
def add_existing_question(template: str, question: str):
	template_doc = _get_template_for_write(template)
	frappe.get_doc("Question Bank", question).check_permission("read")
	if question in [row.question for row in template_doc.get("questions", [])]:
		frappe.throw(_("Question {0} is already attached to this template.").format(question))
	template_doc.append("questions", {"question": question})
	template_doc.save(ignore_permissions=True)
	return template_doc.as_dict()


@frappe.whitelist()
def remove_question_from_template(template: str, question: str):
	template_doc = _get_template_for_write(template)
	template_doc.set("questions", [row for row in template_doc.get("questions", []) if row.question != question])
	template_doc.save(ignore_permissions=True)
	return template_doc.as_dict()


@frappe.whitelist()
def randomize_template_questions(template: str):
	template_doc = _get_template_for_write(template)
	rows = list(template_doc.get("questions", []))
	random.shuffle(rows)
	template_doc.set("questions", [])
	for row in rows:
		template_doc.append("questions", {"question": row.question})
	template_doc.randomize_questions = 1
	template_doc.save(ignore_permissions=True)
	return template_doc.as_dict()


@frappe.whitelist()
def preview_assessment_template(template: str):
	_require_hr_role()
	template_doc = frappe.get_doc("Assessment Template", template)
	template_doc.check_permission("read")
	return {
		"name": template_doc.name,
		"template_name": template_doc.template_name,
		"description": template_doc.description,
		"duration": template_doc.default_duration_minutes,
		"passing_percentage": template_doc.default_passing_marks,
		"total_questions": template_doc.total_questions,
		"total_marks": template_doc.total_marks,
		"question_categories": template_doc.question_categories,
		"questions": [_question_payload(row.question) for row in template_doc.get("questions", []) if row.question],
	}


@frappe.whitelist()
def get_assessment_assignment_candidates(
	search: str | None = None,
	status: str | None = None,
	designation: str | None = None,
	page: int | str | None = 1,
	page_length: int | str | None = 20,
):
	_require_hr_role()
	frappe.has_permission("Job Applicant", "read", throw=True)

	page = max(_parse_int(page, 1), 1)
	page_length = _parse_int(page_length, 20)
	start = (page - 1) * page_length if page_length > 0 else 0
	filters, or_filters = _parse_filters(search=search, status=status, designation=designation)
	args = {
		"doctype": "Job Applicant",
		"filters": filters,
		"fields": ["name", "applicant_name", "email_id", "designation", "status", "assessment_state", "creation"],
		"order_by": "modified desc",
	}
	if or_filters:
		args["or_filters"] = or_filters
	if page_length > 0:
		args["limit_start"] = start
		args["limit_page_length"] = page_length
	rows = frappe.get_all(**args)
	if page_length > 0:
		total = frappe.db.count("Job Applicant", filters=filters)
	else:
		total = len(rows)
	return {
		"rows": [_serialize_job_applicant_row(row) for row in rows],
		"page": page,
		"page_length": page_length,
		"total": total,
		"has_more": page_length > 0 and (start + len(rows) < total),
	}


@frappe.whitelist()
def assign_assessment_bulk(assessment_template: str, job_applicants, **kwargs):
	_require_hr_role()

	template_doc = frappe.get_doc("Assessment Template", assessment_template)
	template_doc.check_permission("read")
	if not template_doc.is_active:
		frappe.throw(_("Assessment Template {0} is inactive").format(template_doc.name))

	applicants = []
	for item in _normalize_list(job_applicants):
		name = str(item).strip()
		if name and name not in applicants:
			applicants.append(name)
	if not applicants:
		frappe.throw(_("Please select at least one Job Applicant"))

	options = {}
	for fieldname in [
		"validity_days",
		"duration_minutes",
		"passing_marks",
		"negative_marking",
		"randomize_questions",
		"shuffle_options",
		"max_attempts",
		"webcam_recording",
		"webcam_snapshots",
		"microphone_recording",
		"screen_recording",
		"fullscreen_enforcement",
		"tab_switch_detection",
		"copy_paste_blocking",
		"right_click_blocking",
		"devtools_detection",
		"snapshot_interval_seconds",
		"screen_grace_period_seconds",
		"camera_grace_period_seconds",
		"microphone_failure_behavior",
		"allow_resume_after_crash",
		"auto_save",
		"auto_submit",
		"email_notifications",
		"send_email",
		"email_template",
		"assessment_expiry_date",
		"whatsapp_notifications",
	]:
		if fieldname in kwargs and kwargs[fieldname] not in (None, ""):
			options[fieldname] = kwargs[fieldname]

	result = {
		"message": _("Assignment Completed"),
		"assigned": 0,
		"emails_sent": 0,
		"skipped": 0,
		"failed": 0,
		"assigned_applicants": [],
		"skipped_applicants": [],
		"failed_applicants": [],
	}

	for idx, applicant_name in enumerate(applicants, start=1):
		savepoint = f"assessment_bulk_{idx}"
		frappe.db.savepoint(savepoint)
		try:
			applicant = frappe.get_doc("Job Applicant", applicant_name)
			applicant.check_permission("write")

			if applicant.status not in service.ALLOWED_ASSIGNMENT_STATUSES:
				result["skipped"] += 1
				result["skipped_applicants"].append(
					{"name": applicant.name, "reason": _("Applicant status {0} is not eligible").format(applicant.status or _("Unknown"))}
				)
				frappe.db.rollback(save_point=savepoint)
				continue

			existing = frappe.get_all(
				"Assessment Assignment",
				filters={"job_applicant": applicant.name, "template": template_doc.name},
				fields=["name", "portal_status"],
				order_by="creation desc",
				limit=1,
			)
			if existing:
				result["skipped"] += 1
				result["skipped_applicants"].append(
					{
						"name": applicant.name,
						"reason": _("Already assigned"),
						"assignment": existing[0].name,
					}
				)
				frappe.db.rollback(save_point=savepoint)
				continue

			assignment = service.create_assignment(applicant.name, template_doc.name, **options)
			result["assigned"] += 1
			if getattr(assignment, "email_sent", 0):
				result["emails_sent"] = result.get("emails_sent", 0) + 1
			result["assigned_applicants"].append(_build_assignment_payload(assignment))
		except frappe.PermissionError as exc:
			frappe.db.rollback(save_point=savepoint)
			result["failed"] += 1
			result["failed_applicants"].append({"name": applicant_name, "reason": str(exc) or _("Permission denied")})
		except Exception as exc:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(frappe.get_traceback(), f"Bulk assessment assignment failed for {applicant_name}")
			result["failed"] += 1
			result["failed_applicants"].append({"name": applicant_name, "reason": str(exc) or _("Unexpected error")})

	return result


@frappe.whitelist()
def generate_secure_link(assignment: str | None = None, job_applicant: str | None = None):
	_require_hr_role()
	if not assignment and job_applicant:
		assignment = frappe.db.get_value(
			"Assessment Assignment",
			{"job_applicant": job_applicant},
			"name",
			order_by="creation desc",
		)
	if not assignment:
		frappe.throw(_("Assessment Assignment is required"))
	doc = frappe.get_doc("Assessment Assignment", assignment)
	doc.check_permission("read")
	return {"assignment": doc.name, "assessment_url": doc.get_portal_link()}


@frappe.whitelist()
def send_assessment_email(assignment: str, email_template: str | None = None):
	_require_hr_role()
	doc = frappe.get_doc("Assessment Assignment", assignment)
	doc.check_permission("read")
	sent = queue_assessment_email(doc, template_name=email_template or getattr(doc, "email_template", None))
	service.sync_job_applicant(doc)
	return {"sent": 1 if sent else 0}


@frappe.whitelist()
def send_reminder(job_applicant: str):
	_require_hr_role()
	assignment = frappe.db.get_value(
		"Assessment Assignment",
		{"job_applicant": job_applicant, "portal_status": ["in", ["Assigned", "Pending", "Started"]]},
		"name",
		order_by="creation desc",
	)
	if not assignment:
		frappe.throw(_("No active assessment assignment found for this applicant."))
	return send_assessment_email(assignment)


@frappe.whitelist(allow_guest=True)
def candidate_auth(assignment: str, candidate_id: str, token: str | None = None, password: str | None = None):
	return service.authenticate_candidate(assignment, candidate_id, token=token, password=password)


@frappe.whitelist(allow_guest=True)
def get_public_assignment_context(assignment: str, token: str | None = None, password: str | None = None):
	if not assignment:
		frappe.throw(_("Missing assignment"))

	assignment_doc = frappe.get_doc("Assessment Assignment", assignment)
	token_ok = bool(token and verify_secret(token, assignment_doc.get_secret("assignment_token_hash")))
	password_ok = bool(password and verify_secret(password, assignment_doc.get_secret("temp_password_hash")))

	if not (token_ok or password_ok):
		frappe.throw(_("Invalid access token or temporary password"))

	return {
		"assignment": assignment_doc.name,
		"candidate_id": assignment_doc.candidate_email or assignment_doc.job_applicant,
		"candidate_name": assignment_doc.applicant_name,
		"assessment_name": frappe.db.get_value("Assessment Template", assignment_doc.template, "template_name") or assignment_doc.template,
		"job_position": assignment_doc.job_opening or assignment_doc.designation,
		"duration_minutes": assignment_doc.duration_minutes,
		"total_questions": len(assignment_doc.questions or []),
		"valid_till": assignment_doc.valid_till,
		"portal_link": assignment_doc.get_portal_link(),
	}


@frappe.whitelist(allow_guest=True)
def candidate_context():
	assignment = _get_assignment_from_session()
	current_attempt = None
	if assignment.current_attempt:
		attempt = frappe.get_doc("Assessment Attempt", assignment.current_attempt)
		current_attempt = {
			"name": attempt.name,
			"status": attempt.status,
			"candidate_consent": attempt.candidate_consent,
			"consent_timestamp": attempt.consent_timestamp,
		}
	return {
		"assignment": assignment.name,
		"applicant_name": assignment.applicant_name,
		"assessment_name": frappe.db.get_value("Assessment Template", assignment.template, "template_name") or assignment.template,
		"job_position": assignment.job_opening or assignment.designation,
		"portal_status": assignment.portal_status,
		"valid_till": assignment.valid_till,
		"duration_minutes": assignment.duration_minutes,
		"total_questions": len(assignment.questions or []),
		"started": getattr(assignment, "started", 0),
		"submitted": getattr(assignment, "submitted", 0),
		"current_attempt": assignment.current_attempt,
		"attempt": current_attempt,
		"settings": {
			"webcam_recording": assignment.webcam_recording,
			"webcam_snapshots": assignment.webcam_snapshots,
			"microphone_recording": assignment.microphone_recording,
			"screen_recording": assignment.screen_recording,
			"fullscreen_enforcement": assignment.fullscreen_enforcement,
			"tab_switch_detection": assignment.tab_switch_detection,
			"copy_paste_blocking": assignment.copy_paste_blocking,
			"right_click_blocking": assignment.right_click_blocking,
			"devtools_detection": assignment.devtools_detection,
			"snapshot_interval_seconds": assignment.snapshot_interval_seconds or 60,
			"screen_grace_period_seconds": assignment.screen_grace_period_seconds or 60,
			"camera_grace_period_seconds": assignment.camera_grace_period_seconds or 60,
			"microphone_failure_behavior": assignment.microphone_failure_behavior or "Warn Only",
			"allow_resume_after_crash": assignment.allow_resume_after_crash,
			"auto_save": assignment.auto_save,
			"auto_submit": assignment.auto_submit,
		},
	}


@frappe.whitelist(allow_guest=True)
def accept_consent(browser_info: str | dict | None = None):
	assignment = _get_assignment_from_session()
	attempt = service.create_consent_attempt(assignment.name, True, _parse_json(browser_info, {}))
	return {
		"name": attempt.name,
		"candidate_consent": attempt.candidate_consent,
		"consent_timestamp": attempt.consent_timestamp,
		"consent_ip": attempt.consent_ip or _client_ip(),
	}


@frappe.whitelist(allow_guest=True)
def reject_consent(browser_info: str | dict | None = None, remarks: str | None = None):
	assignment = _get_assignment_from_session()
	attempt = service.create_consent_attempt(assignment.name, False, _parse_json(browser_info, {}))
	return {
		"name": attempt.name,
		"candidate_consent": attempt.candidate_consent,
		"redirect": "/assessment-portal",
	}


@frappe.whitelist(allow_guest=True)
def get_assessment():
	assignment = _get_assignment_from_session()
	if assignment.valid_till and assignment.valid_till < now_datetime():
		service.finalize_expired_assignment(assignment.name)
		frappe.throw(_("Assessment Expired"))
	response_files = {}
	if assignment.current_attempt:
		response_files = {
			row.question_bank: row.response_file
			for row in frappe.get_doc("Assessment Attempt", assignment.current_attempt).answers
			if row.response_file
		}
	return {
		"response_files": response_files,
		"assignment": assignment.name,
		"template": assignment.template,
		"questions": assignment.questions,
		"portal_status": assignment.portal_status,
		"current_attempt": assignment.current_attempt,
		"valid_till": assignment.valid_till,
		"duration_minutes": assignment.duration_minutes,
	}


@frappe.whitelist(allow_guest=True)
def start_attempt(permissions_confirmed: str | int | bool = 0):
	if not _parse_bool(permissions_confirmed):
		frappe.throw(_("Camera, microphone and screen sharing are mandatory for this assessment."))
	assignment = _get_assignment_from_session()
	if not assignment.current_attempt:
		frappe.throw(_("Consent is required before starting the assessment."))
	attempt = frappe.get_doc("Assessment Attempt", assignment.current_attempt)
	if not attempt.candidate_consent:
		frappe.throw(_("Consent is required before starting the assessment."))
	return service.start_assessment(assignment.name)


@frappe.whitelist(allow_guest=True)
def start_assessment():
	return start_attempt(permissions_confirmed=1)


@frappe.whitelist(allow_guest=True)
def autosave_answers(attempt: str, answers: str | dict | list, event: str | None = None):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	return retry_on_conflict(lambda: service.autosave(assignment.name, attempt, _parse_json(answers, [])))


ANSWER_FILE_QUESTION_TYPES = {"File Upload", "Image-based Question", "Video Response"}


@frappe.whitelist(allow_guest=True)
def upload_answer_file(attempt: str, question_bank: str, duration_seconds: int | None = None):
	"""Store the candidate's file / recorded video for one question on the attempt's answer row."""
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	if assignment.submitted:
		frappe.throw(_("Assessment already submitted"))
	row = frappe.db.get_value(
		"Assessment Answer",
		{"parent": attempt, "parenttype": "Assessment Attempt", "question_bank": question_bank},
		["name", "question_type"],
		as_dict=True,
	)
	if not row or row.question_type not in ANSWER_FILE_QUESTION_TYPES:
		frappe.throw(_("This question does not accept a file answer."))

	duration = _parse_int(duration_seconds, 0)
	extension = "webm" if row.question_type == "Video Response" else "bin"
	file_doc = _save_uploaded_file(attempt, "file", f"{attempt}-{question_bank}.{extension}")
	# Keep the uploaded file even if the row update below has to retry.
	frappe.db.commit()

	def attach_to_answer():
		previous_file = frappe.db.get_value("Assessment Answer", row.name, "response_file")
		values = {"response_file": file_doc.file_url}
		if row.question_type == "Video Response":
			values["answer"] = _("Video recorded ({0} sec)").format(duration)
		frappe.db.set_value("Assessment Answer", row.name, values, update_modified=False)
		# A retake replaces the earlier recording; don't keep orphaned files.
		if previous_file and previous_file != file_doc.file_url:
			for name in frappe.get_all(
				"File",
				filters={"file_url": previous_file, "attached_to_doctype": "Assessment Attempt", "attached_to_name": attempt},
				pluck="name",
			):
				frappe.delete_doc("File", name, ignore_permissions=True)
		log_assessment_audit(
			attempt,
			"Answer File Uploaded",
			remarks=_("{0} answer saved for {1}").format(row.question_type, question_bank),
			payload={"question_bank": question_bank, "file_url": file_doc.file_url, "duration_seconds": duration},
		)
		return {"file_url": file_doc.file_url, "answer": values.get("answer")}

	return retry_on_conflict(attach_to_answer)


@frappe.whitelist(allow_guest=True)
def save_violation(
	event_type: str,
	attempt: str | None = None,
	payload: str | dict | list | None = None,
	severity: str = "Medium",
	remarks: str | None = None,
	screenshot: str | None = None,
):
	assignment = _get_assignment_from_session()
	if attempt:
		_require_attempt_for_assignment(assignment, attempt)
	return retry_on_conflict(
		lambda: service.record_violation(
			assignment.name, attempt, event_type, _parse_json(payload, {}), severity=severity, remarks=remarks, screenshot=screenshot
		)
	)


@frappe.whitelist(allow_guest=True)
def save_audit_log(event_type: str, attempt: str, payload: str | dict | list | None = None, remarks: str | None = None):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	return retry_on_conflict(
		lambda: service.record_audit(assignment.name, attempt, event_type, remarks=remarks, payload=_parse_json(payload, {}))
	)


@frappe.whitelist(allow_guest=True)
def log_violation(event_type: str, attempt: str | None = None, payload: str | dict | list | None = None):
	return save_violation(event_type=event_type, attempt=attempt, payload=payload)


def _record_uploaded_media(attempt: str, recording_type: str, file_key: str, fallback_filename: str, mime_type: str | None = None, duration_seconds: int | None = None):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	file_doc = _save_uploaded_file(attempt, file_key, fallback_filename)
	frappe.db.commit()
	doc = frappe.new_doc("Assessment Recording")
	doc.assignment = assignment.name
	doc.attempt = attempt
	doc.recording_type = recording_type
	doc.file = file_doc.file_url
	doc.mime_type = mime_type or frappe.request.headers.get("Content-Type")
	doc.duration_seconds = duration_seconds or 0
	doc.captured_on = now_datetime()
	doc.insert(ignore_permissions=True)
	frappe.db.commit()
	retry_on_conflict(lambda: service.set_recording_file(attempt, recording_type, file_doc.file_url))
	return {"name": doc.name, "file": file_doc.file_url}


@frappe.whitelist(allow_guest=True)
def upload_screen_recording(attempt: str, mime_type: str | None = None, duration_seconds: int | None = None):
	return _record_uploaded_media(attempt, "Screen", "file", f"{attempt}-screen.webm", mime_type, _parse_int(duration_seconds, 0))


@frappe.whitelist(allow_guest=True)
def upload_camera_recording(attempt: str, mime_type: str | None = None, duration_seconds: int | None = None):
	return _record_uploaded_media(attempt, "Webcam", "file", f"{attempt}-camera.webm", mime_type, _parse_int(duration_seconds, 0))


@frappe.whitelist(allow_guest=True)
def upload_microphone_recording(attempt: str, mime_type: str | None = None, duration_seconds: int | None = None):
	return _record_uploaded_media(attempt, "Microphone", "file", f"{attempt}-microphone.webm", mime_type, _parse_int(duration_seconds, 0))


@frappe.whitelist(allow_guest=True)
def save_snapshot(attempt: str, image_data: str | None = None, reason: str | None = None):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	file_doc = _save_uploaded_file(attempt, "file", f"{attempt}-snapshot-{frappe.generate_hash(length=8)}.jpg", data_url=image_data)
	doc = frappe.new_doc("Assessment Snapshot")
	doc.assignment = assignment.name
	doc.attempt = attempt
	doc.job_applicant = assignment.job_applicant
	doc.snapshot_file = file_doc.file_url
	doc.reason = reason
	doc.captured_on = now_datetime()
	doc.ip_address = _client_ip()
	doc.user_agent = frappe.get_request_header("User-Agent")
	doc.insert(ignore_permissions=True)
	return {"name": doc.name, "file": file_doc.file_url}


@frappe.whitelist(allow_guest=True)
def heartbeat(attempt: str, status: str | dict | None = None):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	return retry_on_conflict(lambda: service.heartbeat(assignment.name, attempt, _parse_json(status, {})))


@frappe.whitelist(allow_guest=True)
def upload_recording(attempt: str, recording_type: str, file_url: str | None = None, external_url: str | None = None, mime_type: str | None = None, duration_seconds: int | None = None):
	assignment = _get_assignment_from_session()
	doc = frappe.new_doc("Assessment Recording")
	doc.assignment = assignment.name
	doc.attempt = attempt
	doc.recording_type = recording_type
	doc.file = file_url
	doc.external_url = external_url
	doc.mime_type = mime_type
	doc.duration_seconds = duration_seconds or 0
	doc.captured_on = now_datetime()
	doc.insert(ignore_permissions=True)
	return {"name": doc.name}


@frappe.whitelist(allow_guest=True)
def complete_attempt(attempt: str):
	assignment = _get_assignment_from_session()
	_require_attempt_for_assignment(assignment, attempt)
	return retry_on_conflict(lambda: service.complete_attempt(assignment.name, attempt))


@frappe.whitelist(allow_guest=True)
def submit_assessment(attempt: str):
	return complete_attempt(attempt)


@frappe.whitelist(allow_guest=True)
def get_results():
	assignment = _get_assignment_from_session()
	return frappe.get_all("Assessment Result", filters={"assignment": assignment.name}, fields=["*"], order_by="creation desc")


@frappe.whitelist()
def assign_assessment(job_applicants: str, template: str, **kwargs):
	return assign_assessment_bulk(template, job_applicants, **kwargs)


@frappe.whitelist()
def get_dashboard_stats(filters: str | dict | None = None):
	_require_hr_role()
	filters = _parse_json(filters, {})
	assignments = frappe.get_all("Assessment Assignment", fields=["name", "portal_status", "template", "job_applicant", "department", "creation", "valid_till", "current_attempt"])
	attempts = frappe.get_all("Assessment Attempt", fields=["name", "status", "percentage", "violation_count", "recording_status", "last_heartbeat_on"])
	live = sum(1 for row in attempts if row.status == "Started")
	completed = [row for row in attempts if row.status in {"Submitted", "Evaluated"}]
	return {
		"pending_assignments": sum(1 for row in assignments if row.portal_status in {"Assigned", "Pending"}),
		"completed": len(completed),
		"in_progress": live,
		"average_score": round(sum(flt(row.percentage or 0) for row in completed) / len(completed), 2) if completed else 0,
		"violations": sum(_parse_int(row.violation_count, 0) for row in attempts),
		"live_candidates": live,
		"assignments": assignments,
		"attempts": attempts,
	}


@frappe.whitelist()
def evaluate_subjective_answer(attempt: str, question_bank: str, score: float, remarks: str | None = None):
	_require_hr_role()
	return service.record_manual_score(attempt, question_bank, score, remarks)
