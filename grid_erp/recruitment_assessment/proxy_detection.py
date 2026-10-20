from __future__ import annotations

"""Anti-proxy / VPN detection for assessment attempts.

Strategy (no paid API required):
1. Check if the IP is from a known datacenter/hosting ASN (free ip-api.com).
2. Check for common VPN/proxy headers set by proxies.
3. Optionally use ip-api.com's proxy/vpn/tor fields (free tier, 45 req/min).

A "violation" is logged to Assessment Violation if proxy/VPN is detected.
The attempt is NOT auto-cancelled — HR reviews violations and decides.
"""

import json
import urllib.request
import urllib.parse

import frappe
from frappe import _
from frappe.utils import now_datetime

from .audit import log_security_event


# ---------------------------------------------------------------------------
# Known datacenter / cloud ASN prefixes (augmented from public blocklists)
# ---------------------------------------------------------------------------
DATACENTER_ASNS = {
    # Amazon AWS
    "AS16509", "AS14618",
    # Google Cloud
    "AS15169",
    # Microsoft Azure
    "AS8075",
    # DigitalOcean
    "AS14061",
    # Linode / Akamai
    "AS63949",
    # Hetzner
    "AS24940",
    # OVH
    "AS16276",
    # Vultr
    "AS20473",
    # Cloudflare
    "AS13335",
    # Leaseweb
    "AS60781",
}

# Headers commonly set by proxies / CDNs
PROXY_HEADERS = [
    "HTTP_VIA",
    "HTTP_X_FORWARDED_FOR",
    "HTTP_FORWARDED",
    "HTTP_X_PROXY_ID",
    "HTTP_PROXY_CONNECTION",
    "HTTP_X_REAL_IP",
]


# ---------------------------------------------------------------------------
# IP Geolocation via ip-api.com (free, no key needed, 45 req/min)
# ---------------------------------------------------------------------------

def _lookup_ip(ip: str) -> dict:
    """Call ip-api.com for VPN/proxy/hosting info. Returns {} on failure."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return {}
    try:
        url = f"http://ip-api.com/json/{urllib.parse.quote(ip)}?fields=status,message,proxy,vpn,tor,hosting,isp,org,as,countryCode,city"
        req = urllib.request.Request(url, headers={"User-Agent": "ERPNext-Assessment/1.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        if data.get("status") == "success":
            return data
    except Exception:
        pass
    return {}


def _has_proxy_headers() -> bool:
    """Check request headers for proxy indicators."""
    try:
        environ = frappe.local.request.environ
        for h in PROXY_HEADERS:
            val = environ.get(h, "")
            if val and val not in ("", "unknown"):
                # X-Forwarded-For with multiple IPs = proxy chain
                if h == "HTTP_X_FORWARDED_FOR" and "," in val:
                    return True
                if h not in ("HTTP_X_FORWARDED_FOR", "HTTP_X_REAL_IP"):
                    return True
    except Exception:
        pass
    return False


def _get_client_ip() -> str:
    try:
        return getattr(frappe.local, "request_ip", None) or \
               frappe.local.request.environ.get("REMOTE_ADDR", "")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------

def check_proxy_vpn(attempt_name: str | None = None, assignment_name: str | None = None) -> dict:
    """Run proxy/VPN detection for the current request.

    Returns a dict:
      detected   – bool
      reason     – human-readable reason list
      ip         – detected IP
      geo        – geo/ASN data from ip-api.com
    """
    ip = _get_client_ip()
    reasons = []

    # 1. Proxy headers
    if _has_proxy_headers():
        reasons.append("Suspicious proxy headers detected")

    # 2. ip-api.com lookup
    geo = _lookup_ip(ip)
    if geo:
        if geo.get("proxy"):
            reasons.append("IP flagged as proxy by ip-api.com")
        if geo.get("vpn"):
            reasons.append("IP flagged as VPN by ip-api.com")
        if geo.get("tor"):
            reasons.append("IP flagged as Tor exit node")
        if geo.get("hosting"):
            reasons.append(f"IP belongs to hosting/datacenter: {geo.get('isp', '')}")
        # Check ASN against known datacenter list
        asn = geo.get("as", "").split(" ")[0].upper()
        if asn and asn in DATACENTER_ASNS:
            reasons.append(f"IP ASN {asn} is a known cloud/datacenter provider")

    detected = bool(reasons)

    result = {
        "detected": detected,
        "reasons": reasons,
        "ip": ip,
        "geo": geo,
        "checked_at": str(now_datetime()),
    }

    # Log violation if detected
    if detected and (attempt_name or assignment_name):
        _log_proxy_violation(attempt_name, assignment_name, result)

    return result


def _log_proxy_violation(attempt_name, assignment_name, result):
    try:
        violation = frappe.new_doc("Assessment Violation")
        violation.attempt = attempt_name or ""
        violation.assignment = assignment_name or ""
        violation.violation_type = "Proxy/VPN Detected"
        violation.details = json.dumps(
            {"reasons": result["reasons"], "ip": result["ip"], "geo": result.get("geo", {})}
        )
        violation.occurred_at = now_datetime()
        violation.insert(ignore_permissions=True)
        frappe.db.commit()

        if attempt_name:
            log_security_event(
                attempt_name,
                "Proxy/VPN Detected",
                f"IP: {result['ip']} | Reasons: {'; '.join(result['reasons'])}",
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Proxy Violation Log Error")


# ---------------------------------------------------------------------------
# Whitelisted API for portal
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True)
def check_proxy_for_attempt(attempt: str | None = None, assignment: str | None = None) -> dict:
    """Called from the assessment portal JS to check the candidate's IP."""
    result = check_proxy_vpn(attempt_name=attempt, assignment_name=assignment)
    # Return minimal info to the client — don't expose full geo data
    return {
        "proxy_detected": result["detected"],
        "reasons": result["reasons"],
        "ip": result["ip"],
    }
