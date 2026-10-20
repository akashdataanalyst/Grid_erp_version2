import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now


class WhatsAppNotificationLog(Document):
	@staticmethod
	def clear_old_logs(days=180):
		"""Called by Log Settings. Messages still being sent are kept."""
		table = frappe.qb.DocType("WhatsApp Notification Log")
		frappe.db.delete(
			table,
			filters=(table.creation < (Now() - Interval(days=days))) & table.status.notin(["Queued", "Processing"]),
		)
