from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import format_datetime

from .audit import log_notification


DEFAULT_ASSESSMENT_EMAIL = """
<div style="font-family:Arial,sans-serif;color:#172033;line-height:1.55">
	<p>Dear {{ candidate_name }},</p>
	<p>Thank you for applying{% if job_position %} for <b>{{ job_position }}</b>{% endif %}.</p>
	<p>Please complete your online assessment using the secure link below.</p>
	{% if template_description %}<p style="color:#5b667a">{{ template_description }}</p>{% endif %}
	<table cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:18px 0;width:100%;max-width:640px">
		<tr><td style="padding:8px 0;font-weight:700">Assessment:</td><td>{{ assessment_template }}</td></tr>
		<tr><td style="padding:8px 0;font-weight:700">Duration</td><td>{{ duration }}</td></tr>
		<tr><td style="padding:8px 0;font-weight:700">Expiry</td><td>{{ expiry }}</td></tr>
	</table>
	<p>
		<a href="{{ assessment_url }}" style="display:inline-block;background:#2490ef;color:#fff;text-decoration:none;padding:11px 18px;border-radius:6px;font-weight:700">
			Open Secure Assessment
		</a>
	</p>
	<p style="word-break:break-all">{{ assessment_url }}</p>
	<p style="background:#f7f9fc;border:1px solid #dfe4ec;border-radius:6px;padding:10px 12px">
		The link works once. If you need to sign in again, open
		<a href="{{ portal_home }}">{{ portal_home }}</a> and enter:<br>
		<b>Assignment ID:</b> {{ assignment_name }}<br>
		<b>Candidate ID:</b> {{ candidate_email }}<br>
		<b>Temporary Password:</b> {{ temporary_password }}
	</p>
	<p><b>Requirements</b></p>
	<ul>
		<li>Chrome or Edge Browser</li>
		<li>Webcam Required</li>
		<li>Microphone Required</li>
		<li>Screen Sharing Required</li>
		<li>Good Internet Connection</li>
	</ul>
	<p>Regards<br>HR Team</p>
</div>
"""


def build_assessment_email_context(assignment_doc) -> dict:
	template_name, template_description = frappe.db.get_value(
		"Assessment Template", assignment_doc.template, ["template_name", "description"]
	) or (assignment_doc.template, "")
	portal_link = assignment_doc.get_portal_link()
	job_title = (
		frappe.db.get_value("Job Opening", assignment_doc.job_opening, "job_title") if assignment_doc.job_opening else None
	)
	return {
		"assignment_name": assignment_doc.name,
		"portal_home": portal_link.split("?", 1)[0],
		"template_description": frappe.utils.strip_html(template_description or ""),
		"assignment": assignment_doc,
		"applicant_name": assignment_doc.applicant_name,
		"candidate_name": assignment_doc.applicant_name,
		"candidate_email": assignment_doc.candidate_email,
		"designation": assignment_doc.designation or job_title or "",
		"job_position": job_title or assignment_doc.designation,
		"assessment_template": template_name or assignment_doc.template,
		"assessment_name": template_name or assignment_doc.template,
		"duration": _("{0} minutes").format(assignment_doc.duration_minutes or 0),
		"duration_minutes": assignment_doc.duration_minutes or 0,
		"expiry_date": format_datetime(assignment_doc.valid_till) if assignment_doc.valid_till else "",
		"expiry": format_datetime(assignment_doc.valid_till) if assignment_doc.valid_till else "",
		"valid_till": assignment_doc.valid_till,
		"assessment_link": assignment_doc.get_portal_link(),
		"assessment_url": assignment_doc.get_portal_link(),
		"portal_link": assignment_doc.get_portal_link(),
		"temporary_password": assignment_doc.get_secret("temp_password"),
	}


def send_assessment_email(
	assignment_doc,
	subject: str | None = None,
	template_name: str | None = None,
	context: dict | None = None,
):
	context = build_assessment_email_context(assignment_doc) | (context or {})
	subject = subject or _("Online Assessment: {0}").format(context["assessment_name"])
	message = frappe.render_template(DEFAULT_ASSESSMENT_EMAIL, context)

	if template_name:
		email_template = frappe.get_doc("Email Template", template_name)
		subject = frappe.render_template(email_template.subject or subject, context)
		message = frappe.render_template(email_template.response_html or email_template.response or message, context)

	try:
		frappe.sendmail(
			recipients=[assignment_doc.candidate_email],
			subject=subject,
			message=message,
			reference_doctype="Assessment Assignment",
			reference_name=assignment_doc.name,
			now=True,
		)
		if assignment_doc.meta.has_field("email_sent"):
			assignment_doc.db_set("email_sent", 1, update_modified=False)
		log_notification(
			assignment_doc.name,
			subject,
			"Email",
			{key: value for key, value in context.items() if key not in {"temporary_password", "assignment", "assessment_link", "assessment_url", "portal_link"}},
		)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), _("Assessment email failed"))
		return False


def send_assessment_whatsapp(assignment_doc, message: str):
	# Integration placeholder: wire to your WhatsApp provider or ERPNext SMS gateway.
	log_notification(assignment_doc.name, "WhatsApp", "WhatsApp", {"message": message})
	return True
