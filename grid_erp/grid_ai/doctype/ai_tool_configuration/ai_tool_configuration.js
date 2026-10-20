// Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
// For license information, please see license.txt

const GRID_AI_CONFIG_API = "grid_erp.grid_ai.doctype.ai_tool_configuration.ai_tool_configuration";

frappe.ui.form.on("AI Tool Configuration", {
	refresh(frm) {
		if (frm.doc.reference_doctype) {
			frm.add_custom_button(__("Load Fields"), () => load_fields(frm));
			set_fieldname_options(frm);
		}
		if (frm.doc.is_sensitive) {
			frm.dashboard.set_headline_alert(
				__("This DocType looks sensitive. Expose only the fields you really need."),
				"orange"
			);
		}
	},

	reference_doctype(frm) {
		if (!frm.doc.reference_doctype) return;
		if (!frm.doc.label) frm.set_value("label", frm.doc.reference_doctype);
		set_fieldname_options(frm);
		if (!(frm.doc.fields || []).length) load_fields(frm);
	},
});

function load_fields(frm) {
	const replace = () =>
		frappe.call({
			method: `${GRID_AI_CONFIG_API}.suggest_fields`,
			args: { doctype: frm.doc.reference_doctype },
			callback(r) {
				frm.clear_table("fields");
				(r.message || []).forEach((row) => frm.add_child("fields", row));
				frm.refresh_field("fields");
				frappe.show_alert({
					message: __("{0} fields loaded. Review them before enabling.", [(r.message || []).length]),
					indicator: "blue",
				});
			},
		});

	if ((frm.doc.fields || []).length) {
		frappe.confirm(__("Replace the current field list with suggested fields?"), replace);
	} else {
		replace();
	}
}

function set_fieldname_options(frm) {
	frappe.call({
		method: `${GRID_AI_CONFIG_API}.get_field_options`,
		args: { doctype: frm.doc.reference_doctype },
		callback(r) {
			const names = (r.message || []).map((d) => d.fieldname);
			frm.fields_dict.fields.grid.update_docfield_property("fieldname", "options", names);
		},
	});
}
