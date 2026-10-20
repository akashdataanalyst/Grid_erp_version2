"""Grid Branding Settings: serving the values and locking the form.

1. Branding values reach the desk through boot and website/login pages through
   an inline script in <head>, so calco_branding.js renders the configured logo,
   text and colours without a flash of the defaults.
2. Only the Administrator user may edit, and only after entering the branding
   password, which unlocks editing for UNLOCK_SECONDS in that login session."""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils.password import passlibctx

DOCTYPE = "Grid Branding Settings"
EDITOR = "Administrator"
# Only a hash of the edit password is kept, never the password itself. Every site
# uses DEFAULT_PASSWORD_HASH unless site_config.json sets PASSWORD_HASH_KEY.
PASSWORD_HASH_KEY = "grid_branding_password_hash"
DEFAULT_PASSWORD_HASH = "$pbkdf2-sha256$29000$yvnfO8fYew9hTKk1BkBobQ$tLTLWkPD88q8k773qQDSR0jnDz5M3Xf6qcck8wsJlfk"
UNLOCK_SECONDS = 15 * 60

BRAND_FIELDS = {
	"company_name": "companyName",
	"app_name": "appName",
	"login_title": "loginTitle",
	"erp_title": "erpTitle",
	"tagline": "tagline",
	"capabilities": "capabilities",
	"description": "description",
	"footer_line": "footerLine",
	"logo": "logoUrl",
	"favicon": "faviconUrl",
	"banner_image": "bannerUrl",
	"primary_color": "primaryColor",
	"primary_deep_color": "primaryDeepColor",
	"header_color": "headerColor",
	"text_color": "textColor",
	"page_color": "pageColor",
}

TEXT_FIELDS = {
	"login_heading": "loginHeading",
	"login_subtitle_prefix": "loginSubtitlePrefix",
	"authorized_access": "authorizedAccess",
	"secure_connection": "secureConnection",
	"workspace_heading": "workspaceHeading",
}


# ---------------------------------------------------------------------------
# 1. Branding values
# ---------------------------------------------------------------------------


def get_branding():
	"""Only filled-in values are returned; blank fields keep the built-in defaults."""
	try:
		settings = frappe.get_cached_doc(DOCTYPE)
	except Exception:
		# DocType not migrated yet: fall back to the defaults in calco_workspace_config.js.
		return {}

	def pick(fields):
		return {key: settings.get(field).strip() for field, key in fields.items() if (settings.get(field) or "").strip()}

	branding = {"brand": pick(BRAND_FIELDS), "text": pick(TEXT_FIELDS)}

	values = [v.strip() for v in (settings.banner_values or "").split(",") if v.strip()]
	if values:
		branding["text"]["bannerValues"] = values

	order = [line.strip() for line in (settings.workspace_order or "").splitlines() if line.strip()]
	if order:
		branding["workspaceOrder"] = order

	if (settings.app_version or "").strip():
		branding["appVersion"] = settings.app_version.strip()

	return branding


def boot_session(bootinfo):
	bootinfo.calco_branding = get_branding()


def update_website_context(context):
	payload = frappe.as_json(get_branding(), indent=None).replace("</", "<\\/")
	script = f"<script>window.calcoBrandingSettings = {payload};</script>"
	# head_html is rendered on every website page, login included (login.html
	# overrides the head_include block). Keep any Website Settings head HTML.
	return {"head_html": (context.get("head_html") or "") + script}


# ---------------------------------------------------------------------------
# 2. Edit lock
# ---------------------------------------------------------------------------


def has_permission(doc, ptype=None, user=None, debug=False):
	return (user or frappe.session.user) == EDITOR


def ensure_editor():
	if frappe.session.user != EDITOR:
		frappe.throw(_("Only Administrator can change branding settings."), frappe.PermissionError)


def _unlock_key():
	return f"grid_branding_unlocked:{frappe.session.sid}"


def is_unlocked():
	return frappe.session.user == EDITOR and bool(frappe.cache.get_value(_unlock_key()))


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=5, seconds=5 * 60)
def unlock(password):
	"""Unlock editing for this login session for UNLOCK_SECONDS."""
	ensure_editor()
	password_hash = frappe.conf.get(PASSWORD_HASH_KEY) or DEFAULT_PASSWORD_HASH
	if not password or not passlibctx.verify(password, password_hash):
		frappe.throw(_("Incorrect password."), frappe.AuthenticationError)
	frappe.cache.set_value(_unlock_key(), 1, expires_in_sec=UNLOCK_SECONDS)
	return True
