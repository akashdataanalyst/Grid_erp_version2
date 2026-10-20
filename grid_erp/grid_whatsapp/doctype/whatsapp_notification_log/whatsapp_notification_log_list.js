frappe.listview_settings["WhatsApp Notification Log"] = {
	get_indicator(doc) {
		const colors = {
			Queued: "orange",
			Processing: "blue",
			Sent: "green",
			Delivered: "green",
			Read: "green",
			Failed: "red",
			Cancelled: "gray",
		};
		return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
