frappe.ui.form.on("WhatsApp Notification Log", {
	refresh(frm) {
		const can_send = frm.doc.recipient && frm.doc.message;
		if (!can_send || ["Queued", "Processing"].includes(frm.doc.status)) return;
		if (!frappe.user.has_role(["System Manager", "WhatsApp Manager"])) return;

		frappe.db.get_single_value("WhatsApp Settings", "allow_manual_resend").then((allowed) => {
			if (!allowed) return;
			const label = ["Failed", "Cancelled"].includes(frm.doc.status) ? __("Retry Now") : __("Resend");
			frm.add_custom_button(label, () =>
				frappe.confirm(__("Send this WhatsApp message to {0} again?", [frm.doc.recipient]), () =>
					frappe
						.call("grid_erp.grid_whatsapp.api.notification.resend", { log: frm.doc.name })
						.then(({ message: name }) => {
							frappe.show_alert({ message: __("Queued"), indicator: "green" });
							if (name !== frm.doc.name) frappe.set_route("Form", "WhatsApp Notification Log", name);
							else frm.reload_doc();
						})
				)
			);
		});
	},
});
