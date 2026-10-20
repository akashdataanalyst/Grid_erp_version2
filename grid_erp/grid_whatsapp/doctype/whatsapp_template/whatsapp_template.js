frappe.ui.form.on("WhatsApp Template", {
	refresh(frm) {
		frm.add_custom_button(__("Detect Variables"), () => detect_variables(frm));
	},
});

function detect_variables(frm) {
	// Adds a Variables row for every {{placeholder}} in the message that has none yet.
	const used = [...(frm.doc.message_body || "").matchAll(/{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}/g)].map((m) => m[1]);
	const defined = (frm.doc.variables || []).map((row) => row.variable_name);
	const added = [...new Set(used)].filter((name) => !defined.includes(name));
	added.forEach((name) => {
		const row = frm.add_child("variables", { variable_name: name, source_type: "Document Field" });
		if (frm.doc.reference_doctype && frappe.meta.has_field(frm.doc.reference_doctype, name)) {
			row.source_field = name;
		}
	});
	frm.refresh_field("variables");
	frappe.show_alert(
		added.length
			? { message: __("Added {0}. Set the Source Field of each.", [added.join(", ")]), indicator: "green" }
			: { message: __("All placeholders already have a variable"), indicator: "blue" }
	);
}
