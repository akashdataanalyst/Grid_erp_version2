from __future__ import annotations

"""WhatsApp notification for assessment invitations.

Supports multiple providers:
  - Twilio
  - WATI (WhatsApp Team Inbox)
  - UltraMsg
  - Custom (generic HTTP POST)
"""

import json
import urllib.request
import urllib.parse

import frappe
from frappe import _

from .audit import log_notification


# ---------------------------------------------------------------------------
# Settings helper
# ---------------------------------------------------------------------------

def _settings():
    try:
        return frappe.get_single("Assessment Settings")
    except Exception:
        return None


def _default_message(assignment_doc) -> str:
    return (
        f"Hello {assignment_doc.applicant_name},\n\n"
        f"You have been assigned an online assessment: *{assignment_doc.template}*\n\n"
        f"Duration: {assignment_doc.duration_minutes} minutes\n"
        f"Expiry: {assignment_doc.valid_till or 'N/A'}\n\n"
        f"Click the link below to start your assessment:\n"
        f"{assignment_doc.get_portal_link()}\n\n"
        "Good luck! 🎯"
    )


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _send_via_twilio(phone: str, message: str, settings) -> bool:
    api_url = settings.whatsapp_api_url or ""
    api_key = settings.whatsapp_api_key or ""
    if not api_url or not api_key:
        frappe.throw(_("Twilio credentials not configured in Assessment Settings."))

    # api_url expected format: https://api.twilio.com/2010-04-01/Accounts/<SID>/Messages.json
    # api_key expected format: <SID>:<AUTH_TOKEN>
    sid, _, token = api_key.partition(":")
    auth = urllib.parse.quote(f"{sid}:{token}", safe="")

    payload = urllib.parse.urlencode(
        {"From": "whatsapp:+14155238886", "To": f"whatsapp:{phone}", "Body": message}
    ).encode()

    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={
            "Authorization": "Basic " + __import__("base64").b64encode(f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status in (200, 201)


def _send_via_wati(phone: str, message: str, settings) -> bool:
    api_url = settings.whatsapp_api_url or ""
    api_key = settings.whatsapp_api_key or ""
    template = settings.whatsapp_template_name or ""
    if not api_url or not api_key:
        frappe.throw(_("WATI credentials not configured in Assessment Settings."))

    # Strip leading +
    phone = phone.lstrip("+")

    endpoint = f"{api_url.rstrip('/')}/api/v1/sendTemplateMessage/{phone}"
    payload = json.dumps(
        {
            "template_name": template or "assessment_invitation",
            "broadcast_name": "assessment_invite",
            "parameters": [
                {"name": "message", "value": message},
            ],
        }
    ).encode()

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def _send_via_ultramsg(phone: str, message: str, settings) -> bool:
    api_url = settings.whatsapp_api_url or ""
    api_key = settings.whatsapp_api_key or ""
    if not api_url or not api_key:
        frappe.throw(_("UltraMsg credentials not configured in Assessment Settings."))

    # api_url: https://api.ultramsg.com/<instance_id>
    endpoint = f"{api_url.rstrip('/')}/messages/chat"
    payload = urllib.parse.urlencode(
        {"token": api_key, "to": phone, "body": message}
    ).encode()

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status == 200


def _send_via_custom(phone: str, message: str, settings) -> bool:
    api_url = settings.whatsapp_api_url or ""
    api_key = settings.whatsapp_api_key or ""
    if not api_url:
        frappe.throw(_("Custom WhatsApp API URL not configured in Assessment Settings."))

    payload = json.dumps({"phone": phone, "message": message}).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(api_url, data=payload, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.status in (200, 201)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def send_whatsapp_assessment_link(assignment_name: str, custom_message: str | None = None) -> dict:
    """Send assessment link via WhatsApp for a given assignment.

    Called from the Assessment Assignment form button.
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection({"System Manager", "HR Manager", "HR User", "Recruiter"}):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    assignment = frappe.get_doc("Assessment Assignment", assignment_name)
    phone = assignment.candidate_phone or ""

    if not phone:
        # Try to get from Job Applicant
        phone = frappe.db.get_value("Job Applicant", assignment.job_applicant, "phone_number") or ""

    if not phone:
        frappe.throw(_("Candidate phone number not found on this assignment or Job Applicant."))

    # Normalize phone: ensure + prefix
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")

    message = custom_message or _default_message(assignment)
    settings = _settings()

    provider = (settings.whatsapp_provider if settings else "") or "Custom"

    try:
        if provider == "Twilio":
            ok = _send_via_twilio(phone, message, settings)
        elif provider == "Wati":
            ok = _send_via_wati(phone, message, settings)
        elif provider == "UltraMsg":
            ok = _send_via_ultramsg(phone, message, settings)
        else:
            ok = _send_via_custom(phone, message, settings)

        if ok:
            log_notification(assignment_name, "WhatsApp Assessment Link", "WhatsApp", {"phone": phone})
            # Update flag on assignment if field exists
            if assignment.meta.has_field("whatsapp_sent"):
                assignment.db_set("whatsapp_sent", 1, update_modified=False)
            return {"success": True, "message": _("WhatsApp message sent to {0}").format(phone)}
        else:
            return {"success": False, "message": _("WhatsApp provider returned an error.")}

    except Exception as exc:
        frappe.log_error(frappe.get_traceback(), "WhatsApp Send Error")
        return {"success": False, "message": str(exc)}


def build_whatsapp_web_link(phone: str, text: str) -> str:
    """Generate a wa.me link that opens WhatsApp with pre-filled text."""
    encoded = urllib.parse.quote(text)
    clean_phone = phone.lstrip("+").replace(" ", "").replace("-", "")
    return f"https://wa.me/{clean_phone}?text={encoded}"
