frappe.listview_settings["Job Applicant"] = {
	add_fields: ["status", "assessment_state"],

	get_indicator(doc) {
		const map = {
			"Not Assigned": ["gray", "assessment_state,=,Not Assigned"],
			Assigned: ["blue", "assessment_state,=,Assigned"],
			"In Progress": ["orange", "assessment_state,=,In Progress"],
			Completed: ["green", "assessment_state,=,Completed"],
			Expired: ["red", "assessment_state,=,Expired"],
		};
		const state = doc.assessment_state;
		if (state && map[state]) return [state, ...map[state].slice(1), "Assessment"];
	},

	onload(listview) {
		// ── Bulk Assign Assessment button ──────────────────────────────
		listview.page.add_action_item(__("📋 Bulk Assign Assessment"), () => {
			const selected = listview.get_checked_items();
			if (!selected.length) {
				frappe.msgprint(__("Please select at least one applicant."));
				return;
			}

			const names = selected.map((r) => r.name);

			// Get templates for dialog
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Assessment Template",
					filters: { is_active: 1 },
					fields: ["name"],
					limit: 100,
				},
				callback(r) {
					const templates = (r.message || []).map((t) => t.name);
					if (!templates.length) {
						frappe.msgprint(__("No active Assessment Templates found."));
						return;
					}

					const d = new frappe.ui.Dialog({
						title: __("Bulk Assign Assessment to {0} Applicants", [names.length]),
						fields: [
							{
								fieldname: "template",
								fieldtype: "Link",
								options: "Assessment Template",
								label: __("Assessment Template"),
								reqd: 1,
								get_query() {
									return { filters: { is_active: 1 } };
								},
							},
							{ fieldtype: "Column Break" },
							{
								fieldname: "validity_days",
								fieldtype: "Int",
								label: __("Validity (Days)"),
								default: 3,
							},
							{ fieldtype: "Section Break", label: __("Options") },
							{
								fieldname: "duration_minutes",
								fieldtype: "Int",
								label: __("Duration (Minutes)"),
								default: 60,
							},
							{
								fieldname: "passing_marks",
								fieldtype: "Percent",
								label: __("Passing Percentage"),
								default: 0,
							},
							{ fieldtype: "Column Break" },
							{
								fieldname: "webcam_recording",
								fieldtype: "Check",
								label: __("Enable Webcam Recording"),
								default: 0,
							},
							{
								fieldname: "send_email",
								fieldtype: "Check",
								label: __("Send Email Invitation"),
								default: 1,
							},
						],
						primary_action_label: __("Assign Now"),
						primary_action(values) {
							d.hide();
							frappe.show_alert({ message: __("Assigning assessments…"), indicator: "blue" });
							frappe.call({
								method: "grid_erp.recruitment_assessment.bulk_assignment.bulk_assign_assessment",
								args: {
									applicant_names: JSON.stringify(names),
									template: values.template,
									validity_days: values.validity_days || 3,
									duration_minutes: values.duration_minutes || 60,
									passing_marks: values.passing_marks || 0,
									webcam_recording: values.webcam_recording ? 1 : 0,
									send_email: values.send_email ? 1 : 0,
								},
								callback(r) {
									if (r.message) {
										const m = r.message;
										let msg = `<b>${__("Done!")}</b><br>✅ ${m.success_count} ${__("assigned")}`;
										if (m.failed_count) {
											msg += `<br>❌ ${m.failed_count} ${__("failed")}`;
											if (m.failed.length) {
												msg += "<ul>" + m.failed.map(
													(f) => `<li>${f.applicant}: ${frappe.utils.escape_html(f.error)}</li>`
												).join("") + "</ul>";
											}
										}
										frappe.msgprint({ message: msg, indicator: m.failed_count ? "orange" : "green" });
										listview.refresh();
									}
								},
							});
						},
					});
					d.show();
				},
			});
		});
	},
};
