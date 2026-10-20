frappe.ui.form.on("WhatsApp Notification Rule", {
	setup(frm) {
		frm.set_query("document_type", () => ({
			filters: { istable: 0, module: ["!=", "Grid WhatsApp"] },
		}));
		frm.set_query("template", () => ({ filters: { active: 1 } }));
		frm.set_query("whatsapp_instance", () => ({ filters: { active: 1 } }));
	},

	refresh(frm) {
		frm.trigger("load_fields");
		if (frm.is_new()) return;

		frm.add_custom_button(__("Preview"), () => pick_document(frm, __("Preview"), preview));
		frm.add_custom_button(__("Send Now"), () => pick_document(frm, __("Send Now"), send_now));
		frm.add_custom_button(__("View Logs"), () =>
			frappe.set_route("List", "WhatsApp Notification Log", { rule: frm.doc.name })
		);
	},

	document_type(frm) {
		frm.trigger("load_fields");
	},

	load_fields(frm) {
		// Field suggestions for "Field" (On Value Change), from the chosen DocType's metadata.
		const doctype = frm.doc.document_type;
		if (!doctype) return;
		frappe.model.with_doctype(doctype, () => {
			const skip = ["Section Break", "Column Break", "Tab Break", "HTML", "Button", "Table", "Table MultiSelect"];
			const fields = frappe.meta
				.get_docfields(doctype)
				.filter((df) => !skip.includes(df.fieldtype) && df.fieldtype !== "Password")
				.map((df) => ({ value: df.fieldname, label: `${__(df.label || df.fieldname)} (${df.fieldname})` }));
			frm.set_df_property("value_changed_field", "options", fields);
		});
	},
});

function pick_document(frm, title, action) {
	const dialog = new frappe.ui.Dialog({
		title,
		fields: [
			{
				fieldname: "docname",
				fieldtype: "Link",
				options: frm.doc.document_type,
				label: __(frm.doc.document_type),
				reqd: 1,
			},
		],
		primary_action_label: title,
		primary_action({ docname }) {
			dialog.hide();
			action(frm, docname);
		},
	});
	dialog.show();
}

function preview(frm, docname) {
	frappe
		.call("grid_erp.grid_whatsapp.api.notification.preview", { rule: frm.doc.name, docname })
		.then(({ message: r }) => {
			const esc = frappe.utils.escape_html;
			const recipients = r.recipients.length
				? r.recipients
						.map((x) => `<li><b>${esc(x.number)}</b> &middot; ${esc(x.label)} <span class="text-muted">(${esc(x.source)})</span></li>`)
						.join("")
				: `<li class="text-muted">${__("None")}</li>`;
			const errors = r.errors.length
				? `<h5 class="text-danger">${__("Problems")}</h5><ul>${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`
				: "";
			const condition =
				r.condition === null ? __("Error") : r.condition ? __("True (would send)") : __("False (would not send)");
			frappe.msgprint({
				title: __("Preview for {0}", [docname]),
				wide: true,
				message: `
					<p><b>${__("Condition")}:</b> ${condition}</p>
					<h5>${__("Message")}</h5>
					<pre style="white-space: pre-wrap">${esc(r.message || "")}</pre>
					<h5>${__("Recipients")}</h5><ul>${recipients}</ul>
					${errors}`,
			});
		});
}

function send_now(frm, docname) {
	frappe
		.call("grid_erp.grid_whatsapp.api.notification.send_now", { rule: frm.doc.name, docname })
		.then(() => frappe.show_alert({ message: __("Queued. See WhatsApp Notification Log."), indicator: "green" }));
}
