frappe.ui.form.on("Job Opening", {
	refresh(frm) {
		if (!frm.doc.__islocal && frm.doc.assessment_template) {
			frm.add_custom_button(__("Open Assessment Template"), () => {
				frappe.set_route("Form", "Assessment Template", frm.doc.assessment_template);
			});
		}
	},
});

