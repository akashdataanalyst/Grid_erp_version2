// Copyright (c) 2026, Calco PolyTechnik Pvt Ltd and contributors
// For license information, please see license.txt

const GRID_AI_API = "grid_erp.grid_ai.api.chat";
const GRID_AI_MY_API = "grid_erp.grid_ai.api.my_ai";
const GRID_AI_POLL_MS = 4000;
const GRID_AI_GIVE_UP_MS = 6 * 60 * 1000; // a little over the server job timeout

frappe.pages["ai-assistant"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("AI Assistant"),
		single_column: true,
	});
	wrapper.grid_ai_chat = new GridAIChat(page, wrapper);
};

frappe.pages["ai-assistant"].on_page_show = function (wrapper) {
	wrapper.grid_ai_chat && wrapper.grid_ai_chat.focus();
};

class GridAIChat {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.conversation = null;
		this.pending = null; // {request_id, message_id, $msg, $body, $status, text, timer, started}
		this.make_layout();
		this.bind_events();
		this.setup_realtime();
		this.load_status();
	}

	// ------------------------------------------------------------ layout

	make_layout() {
		this.page.add_button(__("New Chat"), () => this.new_chat(), { icon: "add" });
		if (window.matchMedia("(max-width: 768px)").matches) {
			this.page.add_inner_button(__("Chats"), () => this.$root.toggleClass("gai-show-sidebar"));
		}

		this.$root = $(`
			<div class="gai-root">
				<aside class="gai-sidebar">
					<button class="btn btn-default btn-sm gai-new-chat">+ ${__("New Chat")}</button>
					<div class="gai-conv-list"></div>
				</aside>
				<section class="gai-main">
					<div class="gai-messages"></div>
					<div class="gai-composer">
						<textarea class="gai-input" rows="1" placeholder="${__("Apna sawal poochiye...")}"></textarea>
						<button class="btn btn-primary gai-send" title="${__("Send")}">${frappe.utils.icon("arrow-up", "sm")}</button>
					</div>
					<div class="gai-hint">
						<span>${__("Enter to send · Shift + Enter for new line")}</span>
						<a class="gai-model-link" title="${__("AI provider / model badlein")}"></a>
					</div>
				</section>
			</div>
		`).appendTo(this.page.main);

		this.$messages = this.$root.find(".gai-messages");
		this.$input = this.$root.find(".gai-input");
		this.$send = this.$root.find(".gai-send");
		this.$conv_list = this.$root.find(".gai-conv-list");
		this.$model_link = this.$root.find(".gai-model-link");
	}

	bind_events() {
		this.$root.find(".gai-new-chat").on("click", () => this.new_chat());
		this.$model_link.on("click", () => this.open_model_dialog());
		this.$send.on("click", () => this.send());
		this.$input.on("keydown", (e) => {
			if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
				e.preventDefault();
				this.send();
			}
		});
		this.$input.on("input", () => this.autosize());
		this.$messages.on("click", ".gai-suggestion", (e) => {
			this.$input.val($(e.currentTarget).text());
			this.send();
		});
		this.$conv_list.on("click", ".gai-conv", (e) => {
			if ($(e.target).closest(".gai-conv-delete").length) return;
			this.open_conversation($(e.currentTarget).attr("data-name"));
			this.$root.removeClass("gai-show-sidebar");
		});
		this.$conv_list.on("click", ".gai-conv-delete", (e) => {
			e.stopPropagation();
			this.delete_conversation($(e.currentTarget).closest(".gai-conv").attr("data-name"));
		});
	}

	setup_realtime() {
		frappe.realtime.on("grid_ai_stream", (data) => {
			const p = this.pending;
			if (!p || !data) return;
			const same_request = p.request_id && data.request_id === p.request_id;
			const same_message = p.message_id && data.message && data.message.name === p.message_id;
			if (!same_request && !same_message) return;
			if (data.type === "done") {
				this.finish_pending(data.message);
			} else if (data.type === "reset") {
				this.pending.text = "";
				this.pending.$body.text("");
			} else if (data.type === "status") {
				this.pending.$status.text(data.text || "").show();
			} else if (data.type === "token") {
				this.pending.text += data.text || "";
				this.pending.$status.hide();
				this.pending.$body.removeClass("gai-typing").text(this.pending.text);
				this.scroll_to_bottom();
			}
		});
	}

	load_status() {
		frappe.call(`${GRID_AI_API}.get_status`).then((r) => {
			this.status = r.message || {};
			if (this.status.is_admin) {
				this.page.add_menu_item(__("AI Settings"), () => frappe.set_route("Form", "AI Settings"));
				this.page.add_menu_item(__("AI Tool Configuration"), () =>
					frappe.set_route("List", "AI Tool Configuration")
				);
				this.page.add_menu_item(__("AI Query Log"), () => frappe.set_route("List", "AI Query Log"));
				this.page.add_menu_item(__("AI Usage"), () => frappe.set_route("query-report", "AI Usage"));
			}
			if (!this.status.can_use) {
				this.show_notice(__("Aapko AI Assistant use karne ki permission nahi hai."));
				return;
			}
			if (!this.status.enabled) {
				this.show_notice(
					this.status.is_admin
						? __("AI Assistant disabled hai. AI Settings mein provider configure karke enable kariye.")
						: __("AI Assistant abhi enabled nahi hai. Administrator se contact kariye.")
				);
				return;
			}
			this.page.add_inner_button(__("AI Model"), () => this.open_model_dialog());
			this.show_model(this.status.model);
			this.load_conversations();
			this.render_empty();
			this.focus();
		});
	}

	// ------------------------------------------------------------ AI model (user's own choice)

	show_model(model) {
		if (!model) return;
		const label = `${model.provider} · ${model.model}`;
		this.$model_link.text(`${__("Model")}: ${label}${model.own ? "" : " (" + __("company default") + ")"}`);
	}

	open_model_dialog() {
		frappe.call(`${GRID_AI_MY_API}.get_my_ai_settings`).then((r) => {
			const s = r.message;
			if (!s) return;
			if (!s.allowed) {
				frappe.msgprint({
					title: __("AI Model"),
					message: __("Sab users ke liye company ka model chalta hai: {0}. Isse badalne ka option Administrator ne band rakha hai.", [
						`<b>${frappe.utils.escape_html(s.company.provider + " · " + s.company.model)}</b>`,
					]),
				});
				return;
			}
			this.make_model_dialog(s).show();
		});
	}

	make_model_dialog(s) {
		const company = `${s.company.provider} · ${s.company.model}`;
		const OWN = __("Apna provider / model");
		const COMPANY = __("Company default") + ` (${company})`;
		const key_rule = {
			"Free models only": __("Company ki key par sirf free models (koi kharcha nahi). Doosre models ke liye apni key."),
			"Any model": __("Company ki key par is provider ka koi bhi model (company ke credit se)."),
			"Not allowed": __("Company ki key par sirf company ka model. Kuch bhi badalne ke liye apni key."),
		}[s.company_key_policy];

		const d = new frappe.ui.Dialog({
			title: __("AI Model"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "intro",
					options: `<p class="text-muted small">${__(
						"Aapke sawal kis AI se answer honge, yeh aap chun sakte hain. Permission aur data ke rules wahi rahenge."
					)}</p>`,
				},
				{
					fieldtype: "Select",
					fieldname: "choice",
					label: __("Kaunsa AI use karein?"),
					options: [COMPANY, OWN],
					default: s.mine.use_own ? OWN : COMPANY,
				},
				{
					fieldtype: "Select",
					fieldname: "provider",
					label: __("Provider"),
					options: s.providers,
					default: s.mine.provider || s.company.provider,
					depends_on: `eval:doc.choice == ${JSON.stringify(OWN)}`,
				},
				{
					fieldtype: "Autocomplete",
					fieldname: "model",
					label: __("Model"),
					ignore_validation: 1,
					default: s.mine.model || s.company.model,
					depends_on: `eval:doc.choice == ${JSON.stringify(OWN)}`,
				},
				{
					fieldtype: "Password",
					fieldname: "api_key",
					label: __("Meri API Key"),
					depends_on: `eval:doc.choice == ${JSON.stringify(OWN)}`,
				},
				{
					fieldtype: "Check",
					fieldname: "remove_key",
					label: __("Saved key hata do"),
					depends_on: `eval:doc.choice == ${JSON.stringify(OWN)} && ${s.mine.has_key ? 1 : 0}`,
				},
				{ fieldtype: "HTML", fieldname: "help" },
			],
			primary_action_label: __("Save & Test"),
			primary_action: (values) => this.save_model_choice(d, values, OWN),
		});

		const update_help = () => {
			const provider = d.get_value("provider");
			const same_provider = provider === s.company.provider;
			const saved_key_here = s.mine.has_key && s.mine.provider === provider;
			const page = s.key_pages[provider];
			const lines = [];
			if (d.get_value("choice") === OWN) {
				if (saved_key_here) lines.push(__("Aapki {0} key saved hai. Khaali chhodenge toh wahi chalegi.", [provider]));
				if (same_provider) lines.push(key_rule);
				else lines.push(__("{0} ke liye apni {0} API key zaroori hai.", [provider]));
				if (page) {
					lines.push(
						`${__("Key yahan se milegi")}: <a href="${page}" target="_blank" rel="noopener noreferrer">${page}</a>`
					);
				}
				lines.push(__("Key encrypted save hoti hai aur dobara kabhi dikhayi nahi jaati."));
			}
			if (s.notice) lines.unshift(`<span class="text-warning">${frappe.utils.escape_html(s.notice)}</span>`);
			d.fields_dict.help.$wrapper.html(
				lines.length ? `<div class="small text-muted">${lines.map((l) => `<p>${l}</p>`).join("")}</div>` : ""
			);
			d.fields_dict.api_key.set_description(
				saved_key_here ? __("Saved hai (••••). Nayi key daalein sirf badalni ho toh.") : ""
			);
		};

		const load_models = (reset) => {
			const provider = d.get_value("provider");
			frappe.call(`${GRID_AI_MY_API}.get_my_model_options`, { provider }).then((r) => {
				if (d.get_value("provider") !== provider) return;
				const opts = r.message || {};
				const free = new Set(opts.company_key_models || []);
				const items = (opts.models || []).map((m) => ({
					value: m,
					label: m,
					description: free.has(m) ? __("company key par chalega") : "",
				}));
				d.fields_dict.model.set_data(items);
				if (reset && !(opts.models || []).includes(d.get_value("model"))) {
					d.set_value("model", opts.default || "");
				}
			});
		};

		d.fields_dict.choice.$input.on("change", update_help);
		d.fields_dict.provider.$input.on("change", () => {
			update_help();
			load_models(true);
		});
		update_help();
		load_models(false);
		return d;
	}

	save_model_choice(d, values, OWN) {
		const use_own = values.choice === OWN ? 1 : 0;
		frappe
			.call({
				method: `${GRID_AI_MY_API}.save_my_ai_settings`,
				type: "POST",
				freeze: true,
				freeze_message: __("Saving..."),
				args: {
					use_own,
					provider: values.provider,
					model: values.model,
					api_key: values.api_key || "",
					remove_key: values.remove_key ? 1 : 0,
				},
			})
			.then((r) => {
				const s = r.message || {};
				d.hide();
				this.show_model(s.effective);
				frappe.call({
					method: `${GRID_AI_MY_API}.test_my_connection`,
					type: "POST",
					freeze: true,
					freeze_message: __("Connection test ho raha hai..."),
					callback: (t) => {
						const res = t.message || {};
						frappe.msgprint({
							title: res.ok ? __("Ho gaya") : __("Save hua, par connection fail"),
							indicator: res.ok ? "green" : "red",
							message: frappe.utils.escape_html(res.message || ""),
						});
					},
				});
			});
	}

	// ------------------------------------------------------------ conversations

	load_conversations() {
		return frappe.call(`${GRID_AI_API}.get_conversations`).then((r) => {
			const rows = r.message || [];
			this.$conv_list.empty();
			if (!rows.length) {
				this.$conv_list.append(`<div class="gai-conv-empty">${__("No conversations yet")}</div>`);
			}
			rows.forEach((row) => {
				$(`
					<div class="gai-conv" data-name="${frappe.utils.escape_html(row.name)}">
						<span class="gai-conv-title"></span>
						<button class="btn btn-xs gai-conv-delete" title="${__("Delete")}">${frappe.utils.icon("delete", "xs")}</button>
					</div>
				`)
					.toggleClass("active", row.name === this.conversation)
					.find(".gai-conv-title")
					.text(row.title || __("Untitled"))
					.end()
					.appendTo(this.$conv_list);
			});
		});
	}

	open_conversation(name) {
		if (this.pending) return;
		this.conversation = name;
		this.$conv_list.find(".gai-conv").removeClass("active");
		this.$conv_list.find(`.gai-conv[data-name="${CSS.escape(name)}"]`).addClass("active");
		this.$messages.empty();
		frappe.call(`${GRID_AI_API}.get_messages`, { conversation_id: name }).then((r) => {
			this.$messages.empty();
			(r.message || []).forEach((m) => {
				const $msg = this.add_message(m.role, "");
				if (m.status === "pending") {
					// Still being answered (e.g. the page was reloaded): keep watching it.
					this.start_pending($msg, null, m.name);
				} else {
					this.render_message($msg, m);
				}
			});
			this.scroll_to_bottom();
			this.focus();
		});
	}

	new_chat() {
		if (this.pending) return;
		this.conversation = null;
		this.$conv_list.find(".gai-conv").removeClass("active");
		this.render_empty();
		this.$root.removeClass("gai-show-sidebar");
		this.focus();
	}

	delete_conversation(name) {
		frappe.confirm(__("Delete this conversation?"), () => {
			frappe
				.call({ method: `${GRID_AI_API}.delete_conversation`, args: { conversation_id: name }, type: "POST" })
				.then(() => {
					if (this.conversation === name) this.new_chat();
					this.load_conversations();
				});
		});
	}

	// ------------------------------------------------------------ sending

	send() {
		const text = (this.$input.val() || "").trim();
		if (!text || this.pending || !this.status || !this.status.enabled) return;
		if (text.length > (this.status.max_message_length || 2000)) {
			frappe.show_alert({ message: __("Message bahut lamba hai."), indicator: "orange" });
			return;
		}

		this.$messages.find(".gai-empty").remove();
		this.add_message("user", text);
		this.$input.val("");
		this.autosize();

		const request_id = frappe.utils.get_random(16);
		this.start_pending(this.add_message("assistant", ""), request_id, null);

		frappe
			.call({
				method: `${GRID_AI_API}.send_message`,
				type: "POST",
				args: { message: text, conversation_id: this.conversation, client_request_id: request_id },
				error_handlers: {},
			})
			.then((r) => {
				const res = r.message || {};
				if (res.conversation) {
					const is_new = this.conversation !== res.conversation.name;
					this.conversation = res.conversation.name;
					if (is_new) this.load_conversations();
				}
				if (!res.ok || !res.message || typeof res.message === "string") {
					// Rejected before queuing (e.g. rate limit): message is a plain string.
					const content = typeof res.message === "string" ? res.message : __("Sorry, kuch problem aayi.");
					this.finish_pending({ role: "assistant", content, status: "error" });
					return;
				}
				if (this.pending && this.pending.request_id === request_id) {
					this.pending.message_id = res.message.name;
				}
			})
			.catch(() => {
				this.finish_pending({
					role: "assistant",
					content: __("Sorry, request complete nahi ho paayi. Kripya dobara try kariye."),
					status: "error",
				});
			});
	}

	start_pending($msg, request_id, message_id) {
		this.pending = {
			request_id,
			message_id,
			$msg,
			$body: $msg.find(".gai-body").addClass("gai-typing"),
			$status: $(`<div class="gai-status"></div>`).hide().prependTo($msg.find(".gai-bubble")),
			text: "",
			started: Date.now(),
		};
		// The reply is written by a background job; realtime brings it, polling is the fallback.
		this.pending.timer = setInterval(() => this.poll_pending(), GRID_AI_POLL_MS);
		this.set_busy(true);
	}

	poll_pending() {
		const pending = this.pending;
		if (!pending) return;
		if (Date.now() - pending.started > GRID_AI_GIVE_UP_MS) {
			this.finish_pending({
				role: "assistant",
				content: __("Sorry, jawab time par nahi aa paya. Kripya dobara try kariye."),
				status: "error",
			});
			return;
		}
		if (!pending.message_id || pending.polling) return;
		pending.polling = true;
		frappe
			.call({ method: `${GRID_AI_API}.get_message`, args: { message_id: pending.message_id }, error_handlers: {} })
			.then((r) => {
				pending.polling = false;
				const m = r.message;
				if (this.pending === pending && m && m.status !== "pending") this.finish_pending(m);
			})
			.catch(() => (pending.polling = false));
	}

	finish_pending(message) {
		if (!this.pending) return;
		const { $msg, $status, timer } = this.pending;
		clearInterval(timer);
		$status.remove();
		this.render_message($msg, message);
		this.pending = null;
		this.set_busy(false);
		this.scroll_to_bottom();
		this.focus();
	}

	// ------------------------------------------------------------ rendering

	add_message(role, content, is_error = false) {
		const $msg = $(`
			<div class="gai-msg gai-${role}">
				<div class="gai-bubble"><div class="gai-body"></div></div>
			</div>
		`).appendTo(this.$messages);
		if (content) this.render_body($msg, role, content, is_error);
		this.scroll_to_bottom();
		return $msg;
	}

	render_message($msg, m) {
		const is_error = m.status === "error";
		this.render_body($msg, m.role, m.content || "", is_error);
		if (m.role !== "assistant" || is_error) return;
		(m.views || []).forEach((view) => this.render_view($msg, view));
		if (m.name) this.render_feedback($msg, m);
	}

	render_body($msg, role, content, is_error) {
		const $body = $msg.find(".gai-body").removeClass("gai-typing");
		$msg.toggleClass("gai-error", !!is_error);
		if (role === "user" || is_error) {
			$body.text(content);
		} else {
			$body.html(this.safe_markdown(content));
		}
	}

	render_view($msg, view) {
		// Where the answer came from, a link to the same records, and the rows behind it.
		const $view = $(`<div class="gai-view">
			<div class="gai-view-head">
				<span class="gai-view-title"></span>
				<span class="gai-view-actions"></span>
			</div>
			<div class="gai-view-chart"></div>
		</div>`).appendTo($msg.find(".gai-bubble"));
		$view.find(".gai-view-title").text(view.title || view.label || "");
		const $actions = $view.find(".gai-view-actions");

		if (view.route && view.route.startsWith("/app/")) {
			$(`<a class="gai-view-link" target="_blank" rel="noopener noreferrer"></a>`)
				.attr("href", view.route)
				.text(__("Records dekhein") + " ↗")
				.appendTo($actions);
		}
		if (view.rows && view.rows.length) {
			$(`<a class="gai-view-link"></a>`)
				.text(__("CSV"))
				.on("click", () => this.download_csv(view))
				.appendTo($actions);
		}
		if (view.chart && view.rows && view.rows.length > 1) {
			this.render_chart($view.find(".gai-view-chart"), view);
		}
	}

	render_chart($el, view) {
		const { label, value } = view.chart;
		const rows = view.rows.slice(0, 15);
		try {
			new frappe.Chart($el.get(0), {
				data: {
					labels: rows.map((r) => String(r[label] ?? __("(blank)"))),
					datasets: [{ name: value === "value" ? __("Value") : __("Count"), values: rows.map((r) => r[value] || 0) }],
				},
				type: "bar",
				height: 220,
				colors: ["#5e64ff"],
				axisOptions: { xAxisMode: "tick", xIsSeries: false },
				barOptions: { spaceRatio: 0.4 },
			});
		} catch (e) {
			$el.remove(); // the chart is extra; the answer and CSV still work
		}
	}

	download_csv(view) {
		const columns = view.columns || Object.keys(view.rows[0] || {});
		const data = [columns, ...view.rows.map((row) => columns.map((c) => row[c] ?? ""))];
		frappe.tools.downloadify(data, null, (view.label || "ai-data").replace(/[^\w-]+/g, "_"));
	}

	render_feedback($msg, m) {
		const $fb = $(`<div class="gai-feedback">
			<button class="btn btn-xs gai-fb" data-value="Up" title="${__("Sahi jawab")}">👍</button>
			<button class="btn btn-xs gai-fb" data-value="Down" title="${__("Galat jawab")}">👎</button>
		</div>`).appendTo($msg.find(".gai-bubble"));
		const mark = (value) => $fb.find(".gai-fb").each((_, b) => $(b).toggleClass("active", b.dataset.value === value));
		mark(m.feedback);
		$fb.on("click", ".gai-fb", (e) => {
			const clicked = e.currentTarget.dataset.value;
			const value = $(e.currentTarget).hasClass("active") ? "" : clicked;
			frappe
				.call({ method: `${GRID_AI_API}.set_feedback`, type: "POST", args: { message_id: m.name, feedback: value } })
				.then(() => mark(value));
		});
	}

	safe_markdown(text) {
		// Escape first so model output (which may echo ERP data) can never inject HTML.
		const html = frappe.markdown(frappe.utils.escape_html(text || ""));
		const $tmp = $("<div>").html(html);
		$tmp.find("script, style, iframe, object, embed, img").remove();
		$tmp.find("a").each(function () {
			const href = ($(this).attr("href") || "").trim().toLowerCase();
			if (!href.startsWith("http://") && !href.startsWith("https://") && !href.startsWith("/")) {
				$(this).removeAttr("href");
			}
			$(this).attr({ target: "_blank", rel: "noopener noreferrer" });
		});
		$tmp.find("*").each(function () {
			[...this.attributes].forEach((attr) => {
				if (attr.name.startsWith("on")) this.removeAttribute(attr.name);
			});
		});
		$tmp.find("table").addClass("table table-bordered table-sm");
		return $tmp.html();
	}

	render_empty() {
		this.$messages.empty();
		const $empty = $(`
			<div class="gai-empty">
				<div class="gai-empty-title">${__("ERP AI Assistant")}</div>
				<div class="gai-empty-sub">${__("English, Hindi ya Hinglish mein apne ERP data ke baare mein poochiye.")}</div>
				<div class="gai-suggestions"></div>
			</div>
		`).appendTo(this.$messages);
		((this.status && this.status.suggestions) || []).forEach((s) =>
			$(`<button class="btn btn-default btn-sm gai-suggestion"></button>`)
				.text(s)
				.appendTo($empty.find(".gai-suggestions"))
		);
	}

	show_notice(text) {
		this.$messages.html(`<div class="gai-empty"><div class="gai-empty-sub"></div></div>`);
		this.$messages.find(".gai-empty-sub").text(text);
		this.$input.prop("disabled", true);
		this.$send.prop("disabled", true);
	}

	// ------------------------------------------------------------ helpers

	set_busy(busy) {
		this.$send.prop("disabled", busy);
		this.$root.toggleClass("gai-busy", busy);
	}

	autosize() {
		const el = this.$input.get(0);
		el.style.height = "auto";
		el.style.height = Math.min(el.scrollHeight, 200) + "px";
	}

	scroll_to_bottom() {
		const el = this.$messages.get(0);
		if (el) el.scrollTop = el.scrollHeight;
	}

	focus() {
		if (this.$input && !this.$input.prop("disabled")) this.$input.trigger("focus");
	}
}
