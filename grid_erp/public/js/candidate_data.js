// Job Applicant > Actions > Delete Candidate Data (grid_erp/candidate_data.py).
// One applicant at a time; only for Grid Interview Administrator / System Manager.
frappe.ui.form.on("Job Applicant", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role(["Grid Interview Administrator", "System Manager"])) return;
		frm.add_custom_button(__("Delete Candidate Data"), () => confirm_delete_candidate_data(frm), __("Actions"));
	},
});

const CANDIDATE_DATA_LABELS = {
	"Assessment Assignment": __("Assessment Assignments (with attempts, results, recordings)"),
	"Interview": __("Interviews (HRMS)"),
	"Interview Feedback": __("Interview Feedback (HRMS)"),
	"Grid Interview Video Session": __("Video Sessions"),
	"Grid Interview Notification Log": __("Notification Logs (emails sent)"),
	"Grid Interview Audit Log": __("Audit Log entries"),
	"Grid Interview Access Session": __("Join links"),
	"Grid Interview Consent": __("Consents"),
	"Grid Interview Recording": __("Recordings"),
	"Grid Interview Transcript": __("Transcripts"),
	"Grid Interview Analysis": __("AI Analyses"),
	"Grid Interview Processing Job": __("Processing Jobs"),
	"Grid Interview Result": __("Results"),
	"Grid Interview Reference": __("References"),
};

function confirm_delete_candidate_data(frm) {
	const API = "grid_erp.candidate_data";
	frappe.call({
		method: `${API}.get_delete_summary`,
		args: { job_applicant: frm.doc.name },
		callback(r) {
			const info = r.message || {};
			const esc = frappe.utils.escape_html;
			const blockers = Object.entries(info.blockers || {});
			if (blockers.length) {
				frappe.msgprint({
					title: __("Cannot delete"),
					indicator: "red",
					message: __("{0} has hiring records: {1}. Delete or cancel those first.", [
						esc(info.applicant || frm.doc.name),
						blockers.map(([dt, names]) => `${esc(dt)} (${names.map(esc).join(", ")})`).join(", "),
					]),
				});
				return;
			}
			const rows = Object.entries(info.counts || {})
				.map(([dt, n]) => `<tr><td>${CANDIDATE_DATA_LABELS[dt] || esc(dt)}</td><td style="text-align:right"><b>${n}</b></td></tr>`)
				.concat([`<tr><td>${__("Files (resume, documents, recordings)")}</td><td style="text-align:right"><b>${info.files || 0}</b></td></tr>`])
				.join("");
			const html = `
				<p>${__("This permanently deletes Job Applicant <b>{0}</b> ({1}) and everything recorded about them:", [esc(frm.doc.name), esc(info.applicant || "")])}</p>
				<table class="table table-bordered table-sm">${rows}</table>
				<div class="alert alert-danger">${__("Only for wrong or test entries. A real candidate's interviews, results and audit trail are hiring records. This cannot be undone.")}</div>
				<p class="text-muted">${__("The candidate's portal login is not deleted (it may be used for other applications).")}</p>`;

			const dialog = new frappe.ui.Dialog({
				title: __("Delete Candidate Data?"),
				indicator: "red",
				fields: [
					{ fieldname: "summary", fieldtype: "HTML", options: html },
					{
						fieldname: "confirm",
						fieldtype: "Data",
						label: __("Type {0} to confirm", [frm.doc.name]),
					},
				],
				primary_action_label: __("Delete Permanently"),
				primary_action(values) {
					if ((values.confirm || "").trim() !== frm.doc.name) {
						frappe.show_alert({ message: __("Type {0} exactly to confirm.", [frm.doc.name]), indicator: "orange" });
						return;
					}
					dialog.hide();
					frappe.call({
						method: `${API}.delete_candidate_data`,
						args: { job_applicant: frm.doc.name, confirm: values.confirm.trim() },
						freeze: true,
						freeze_message: __("Deleting…"),
						callback() {
							frappe.show_alert({ message: __("{0} and all related records deleted", [frm.doc.name]), indicator: "green" });
							frappe.set_route("List", "Job Applicant");
						},
					});
				},
			});
			dialog.get_primary_btn().removeClass("btn-primary").addClass("btn-danger");
			dialog.show();
		},
	});
}
