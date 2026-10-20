(function () {
	function apply_template_defaults(dialog, template) {
		if (!template) return;
		frappe.db
			.get_value("Assessment Template", template, ["default_duration_minutes", "default_validity_days", "default_passing_marks"])
			.then(({ message }) => {
				if (!message) return;
				dialog.set_value("duration_minutes", message.default_duration_minutes || 60);
				dialog.set_value("passing_marks", message.default_passing_marks || 0);
				dialog.set_value(
					"assessment_expiry_date",
					frappe.datetime.add_days(frappe.datetime.now_datetime(), message.default_validity_days || 3)
				);
			});
	}

	// default_template comes from the Job Opening (or Assessment Settings); HR can change it here.
	function open_assign_dialog(applicant_names, default_template) {
		const dialog = new frappe.ui.Dialog({
			title: __("Assign Assessment"),
			fields: [
				{
					fieldname: "template",
					fieldtype: "Link",
					label: __("Assessment Template"),
					options: "Assessment Template",
					reqd: 1,
					default: default_template || "",
					get_query: () => ({ filters: { is_active: 1 } }),
					onchange() {
						apply_template_defaults(dialog, this.get_value());
					},
				},
				{
					fieldname: "email_template",
					fieldtype: "Link",
					label: __("Email Template"),
					options: "Email Template",
				},
				{ fieldname: "assessment_expiry_date", fieldtype: "Datetime", label: __("Assessment Expiry Date"), reqd: 1 },
				{ fieldname: "duration_minutes", fieldtype: "Int", label: __("Assessment Duration"), default: 60, reqd: 1 },
				{ fieldname: "send_email", fieldtype: "Check", label: __("Send Email"), default: 1 },
				{ fieldname: "generate_secure_link", fieldtype: "Check", label: __("Generate Secure Link"), default: 1, read_only: 1 },
				{
					fieldname: "selected_applicants",
					fieldtype: "HTML",
					options: `<div class="text-muted">${__("Selected Applicants")}: ${applicant_names.map(frappe.utils.escape_html).join(", ")}</div>`,
				},
				{ fieldname: "proctoring_section", fieldtype: "Section Break", label: __("Proctoring Policy") },
				{ fieldname: "passing_marks", fieldtype: "Float", label: __("Passing Marks (%)"), default: 0 },
				{ fieldname: "randomize_questions", fieldtype: "Check", label: __("Randomize Questions"), default: 1 },
				{ fieldname: "shuffle_options", fieldtype: "Check", label: __("Shuffle Options"), default: 1 },
				{ fieldname: "auto_save", fieldtype: "Check", label: __("Auto Save"), default: 1 },
				{ fieldname: "auto_submit", fieldtype: "Check", label: __("Auto Submit"), default: 1 },
				{ fieldname: "webcam_recording", fieldtype: "Check", label: __("Webcam Recording"), default: 1 },
				{ fieldname: "webcam_snapshots", fieldtype: "Check", label: __("Webcam Snapshots"), default: 1 },
				{ fieldname: "microphone_recording", fieldtype: "Check", label: __("Microphone Recording"), default: 1 },
				{ fieldname: "screen_recording", fieldtype: "Check", label: __("Screen Recording"), default: 1 },
				{ fieldname: "fullscreen_enforcement", fieldtype: "Check", label: __("Fullscreen Enforcement"), default: 1 },
			],
			primary_action_label: __("Assign & Send Email"),
			primary_action(values) {
				values.send_email = 1;
				assign(values);
			},
		});

		function assign(values) {
			if (!values.assessment_expiry_date) {
				frappe.msgprint(__("Assessment Expiry Date is required."));
				return;
			}
				frappe.call({
					method: "grid_erp.recruitment_assessment.api.assign_assessment",
					args: {
						job_applicants: JSON.stringify(applicant_names),
						template: values.template,
						assessment_expiry_date: values.assessment_expiry_date,
						duration_minutes: values.duration_minutes,
						passing_marks: values.passing_marks,
						randomize_questions: values.randomize_questions ? 1 : 0,
						shuffle_options: values.shuffle_options ? 1 : 0,
						auto_save: values.auto_save ? 1 : 0,
						auto_submit: values.auto_submit ? 1 : 0,
						email_notifications: values.send_email ? 1 : 0,
						send_email: values.send_email ? 1 : 0,
						email_template: values.email_template,
						webcam_recording: values.webcam_recording ? 1 : 0,
						webcam_snapshots: values.webcam_snapshots ? 1 : 0,
						microphone_recording: values.microphone_recording ? 1 : 0,
						screen_recording: values.screen_recording ? 1 : 0,
						fullscreen_enforcement: values.fullscreen_enforcement ? 1 : 0,
					},
					callback: function (r) {
						const result = r.message || {};
						dialog.hide();
						frappe.msgprint(
							__("Assigned: {0}<br>Emails Sent: {1}<br>Skipped: {2}<br>Failed: {3}", [
								result.assigned || 0,
								result.emails_sent || 0,
								result.skipped || 0,
								result.failed || 0,
							])
						);
						if (window.cur_frm) window.cur_frm.reload_doc();
						else frappe.refresh_list();
					},
				});
		}

		dialog.show();
		apply_template_defaults(dialog, default_template);
		dialog.get_primary_btn().removeClass("btn-primary").addClass("btn-success");
		dialog.set_secondary_action_label(__("Assign Only"));
		dialog.set_secondary_action(() => {
			const values = dialog.get_values();
			if (!values) return;
			values.send_email = 0;
			assign(values);
		});
	}

	function sync_assessment_connection(frm) {
		if (frm.doc.__islocal) return;

		frappe.call({
			method: "grid_erp.recruitment_assessment.workflow.get_job_applicant_assessment_overview",
			args: { job_applicant: frm.doc.name },
			callback: function (r) {
				const info = r.message || {};
				frm.get_field("assessment_summary_html")?.$wrapper.html(info.summary_html || "");

				// Always available, so HR can send any template manually (even a second one).
				frm.add_custom_button(
					info.assignment ? __("Assign Another Assessment") : __("Assign Assessment"),
					() => open_assign_dialog([frm.doc.name], info.assignment ? "" : info.template),
					__("Assessment")
				);

				if (info.assignment) {
					frm.add_custom_button(__("View Assessment"), () => {
						frappe.set_route("Form", "Assessment Assignment", info.assignment);
					}, __("Assessment"));
				}

				if (info.attempt) {
					frm.add_custom_button(__("View Attempt"), () => {
						frappe.set_route("Form", "Assessment Attempt", info.attempt);
					}, __("Assessment"));
				}

				if (info.recording) {
					frm.add_custom_button(__("View Recording"), () => {
						window.open(info.recording, "_blank", "noopener");
					}, __("Assessment"));
				}

				if (info.assignment && ["Assigned", "Pending", "Started"].includes(info.portal_status)) {
					frm.add_custom_button(__("Send Reminder"), () => {
						frappe.call({
							method: "grid_erp.recruitment_assessment.api.send_reminder",
							args: { job_applicant: frm.doc.name },
							callback: () => frappe.show_alert({ message: __("Reminder queued"), indicator: "green" }),
						});
					}, __("Assessment"));
				}

				if (!info.assignment && frm.doc.status === "Shortlisted" && info.template) {
					frm.add_custom_button(__("Send {0}", [info.template_name || info.template]), () => {
						frappe.call({
							method: "grid_erp.recruitment_assessment.workflow.ensure_job_applicant_assessment",
							args: { job_applicant: frm.doc.name },
							callback: function () {
								frappe.msgprint(__("Assessment assigned. The link is emailed to the applicant if notifications are enabled."));
								frm.reload_doc();
							},
						});
					}, __("Assessment"));
				}
			},
		});
	}

	frappe.ui.form.on("Job Applicant", {
		refresh(frm) {
			sync_assessment_connection(frm);
		},
	});

	frappe.recruitment_assessment = frappe.recruitment_assessment || {};
	frappe.recruitment_assessment.open_assign_dialog = open_assign_dialog;
})();
