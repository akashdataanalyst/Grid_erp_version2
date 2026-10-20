"""Installation, metadata and the 'do not modify existing apps' guarantees."""

import json
import os

import frappe

from grid_erp.grid_interview.setup.install import CANDIDATE_ROLE, ROLES
from grid_erp.grid_interview.tests.utils import RollbackTestCase

# The module lives in grid_erp/grid_interview; www/ is grid_erp's.
APP_DIR = frappe.get_app_path("grid_erp")

EXPECTED_DOCTYPES = {
	"Grid Interview Settings",
	"Grid Interview Portal Profile",
	"Grid Interview Video Session",
	"Grid Interview Participant Event",
	"Grid Interview Access Session",
	"Grid Interview Consent",
	"Grid Interview Recording",
	"Grid Interview Transcript",
	"Grid Interview Transcript Segment",
	"Grid Interview Analysis",
	"Grid Interview Analysis Finding",
	"Grid Interview Result",
	"Grid Interview Reference",
	"Grid Interview Notification Log",
	"Grid Interview Processing Job",
	"Grid Interview Audit Log",
}

# Duplicate masters the specification forbids while HRMS is available.
FORBIDDEN_DOCTYPES = {
	"Grid Interview Candidate",
	"Grid Interview Position",
	"Grid Interview Round",
	"Grid Interview Feedback",
	"Grid Interview Decision",
}


class TestInstall(RollbackTestCase):
	def test_module_belongs_to_grid_erp(self):
		# grid_interview was merged into grid_erp (patches/grid_interview/adopt_grid_interview_app).
		self.assertIn("grid_erp", frappe.get_installed_apps())
		self.assertNotIn("grid_interview", frappe.get_installed_apps())
		self.assertEqual(frappe.db.get_value("Module Def", "Grid Interview", "app_name"), "grid_erp")

	def test_all_doctypes_exist_in_own_module(self):
		found = set(frappe.get_all("DocType", {"module": "Grid Interview"}, pluck="name"))
		self.assertEqual(found, EXPECTED_DOCTYPES)

	def test_no_duplicate_masters(self):
		for name in FORBIDDEN_DOCTYPES:
			self.assertFalse(frappe.db.exists("DocType", name), name)

	def test_roles(self):
		for role, desk_access in ROLES:
			self.assertTrue(frappe.db.exists("Role", role), role)
			self.assertEqual(frappe.db.get_value("Role", role, "desk_access"), desk_access, role)
		self.assertEqual(frappe.db.get_value("Role", CANDIDATE_ROLE, "desk_access"), 0)

	def test_candidate_role_has_no_doctype_permissions(self):
		# Candidates only use whitelisted portal APIs that check access explicitly.
		self.assertFalse(frappe.get_all("DocPerm", {"role": CANDIDATE_ROLE}, limit=1))
		self.assertFalse(frappe.get_all("Custom DocPerm", {"role": CANDIDATE_ROLE}, limit=1))

	def test_settings_defaults(self):
		s = frappe.get_single("Grid Interview Settings")
		self.assertEqual(s.join_window_before_minutes, 15)
		self.assertEqual(s.video_provider, "Mock")
		self.assertFalse(s.enable_recording)
		self.assertFalse(s.enable_external_processing)


MODULE_PATH = "grid_erp.grid_interview."


class TestNoModificationOfExistingApps(RollbackTestCase):
	def test_no_hooks_into_standard_doctypes(self):
		# Other grid_erp modules have their own hooks; this module adds none on HRMS / standard DocTypes.
		for hook in ("doc_events", "override_doctype_class", "override_whitelisted_methods", "standard_queries"):
			self.assertNotIn(MODULE_PATH, json.dumps(frappe.get_hooks(hook, app_name="grid_erp")), hook)
		for hook in ("doctype_js", "doctype_list_js"):
			self.assertNotIn("grid_interview", json.dumps(frappe.get_hooks(hook, app_name="grid_erp")), hook)
		for hook in ("permission_query_conditions", "has_permission"):
			for doctype, handlers in frappe.get_hooks(hook, app_name="grid_erp").items():
				if any(h.startswith(MODULE_PATH) for h in handlers):
					self.assertTrue(doctype.startswith("Grid Interview "), f"{hook} on {doctype}")

	def test_no_custom_fields_or_property_setters(self):
		self.assertFalse(frappe.get_all("Custom Field", {"module": "Grid Interview"}, limit=1))
		self.assertFalse(frappe.get_all("Property Setter", {"module": "Grid Interview"}, limit=1))

	def test_doctype_files_only_define_own_doctypes(self):
		doctype_dir = os.path.join(APP_DIR, "grid_interview", "doctype")
		for folder in os.listdir(doctype_dir):
			path = os.path.join(doctype_dir, folder, f"{folder}.json")
			if not os.path.exists(path):
				continue
			with open(path) as fh:
				meta = json.load(fh)
			self.assertTrue(meta["name"].startswith("Grid Interview "), meta["name"])
			self.assertEqual(meta["module"], "Grid Interview")

	def test_www_pages_have_loadable_controllers(self):
		# Frappe looks for <name with - replaced by _>.py next to each page template;
		# a hyphenated .py file is silently ignored and the page renders without context.
		www = os.path.join(APP_DIR, "www")
		for root, _dirs, files in os.walk(www):
			for f in files:
				if f.endswith(".html"):
					expected = f[:-5].replace("-", "_") + ".py"
					self.assertIn(expected, files, os.path.join(root, f))
				if f.endswith(".py") and f != "__init__.py":
					self.assertNotIn("-", f, os.path.join(root, f))

	def test_links_to_hrms_are_dynamic(self):
		# Plain Link fields to HRMS DocTypes would break installation without HRMS.
		hrms_doctypes = {"Interview", "Job Applicant", "Job Opening", "Interview Type", "Interview Feedback"}
		for dt in EXPECTED_DOCTYPES:
			for df in frappe.get_meta(dt).fields:
				if df.fieldtype == "Link":
					self.assertNotIn(df.options, hrms_doctypes, f"{dt}.{df.fieldname}")
