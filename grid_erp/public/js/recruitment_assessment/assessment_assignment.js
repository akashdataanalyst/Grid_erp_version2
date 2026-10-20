frappe.ui.form.on("Assessment Assignment", {
	refresh(frm) {
		if (!frm.doc.__islocal && frm.doc.job_applicant) {
			frm.add_custom_button(__("Open Job Applicant"), () => {
				frappe.set_route("Form", "Job Applicant", frm.doc.job_applicant);
			});

			frm.add_custom_button(__("Open Candidate Portal"), () => {
				frappe.call({
					method: "grid_erp.recruitment_assessment.workflow.get_job_applicant_assessment_overview",
					args: { job_applicant: frm.doc.job_applicant },
					callback(r) {
						const portal_link = r.message && r.message.portal_link;
						if (portal_link) {
							window.open(portal_link, "_blank", "noopener");
							return;
						}
						frappe.msgprint(__("Portal link is not available yet."));
					},
				});
			});

			// ── WhatsApp: Send via API ─────────────────────────────────
			frm.add_custom_button(__("📲 Send via WhatsApp (API)"), () => {
				const d = new frappe.ui.Dialog({
					title: __("Send Assessment Link via WhatsApp"),
					fields: [
						{
							fieldname: "phone",
							fieldtype: "Data",
							label: __("Candidate Phone (with country code)"),
							default: frm.doc.candidate_phone || "",
							reqd: 1,
							description: __("Example: +919876543210"),
						},
						{
							fieldname: "message",
							fieldtype: "Text",
							label: __("Message"),
							default: `Hello ${frm.doc.applicant_name},\n\nYou have been assigned an assessment: ${frm.doc.template}\n\nPlease start it before: ${frm.doc.valid_till || "N/A"}\n\nLink: ${frm.doc.portal_link || ""}`,
						},
					],
					primary_action_label: __("Send"),
					primary_action(values) {
						d.hide();
						frappe.show_alert({ message: __("Sending WhatsApp…"), indicator: "blue" });
						frappe.call({
							method: "grid_erp.recruitment_assessment.whatsapp.send_whatsapp_assessment_link",
							args: {
								assignment_name: frm.doc.name,
								custom_message: values.message,
							},
							callback(r) {
								if (r.message && r.message.success) {
									frappe.show_alert({ message: r.message.message, indicator: "green" }, 5);
								} else {
									frappe.msgprint({
										message: (r.message && r.message.message) || __("Failed to send WhatsApp."),
										indicator: "red",
									});
								}
							},
						});
					},
				});
				d.show();
			}, __("Notify"));

			// ── WhatsApp: Open wa.me link (no API needed) ──────────────
			frm.add_custom_button(__("💬 Open WhatsApp (wa.me)"), () => {
				const phone = (frm.doc.candidate_phone || "").replace(/\D/g, "");
				if (!phone) {
					frappe.msgprint(__("No phone number on this assignment."));
					return;
				}
				const portalLink = frm.doc.portal_link || frm.doc.name;
				const text = encodeURIComponent(
					`Hello ${frm.doc.applicant_name}, please complete your assessment: ${portalLink}`
				);
				window.open(`https://wa.me/${phone}?text=${text}`, "_blank", "noopener");
			}, __("Notify"));
		}

		if (frm.doc.portal_status === "Submitted" || frm.doc.portal_status === "Evaluated") {
			frm.add_custom_button(__("View Result"), () => {
				frappe.set_route("List", "Assessment Result", { assignment: frm.doc.name });
			});
		}

		if (!frm.doc.__islocal && (frappe.user.has_role("HR Manager") || frappe.user.has_role("System Manager"))) {
			frm.add_custom_button(__("Delete with Attempts"), () => confirm_delete_with_attempts(frm), __("Actions"));
		}
	},
});

// Normal delete is blocked because Assignment and Attempt link to each other (see cleanup.py).
function confirm_delete_with_attempts(frm) {
	const CLEANUP = "grid_erp.recruitment_assessment.cleanup";
	frappe.call({
		method: `${CLEANUP}.get_delete_summary`,
		args: { assignment: frm.doc.name },
		callback(r) {
			const info = r.message || {};
			const counts = info.counts || {};
			const esc = frappe.utils.escape_html;
			const rows = [
				[__("Assessment Attempt (answers, violations, audit log)"), counts["Assessment Attempt"]],
				[__("Assessment Result"), counts["Assessment Result"]],
				[__("Assessment Snapshot"), counts["Assessment Snapshot"]],
				[__("Assessment Recording"), counts["Assessment Recording"]],
				[__("Assessment Notification"), counts["Assessment Notification"]],
				[__("Files (videos, recordings, snapshots)"), info.files],
			]
				.map(([label, count]) => `<tr><td>${label}</td><td style="text-align:right"><b>${count || 0}</b></td></tr>`)
				.join("");

			let html = `
				<p>${__("This permanently deletes assignment <b>{0}</b> of <b>{1}</b> and everything recorded under it:", [esc(frm.doc.name), esc(info.applicant || "")])}</p>
				<table class="table table-bordered table-sm">${rows}</table>
				<p class="text-muted">${__("The Job Applicant goes back to Not Assigned. This cannot be undone.")}</p>`;
			if (info.has_final_result) {
				html += `<div class="alert alert-danger">${__("This candidate already has a final result (Passed / Failed). This is a real candidate's hiring record. Delete only if it was assigned by mistake or is test data. To give another test, use Assign Another Assessment instead.")}</div>`;
			}
			if (info.resend_on_save) {
				html += `<div class="alert alert-warning">${__("The applicant is Shortlisted and auto-send is on: the next time the Job Applicant is saved, a new assessment email goes out automatically.")}</div>`;
			}

			// Delete stays disabled until HR ticks the confirmation.
			const dialog = new frappe.ui.Dialog({
				title: __("Delete Assignment with Attempts?"),
				indicator: "red",
				fields: [
					{ fieldname: "summary", fieldtype: "HTML", options: html },
					{
						fieldname: "confirm",
						fieldtype: "Check",
						label: __("I have checked everything above and want to delete it permanently"),
					},
				],
				primary_action_label: __("Delete Permanently"),
				primary_action() {
					if (!is_confirmed()) {
						frappe.show_alert({ message: __("Tick the confirmation checkbox first."), indicator: "orange" });
						return;
					}
					dialog.hide();
					frappe.call({
						method: `${CLEANUP}.delete_assignment_with_attempts`,
						args: { assignment: frm.doc.name, confirm: 1 },
						freeze: true,
						freeze_message: __("Deleting…"),
						callback() {
							frappe.show_alert({ message: __("Assignment {0} deleted", [frm.doc.name]), indicator: "green" });
							if (frm.doc.job_applicant) frappe.set_route("Form", "Job Applicant", frm.doc.job_applicant);
							else frappe.set_route("List", "Assessment Assignment");
						},
					});
				},
			});
			const $confirm = dialog.fields_dict.confirm.$input;
			const is_confirmed = () => $confirm.prop("checked");
			const $delete = dialog.get_primary_btn().removeClass("btn-primary").addClass("btn-danger");
			// Read the checkbox itself: the field's onchange does not fire reliably inside a Dialog.
			$confirm.on("change", () => $delete.prop("disabled", !is_confirmed()));
			dialog.show();
			$delete.prop("disabled", true);
		},
	});
}
