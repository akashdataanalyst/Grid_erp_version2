frappe.ui.form.on("Assessment Attempt", {
	refresh(frm) {
		if (frm.doc.status === "Submitted") {
			frm.add_custom_button(__("Open Result"), () => {
				frappe.set_route("List", "Assessment Result", { attempt: frm.doc.name });
			});
		}

		// ── AI Evaluation button ────────────────────────────────────────
		if (
			frm.doc.status === "Submitted" &&
			frappe.user.has_role(["HR Manager", "HR User", "System Manager", "Recruiter"])
		) {
			frm.add_custom_button(__("🤖 AI Evaluate Open Answers"), () => {
				frappe.confirm(
					__("Run AI evaluation on all pending open-ended answers?"),
					() => {
						frappe.show_alert({ message: __("Running AI evaluation…"), indicator: "blue" });
						frappe.call({
							method:
								"grid_erp.recruitment_assessment.ai_evaluation.run_ai_evaluation_for_attempt",
							args: { attempt_name: frm.doc.name },
							callback(r) {
								if (r.message) {
									const m = r.message;
									frappe.show_alert(
										{
											message: __(
												"AI evaluated {0} answers. Score: {1}% ({2})",
												[m.evaluated, m.percentage, m.passed ? "✅ Passed" : "❌ Failed"]
											),
											indicator: m.passed ? "green" : "orange",
										},
										8
									);
									frm.reload_doc();
								}
							},
						});
					}
				);
			}, __("Actions"));
		}

		// ── Video / Recording Playback ───────────────────────────────────
		_render_recordings(frm);
		_render_answer_review(frm);

		// ── Generate Certificate button ──────────────────────────────────
		if (frm.doc.status === "Submitted" && frm.doc.passed) {
			frm.add_custom_button(__("🏆 Download Certificate"), () => {
				const url = `/api/method/grid_erp.recruitment_assessment.certificate.generate_certificate?attempt=${frm.doc.name}`;
				window.open(url, "_blank");
			}, __("Actions"));
		}
	},
});

// ---------------------------------------------------------------------------
// Video playback helper
// ---------------------------------------------------------------------------
function _render_recordings(frm) {
	const files = {
		screen: frm.doc.screen_recording_file,
		webcam: frm.doc.camera_recording_file,
		audio: frm.doc.microphone_recording_file,
	};

	const anyFile = Object.values(files).some(Boolean);
	if (!anyFile) return;

	// Remove existing section to avoid duplicates on refresh
	frm.get_field && frm.fields_dict["recording_html"] && frm.set_df_property("recording_html", "hidden", false);

	let html = `
	<div style="background:#f8f9fa;border-radius:8px;padding:16px;margin-top:8px">
		<h5 style="margin:0 0 12px;color:#2c3e50">📹 Proctoring Recordings</h5>
		<div style="display:flex;gap:12px;flex-wrap:wrap">
	`;

	const labels = { screen: "Screen Recording", webcam: "Video Recording (Camera + Audio)", audio: "Audio Recording" };
	for (const [key, path] of Object.entries(files)) {
		if (!path) continue;
		const isAudio = key === "audio";
		const src = path.startsWith("http") ? path : path;
		if (isAudio) {
			html += `
			<div style="flex:1;min-width:260px">
				<p style="font-weight:600;margin-bottom:4px">${labels[key]}</p>
				<audio controls style="width:100%;border-radius:4px">
					<source src="${frappe.utils.escape_html(src)}">
					${__("Your browser does not support audio playback.")}
				</audio>
			</div>`;
		} else {
			html += `
			<div style="flex:1;min-width:300px">
				<p style="font-weight:600;margin-bottom:4px">${labels[key]}</p>
				<video controls style="width:100%;border-radius:6px;background:#000;max-height:240px">
					<source src="${frappe.utils.escape_html(src)}">
					${__("Your browser does not support video playback.")}
				</video>
				<a href="${frappe.utils.escape_html(src)}" download style="font-size:12px;display:block;margin-top:4px">
					⬇ ${__("Download")}
				</a>
			</div>`;
		}
	}

	html += `</div></div>`;

	// Inject into a custom HTML field OR append to last section
	if (frm.fields_dict["recording_html"]) {
		frm.fields_dict["recording_html"].$wrapper.html(html);
	} else {
		// Fallback: append below the form body
		const existing = frm.layout.wrapper.find(".recording-playback-section");
		if (existing.length) existing.remove();
		frm.layout.wrapper
			.find(".form-column:last")
			.append(`<div class="recording-playback-section">${html}</div>`);
	}
}

// ---------------------------------------------------------------------------
// HR review of answers that need a human: video introductions, files, written answers
// ---------------------------------------------------------------------------
const REVIEW_DONE = ["Evaluated", "Auto Evaluated", "AI Evaluated"];

function _render_answer_review(frm) {
	frm.layout.wrapper.find(".answer-review-section").remove();
	const is_hr = frappe.user.has_role(["HR Manager", "HR User", "System Manager", "Recruiter"]);
	const rows = (frm.doc.answers || []).filter((row) => !row.is_objective);
	if (!is_hr || !rows.length || !["Submitted", "Evaluated"].includes(frm.doc.status)) return;

	const esc = frappe.utils.escape_html;
	const pending = rows.filter((row) => !REVIEW_DONE.includes(row.evaluation_status)).length;
	const $section = $(`
		<div class="answer-review-section" style="border:1px solid var(--border-color);border-radius:8px;padding:16px;margin:12px 0">
			<h5 style="margin:0 0 4px">${__("Answers to Review")}</h5>
			<p class="text-muted" style="margin:0 0 12px">${
				pending ? __("{0} answer(s) waiting for your score.", [pending]) : __("All answers reviewed.")
			}</p>
		</div>
	`);

	rows.forEach((row) => {
		const done = REVIEW_DONE.includes(row.evaluation_status);
		const media = row.response_file
			? row.question_type === "Video Response"
				? `<video controls preload="metadata" style="width:100%;max-width:520px;border-radius:6px;background:#000" src="${esc(row.response_file)}"></video>
				   <div><a href="${esc(row.response_file)}" target="_blank" download>⬇ ${__("Download video")}</a></div>`
				: `<a href="${esc(row.response_file)}" target="_blank">📎 ${__("Open uploaded file")}</a>`
			: row.question_type === "Video Response"
				? `<div class="text-danger">${__("No video was recorded.")}</div>`
				: `<div style="white-space:pre-wrap;background:var(--control-bg);padding:8px;border-radius:6px">${esc(row.answer || __("(no answer)"))}</div>`;

		const $card = $(`
			<div style="border-top:1px solid var(--border-color);padding:12px 0">
				<div style="display:flex;justify-content:space-between;gap:8px">
					<strong style="white-space:pre-wrap">${esc(row.question_text || row.question_bank)}</strong>
					<span class="indicator-pill ${done ? "green" : "orange"}">${esc(done ? row.evaluation_status : __("Pending Review"))}</span>
				</div>
				<div style="margin:8px 0">${media}</div>
				<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
					<label style="margin:0">${__("Score")}</label>
					<input type="number" class="form-control input-xs review-score" style="width:90px" min="0" max="${row.marks || 0}" step="0.5" value="${done ? row.score || 0 : ""}">
					<span class="text-muted">/ ${row.marks || 0}</span>
					<input type="text" class="form-control input-xs review-remarks" style="flex:1;min-width:200px" placeholder="${__("Remarks (communication, confidence, fit...)")}" value="${esc(row.remarks || "")}">
					<button class="btn btn-xs btn-primary review-save">${__("Save Score")}</button>
				</div>
			</div>
		`);

		$card.find(".review-save").on("click", () => {
			const score = $card.find(".review-score").val();
			if (score === "") {
				frappe.msgprint(__("Please enter a score."));
				return;
			}
			frappe.call({
				method: "grid_erp.recruitment_assessment.api.evaluate_subjective_answer",
				args: {
					attempt: frm.doc.name,
					question_bank: row.question_bank,
					score,
					remarks: $card.find(".review-remarks").val(),
				},
				freeze: true,
				callback(r) {
					const m = r.message || {};
					frappe.show_alert({ message: __("Score saved. Result: {0} ({1}%)", [m.result_status, m.percentage]), indicator: "green" });
					frm.reload_doc();
				},
			});
		});
		$section.append($card);
	});

	frm.layout.wrapper.find(".form-page").first().prepend($section);
}
