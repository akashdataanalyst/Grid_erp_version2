"""Grid Interview was a separate app (`grid_interview`) and is now the "Grid Interview" module of grid_erp.

On a site that had grid_interview installed, this moves what the database records about the app
to grid_erp. No DocType, table or record is deleted: video sessions, recordings, transcripts,
analyses, results, portal profiles and settings stay exactly as they are.

1. Module Def "Grid Interview" belongs to grid_erp.
2. grid_interview leaves the installed apps list. `bench migrate` reads that list before any
   patch runs, so on a bench without apps/grid_interview, run
   `bench --site <site> remove-from-installed-apps grid_interview` before migrating; this step
   then finds nothing to do.

grid_interview had no patches of its own. Its old "Interviews" app icon is removed by
desktop.remove_old_desk_items (REMOVED_APPS).
"""

import frappe

OLD_APP = "grid_interview"
MODULE = "Grid Interview"


def execute():
	if frappe.db.exists("Module Def", MODULE):
		frappe.db.set_value("Module Def", MODULE, "app_name", "grid_erp", update_modified=False)

	if OLD_APP in frappe.get_installed_apps():
		from frappe.installer import remove_from_installed_apps

		remove_from_installed_apps(OLD_APP)
