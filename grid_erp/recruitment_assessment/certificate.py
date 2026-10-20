from __future__ import annotations

"""Generate a PDF certificate for candidates who passed an assessment.

Uses Frappe's built-in Jinja + WeasyPrint/wkhtmltopdf pipeline via
``frappe.utils.pdf.get_pdf``.

Endpoint: GET /api/method/grid_erp.recruitment_assessment.certificate.generate_certificate?attempt=<name>
"""

import frappe
from frappe import _
from frappe.utils import format_datetime, nowdate

CERTIFICATE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Georgia', serif;
    background: #fff;
    color: #1a1a1a;
  }
  .page {
    width: 297mm;
    min-height: 210mm;
    padding: 20mm 24mm;
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border: 12px solid #2490ef;
    outline: 4px solid #ffd700;
    outline-offset: -18px;
  }
  .logo {
    max-height: 80px;
    max-width: 240px;
    object-fit: contain;
    margin-bottom: 16px;
  }
  .company {
    font-size: 22px;
    font-weight: bold;
    color: #2490ef;
    letter-spacing: 2px;
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .title {
    font-size: 42px;
    font-weight: bold;
    color: #1a1a1a;
    margin: 24px 0 8px;
    letter-spacing: 1px;
  }
  .subtitle {
    font-size: 18px;
    color: #555;
    margin-bottom: 28px;
  }
  .recipient {
    font-size: 32px;
    font-weight: bold;
    color: #2490ef;
    border-bottom: 3px solid #2490ef;
    padding-bottom: 6px;
    margin-bottom: 20px;
    text-align: center;
  }
  .body-text {
    font-size: 16px;
    color: #333;
    text-align: center;
    line-height: 1.7;
    max-width: 480px;
  }
  .assessment-name {
    font-size: 20px;
    font-weight: bold;
    color: #1a1a1a;
    margin: 10px 0 4px;
  }
  .score-badge {
    display: inline-block;
    background: #2490ef;
    color: #fff;
    border-radius: 50px;
    padding: 8px 28px;
    font-size: 18px;
    font-weight: bold;
    margin: 16px 0 28px;
    letter-spacing: 1px;
  }
  .meta {
    font-size: 13px;
    color: #888;
    margin-top: 28px;
    text-align: center;
  }
  .footer-text {
    font-size: 12px;
    color: #aaa;
    margin-top: 16px;
    text-align: center;
    font-style: italic;
    max-width: 440px;
  }
  .seal {
    font-size: 60px;
    margin: 12px 0;
  }
</style>
</head>
<body>
<div class="page">
  {% if logo_url %}
  <img class="logo" src="{{ logo_url }}" alt="Logo">
  {% endif %}
  <div class="company">{{ company_name }}</div>
  <div class="title">Certificate of Achievement</div>
  <div class="subtitle">This is to certify that</div>
  <div class="recipient">{{ candidate_name }}</div>
  <div class="body-text">
    has successfully completed the recruitment assessment
  </div>
  <div class="assessment-name">{{ template_name }}</div>
  <div class="score-badge">Score: {{ percentage }}% &nbsp;|&nbsp; {{ result_label }}</div>
  <div class="seal">🏆</div>
  <div class="meta">
    Date Issued: {{ issue_date }}<br>
    Certificate ID: {{ cert_id }}
  </div>
  <div class="footer-text">{{ footer_text }}</div>
</div>
</body>
</html>
"""


def _settings():
    try:
        return frappe.get_single("Assessment Settings")
    except Exception:
        return None


@frappe.whitelist()
def generate_certificate(attempt: str) -> None:
    """Stream a PDF certificate for the given attempt.

    Only accessible to the HR team or the candidate themselves (via session token).
    """
    from frappe.utils.pdf import get_pdf

    # Permission: HR roles or candidate matching the attempt
    attempt_doc = frappe.get_doc("Assessment Attempt", attempt)

    is_hr = bool(
        set(frappe.get_roles(frappe.session.user)).intersection(
            {"System Manager", "HR Manager", "HR User", "Recruiter"}
        )
    )
    is_candidate = attempt_doc.candidate_email == frappe.session.user

    if not is_hr and not is_candidate:
        # Allow access via session token (guest portal)
        token = (
            frappe.get_request_header("X-Assessment-Token")
            or frappe.form_dict.get("session_token")
            or ""
        )
        if not token:
            frappe.throw(_("Not permitted"), frappe.PermissionError)
        from .utils import verify_secret
        assignment = frappe.get_doc("Assessment Assignment", attempt_doc.assignment)
        if not verify_secret(token, assignment.get_secret("portal_session_token_hash")):
            frappe.throw(_("Not permitted"), frappe.PermissionError)

    if not attempt_doc.passed:
        frappe.throw(_("Certificate is only available for candidates who passed the assessment."))

    settings = _settings()
    company_name = (
        (settings.certificate_company_name if settings else None)
        or frappe.db.get_default("company")
        or "Our Company"
    )
    logo_url = (settings.certificate_logo if settings else None) or ""
    footer_text = (
        (settings.certificate_footer_text if settings else None)
        or "This certificate is awarded upon successful completion of the recruitment assessment."
    )

    # Absolute URL for logo
    if logo_url and logo_url.startswith("/"):
        logo_url = frappe.utils.get_url(logo_url)

    context = {
        "candidate_name": attempt_doc.applicant_name or attempt_doc.candidate_email or "Candidate",
        "template_name": attempt_doc.template or "Assessment",
        "percentage": round(attempt_doc.percentage or 0, 1),
        "result_label": "PASSED ✓",
        "issue_date": format_datetime(nowdate(), "dd MMMM yyyy"),
        "cert_id": f"CERT-{attempt_doc.name.replace(' ', '-').upper()}",
        "company_name": company_name,
        "logo_url": logo_url,
        "footer_text": footer_text,
    }

    html = frappe.render_template(CERTIFICATE_HTML, context)
    pdf_content = get_pdf(html, {"orientation": "Landscape", "page-size": "A4"})

    filename = f"Certificate_{attempt_doc.applicant_name or attempt_doc.name}.pdf"

    frappe.local.response.filename = filename
    frappe.local.response.filecontent = pdf_content
    frappe.local.response.type = "pdf"


@frappe.whitelist()
def certificate_available(attempt: str) -> dict:
    """Return whether a certificate is available for this attempt."""
    passed = frappe.db.get_value("Assessment Attempt", attempt, "passed")
    return {"available": bool(passed)}
