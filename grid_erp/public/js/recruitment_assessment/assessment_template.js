frappe.ui.form.on("Assessment Template", {
	setup(frm) {
		frm.set_query("question", "questions", () => {
			return { filters: { disabled: 0 } };
		});
	},

	refresh(frm) {
		add_template_buttons(frm);
		render_question_preview(frm);
	},

	questions_add(frm) {
		update_local_totals(frm);
	},

	questions_remove(frm) {
		update_local_totals(frm);
		render_question_preview(frm);
	},

	question_filter(frm) {
		render_question_preview(frm);
	},

	category_filter(frm) {
		render_question_preview(frm);
	},

	difficulty_filter(frm) {
		render_question_preview(frm);
	},

	question_type_filter(frm) {
		render_question_preview(frm);
	},
});

frappe.ui.form.on("Assessment Template Question", {
	question(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.question) return;

		frappe.db
			.get_value("Question Bank", row.question, [
				"question_text",
				"category",
				"difficulty",
				"marks",
				"correct_answer",
				"question_type",
			])
			.then((r) => {
				const values = r.message || {};
				Object.keys(values).forEach((fieldname) => {
					frappe.model.set_value(cdt, cdn, fieldname, values[fieldname]);
				});
				update_local_totals(frm);
				render_question_preview(frm);
			});
	},
});

function add_template_buttons(frm) {
	frm.add_custom_button(__("Add Existing Question"), () => show_add_existing_dialog(frm), __("Questions"));
	frm.add_custom_button(__("Create New Question"), () => {
		frappe.new_doc("Question Bank", {
			question_type: "Single Choice MCQ",
			marks: 1,
			disabled: 0,
		});
	}, __("Questions"));
	frm.add_custom_button(__("Remove Question"), () => remove_selected_questions(frm), __("Questions"));
	frm.add_custom_button(__("Preview Assessment"), () => show_assessment_preview(frm), __("Questions"));
	frm.add_custom_button(__("Randomize Questions"), () => randomize_questions(frm), __("Questions"));

	// ── Import Questions from Excel/CSV ──────────────────────────────
	frm.add_custom_button(__("📥 Import from Excel/CSV"), () => show_import_dialog(frm), __("Questions"));

	// ── Download Template ────────────────────────────────────────────
	frm.add_custom_button(__("⬇ Download Import Template"), () => {
		frappe.call({
			method: "grid_erp.recruitment_assessment.question_import.get_import_template",
			callback(r) {
				if (r.message) {
					const blob = new Blob([r.message], { type: "text/csv" });
					const url = URL.createObjectURL(blob);
					const a = document.createElement("a");
					a.href = url;
					a.download = "question_import_template.csv";
					a.click();
					URL.revokeObjectURL(url);
				}
			},
		});
	}, __("Questions"));
}

function show_add_existing_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Add Existing Question"),
		fields: [
			{ fieldname: "search", fieldtype: "Data", label: __("Search") },
			{ fieldname: "category", fieldtype: "Data", label: __("Category") },
			{
				fieldname: "difficulty",
				fieldtype: "Select",
				label: __("Difficulty"),
				options: "\nEasy\nMedium\nHard",
			},
			{
				fieldname: "question_type",
				fieldtype: "Select",
				label: __("Question Type"),
				options:
					"\nSingle Choice MCQ\nMultiple Choice\nTrue/False\nShort Answer\nLong Answer\nCoding Question\nSQL Query\nFile Upload\nImage-based Question\nVideo Response\nRandom Question Bank",
			},
			{
				fieldname: "question",
				fieldtype: "Link",
				label: __("Question"),
				options: "Question Bank",
				reqd: 1,
				get_query() {
					const values = get_dialog_filter_values(dialog);
					const filters = { disabled: 0 };
					if (values.category) filters.category = ["like", `%${values.category}%`];
					if (values.difficulty) filters.difficulty = values.difficulty;
					if (values.question_type) filters.question_type = values.question_type;
					return { filters };
				},
			},
			{ fieldname: "results", fieldtype: "HTML" },
		],
		primary_action_label: __("Add"),
		primary_action(values) {
			frappe.call({
				method: "grid_erp.recruitment_assessment.api.add_existing_question",
				args: { template: frm.doc.name, question: values.question },
				freeze: true,
				callback() {
					dialog.hide();
					frm.reload_doc();
				},
			});
		},
	});

	["search", "category", "difficulty", "question_type"].forEach((fieldname) => {
		dialog.fields_dict[fieldname].df.onchange = () => refresh_question_results(dialog);
	});
	dialog.show();
	refresh_question_results(dialog);
}

function refresh_question_results(dialog) {
	const values = get_dialog_filter_values(dialog);
	frappe.call({
		method: "grid_erp.recruitment_assessment.api.search_question_bank",
		args: values,
		callback(r) {
			const rows = r.message || [];
			const html = rows.length
				? rows
						.map(
							(row) => `
								<div class="assessment-question-result" data-question="${frappe.utils.escape_html(row.name)}">
									<a class="question-select">${frappe.utils.escape_html(row.name)}</a>
									<div>${frappe.utils.escape_html(row.question_text || row.question_title || "")}</div>
									<small>${frappe.utils.escape_html(row.category || "")} · ${frappe.utils.escape_html(
								row.difficulty || ""
							)} · ${frappe.utils.escape_html(row.question_type || "")} · ${frappe.utils.escape_html(
								String(row.marks || 0)
							)} marks</small>
								</div>`
						)
						.join("")
				: `<div class="text-muted">${__("No matching questions found.")}</div>`;
			dialog.fields_dict.results.$wrapper.html(`<div style="max-height: 260px; overflow:auto;">${html}</div>`);
			dialog.fields_dict.results.$wrapper.find(".assessment-question-result").on("click", function () {
				dialog.set_value("question", $(this).data("question"));
			});
		},
	});
}

function get_dialog_filter_values(dialog) {
	return {
		search: dialog.get_value("search"),
		category: dialog.get_value("category"),
		difficulty: dialog.get_value("difficulty"),
		question_type: dialog.get_value("question_type"),
	};
}

function remove_selected_questions(frm) {
	const grid = frm.fields_dict.questions.grid;
	const selected = grid.get_selected_children ? grid.get_selected_children() : [];
	const question_names = selected.map((row) => row.question).filter(Boolean);
	if (!question_names.length) {
		frappe.msgprint(__("Select one or more rows in the Questions table first."));
		return;
	}

	frappe.confirm(__("Remove {0} selected question(s)?", [question_names.length]), () => {
		const calls = question_names.map((question) =>
			frappe.call({
				method: "grid_erp.recruitment_assessment.api.remove_question_from_template",
				args: { template: frm.doc.name, question },
			})
		);
		Promise.all(calls).then(() => frm.reload_doc());
	});
}

function randomize_questions(frm) {
	frappe.call({
		method: "grid_erp.recruitment_assessment.api.randomize_template_questions",
		args: { template: frm.doc.name },
		freeze: true,
		callback() {
			frm.reload_doc();
		},
	});
}

function show_assessment_preview(frm) {
	frappe.call({
		method: "grid_erp.recruitment_assessment.api.preview_assessment_template",
		args: { template: frm.doc.name },
		callback(r) {
			const data = r.message || {};
			const questions = data.questions || [];
			const html = `
				<div>
					<h4>${frappe.utils.escape_html(data.template_name || data.name || "")}</h4>
					<p>${data.description || ""}</p>
					<p><b>${__("Duration")}:</b> ${frappe.utils.escape_html(String(data.duration || 0))} ${__("minutes")}
					&nbsp; <b>${__("Passing Percentage")}:</b> ${frappe.utils.escape_html(String(data.passing_percentage || 0))}%
					&nbsp; <b>${__("Total Questions")}:</b> ${questions.length}
					&nbsp; <b>${__("Total Marks")}:</b> ${frappe.utils.escape_html(String(data.total_marks || 0))}</p>
					${questions.map(render_question_card).join("")}
				</div>`;
			frappe.msgprint({
				title: __("Assessment Preview"),
				indicator: "blue",
				message: html,
				wide: true,
			});
		},
	});
}

function render_question_preview(frm) {
	const wrapper = frm.fields_dict.question_preview && frm.fields_dict.question_preview.$wrapper;
	if (!wrapper) return;
	const rows = get_filtered_rows(frm);
	const html = rows.length
		? rows.map((row) => render_question_card({ ...row, name: row.question })).join("")
		: `<div class="text-muted">${__("No questions match the current filters.")}</div>`;
	wrapper.html(`<div class="assessment-preview-list">${html}</div>`);
}

function get_filtered_rows(frm) {
	const search = (frm.doc.question_filter || "").toLowerCase();
	const category = (frm.doc.category_filter || "").toLowerCase();
	const difficulty = frm.doc.difficulty_filter || "";
	const questionType = frm.doc.question_type_filter || "";
	return (frm.doc.questions || []).filter((row) => {
		const haystack = `${row.question || ""} ${row.question_text || ""}`.toLowerCase();
		return (
			(!search || haystack.includes(search)) &&
			(!category || String(row.category || "").toLowerCase().includes(category)) &&
			(!difficulty || row.difficulty === difficulty) &&
			(!questionType || row.question_type === questionType)
		);
	});
}

function render_question_card(row) {
	const options = (row.options || [])
		.map((option) => `<li>${frappe.utils.escape_html(option.option_text || "")}</li>`)
		.join("");
	return `
		<div style="border: 1px solid var(--border-color); border-radius: 6px; padding: 10px 12px; margin-bottom: 10px;">
			<div><a href="/app/question-bank/${encodeURIComponent(row.name || row.question || "")}">${frappe.utils.escape_html(
		row.name || row.question || ""
	)}</a></div>
			<div style="margin: 6px 0;">${row.question_text || ""}</div>
			<div class="text-muted small">${frappe.utils.escape_html(row.category || "")} · ${frappe.utils.escape_html(
		row.difficulty || ""
	)} · ${frappe.utils.escape_html(row.question_type || "")} · ${frappe.utils.escape_html(String(row.marks || 0))} marks</div>
			${options ? `<ol type="A" style="margin-top: 8px;">${options}</ol>` : ""}
			<div class="small"><b>${__("Correct Answer")}:</b> ${frappe.utils.escape_html(row.correct_answer || "")}</div>
		</div>`;
}

function update_local_totals(frm) {
	const rows = (frm.doc.questions || []).filter((row) => row.question);
	const totalMarks = rows.reduce((sum, row) => sum + flt(row.marks), 0);
	const categories = [...new Set(rows.map((row) => row.category).filter(Boolean))].sort();
	frm.set_value("total_questions", rows.length);
	frm.set_value("total_marks", totalMarks);
	frm.set_value("question_categories", categories.join(", "));
}

// ---------------------------------------------------------------------------
// Import Questions Dialog
// ---------------------------------------------------------------------------
function show_import_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Import Questions from Excel / CSV"),
		fields: [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="font-size:13px">
					<b>Columns required:</b> question_title, question_type, category, difficulty, marks, correct_answer, option_1…option_4<br>
					Use the <b>Download Import Template</b> button to get a sample file.
				</div>`,
			},
			{
				fieldname: "file",
				fieldtype: "Attach",
				label: __("Excel or CSV File"),
				reqd: 1,
			},
			{
				fieldname: "section",
				fieldtype: "Link",
				options: "Assessment Section",
				label: __("Assign to Section (optional)"),
			},
		],
		primary_action_label: __("Import"),
		primary_action(values) {
			if (!values.file) {
				frappe.msgprint(__("Please attach a file."));
				return;
			}
			d.hide();
			frappe.show_alert({ message: __("Importing questions…"), indicator: "blue" });
			frappe.call({
				method: "grid_erp.recruitment_assessment.question_import.import_questions_from_file",
				args: { file_url: values.file, section: values.section || null },
				callback(r) {
					if (r.message) {
						const m = r.message;
						let msg = `<b>${__("Import Complete!")}</b><br>
							✅ ${m.created} ${__("questions created")}<br>
							⏭ ${m.skipped} ${__("skipped (duplicate)")}<br>
							❌ ${m.errors} ${__("errors")}`;
						if (m.error_details && m.error_details.length) {
							msg += "<br><br><b>Errors:</b><ul>" +
								m.error_details.map((e) => `<li>Row ${e.row}: ${frappe.utils.escape_html(e.error)}</li>`).join("") +
								"</ul>";
						}
						frappe.msgprint({ message: msg, indicator: m.errors ? "orange" : "green" });
						frm.reload_doc();
					}
				},
			});
		},
	});
	d.show();
}
