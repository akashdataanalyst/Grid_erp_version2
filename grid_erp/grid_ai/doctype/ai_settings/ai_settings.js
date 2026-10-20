// Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("AI Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Connection"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Please save the settings first."));
				return;
			}
			frappe.call({
				method: "grid_erp.grid_ai.doctype.ai_settings.ai_settings.test_connection",
				freeze: true,
				freeze_message: __("Contacting provider..."),
				callback(r) {
					const res = r.message || {};
					frappe.msgprint({
						title: res.ok ? __("Success") : __("Failed"),
						indicator: res.ok ? "green" : "red",
						message: res.message,
					});
				},
			});
		});
		frm.add_custom_button(__("Open Assistant"), () => frappe.set_route("ai-assistant"));
		load_model_options(frm, false);
	},

	setup(frm) {
		// Keep model ids typed by hand (new or custom models) instead of clearing them.
		frm.get_docfield("model").ignore_validation = 1;
	},

	provider(frm) {
		frappe.call({
			method: "grid_erp.grid_ai.doctype.ai_settings.ai_settings.get_provider_defaults",
			args: { provider: frm.doc.provider },
			callback(r) {
				const defaults = r.message || {};
				if (defaults.api_base_url) frm.set_value("api_base_url", defaults.api_base_url);
			},
		});
		load_model_options(frm, true);
		if (frm.doc.provider !== "Ollama") {
			frappe.show_alert({
				message: __("Enter the {0} API Key and save to load all its models.", [frm.doc.provider]),
				indicator: "blue",
			});
		}
	},
});

// Fill the Model dropdown with the selected provider's models. With `reset`, a model
// that is not in the new provider's list is replaced by that provider's default.
function load_model_options(frm, reset) {
	const provider = frm.doc.provider;
	if (!provider) return;
	frappe.call({
		method: "grid_erp.grid_ai.doctype.ai_settings.ai_settings.get_model_options",
		args: { provider },
		callback(r) {
			if (frm.doc.provider !== provider) return; // provider changed again meanwhile
			const { models = [], default: fallback = "" } = r.message || {};
			const field = frm.get_field("model");
			field.df.options = models.join("\n");
			field.set_data(models);
			if (reset && !models.includes(frm.doc.model)) {
				frm.set_value("model", fallback);
			}
		},
	});
}
