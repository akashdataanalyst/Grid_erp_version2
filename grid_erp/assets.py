"""Serve this app's public/ files through the Python backend.

Frappe normally serves /assets/<app>/ from nginx, which only works when the web
server has a built copy of the app (custom Docker image, `bench build`, symlinks).
Serving through /api/method/ works on any setup where the app is installed:
`bench get-app`, `install-app`, `migrate` and the CSS/JS/images load.

URLs carry a content hash (?v=...), so browsers cache each file for a year and
fetch it again only after the file changes. No manual version bump is needed."""

import hashlib
import mimetypes
import os
from urllib.parse import quote

import frappe
from werkzeug.exceptions import NotFound
from werkzeug.utils import send_file

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
ENDPOINT = "/api/method/grid_erp.assets.serve"
ONE_YEAR = 365 * 24 * 60 * 60


def _version():
	digest = hashlib.sha1()
	for root, dirs, files in os.walk(PUBLIC_DIR):
		dirs.sort()
		for name in sorted(files):
			path = os.path.join(root, name)
			digest.update(os.path.relpath(path, PUBLIC_DIR).encode())
			with open(path, "rb") as f:
				digest.update(f.read())
	return digest.hexdigest()[:12]


# Computed once per process; hooks are re-read after migrate / clear-cache.
VERSION = _version()


def url(path):
	"""Public URL of public/<path>, e.g. url("css/calco_branding.css")."""
	return f"{ENDPOINT}?path={quote(path)}&v={VERSION}"


@frappe.whitelist(allow_guest=True, methods=["GET"])
def serve(path, v=None):
	full_path = os.path.realpath(os.path.join(PUBLIC_DIR, path))
	if not full_path.startswith(PUBLIC_DIR + os.sep) or not os.path.isfile(full_path):
		raise NotFound()

	response = send_file(
		full_path,
		frappe.local.request.environ,
		mimetype=mimetypes.guess_type(full_path)[0] or "application/octet-stream",
		conditional=True,
		max_age=ONE_YEAR if v == VERSION else 0,
	)
	if v == VERSION:
		response.cache_control.immutable = True
	return response
