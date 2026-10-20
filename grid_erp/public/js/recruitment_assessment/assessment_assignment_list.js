(function () {
	const original = frappe.listview_settings["Assessment Assignment"] || {};
	frappe.listview_settings["Assessment Assignment"] = Object.assign({}, original, {
		onload(listview) {
			if (original.onload) {
				original.onload(listview);
			}
			listview.page.add_action_item(__("Refresh Statuses"), () => listview.refresh());
		},
	});
})();

