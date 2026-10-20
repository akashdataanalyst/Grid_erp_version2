import frappe


def debug(message, *args):
	"""Write a decision (rule skipped, recipient skipped, ...) to logs/grid_whatsapp.log
	when Debug Logging is on in WhatsApp Settings."""
	if not frappe.db.get_single_value("WhatsApp Settings", "debug_logging", cache=True):
		return
	frappe.logger("grid_whatsapp", allow_site=True).info(message % args if args else message)
