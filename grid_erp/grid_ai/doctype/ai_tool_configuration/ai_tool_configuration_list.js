// Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
// For license information, please see license.txt

frappe.listview_settings["AI Tool Configuration"] = {
	add_fields: ["enabled", "is_sensitive"],
	get_indicator(doc) {
		if (doc.enabled) return [__("Enabled"), "green", "enabled,=,1"];
		if (doc.is_sensitive) return [__("Sensitive"), "orange", "is_sensitive,=,1"];
		return [__("Disabled"), "gray", "enabled,=,0"];
	},
	onload(listview) {
		listview.page.add_inner_button(__("Discover DocTypes"), () => {
			frappe.call({
				method: "grid_erp.grid_ai.doctype.ai_tool_configuration.ai_tool_configuration.discover_doctypes",
				freeze: true,
				callback(r) {
					frappe.show_alert({
						message: __("{0} new DocTypes discovered (disabled).", [r.message.created]),
						indicator: "green",
					});
					listview.refresh();
				},
			});
		});
	},
};
