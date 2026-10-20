const METHOD_ROOT = "/api/method/grid_erp.recruitment_assessment.api.";
const STORE_KEYS = {
	token: "assessment_session_token",
	assignment: "assessment_assignment",
	attempt: "assessment_attempt",
	candidate: "assessment_candidate_id",
};

const $ = (id) => document.getElementById(id);

// Frappe errors arrive as a JSON-encoded list of JSON messages; show plain text to the candidate.
function readableError(json, fallback) {
	try {
		const messages = JSON.parse(json._server_messages || "[]").map((item) => {
			const text = JSON.parse(item).message || item;
			const div = document.createElement("div");
			div.innerHTML = text;
			return div.textContent;
		});
		if (messages.length) return messages.join("\n");
	} catch {
		// fall through
	}
	return json.message || json.exc_type || fallback;
}
const bool = (value) => value === 1 || value === true || value === "1";

class AssessmentApi {
	constructor(state) {
		this.state = state;
	}

	async call(method, data = {}) {
		// credentials: "omit" — the candidate is identified by X-Assessment-Token only. Sending an
		// HR user's desk cookie (same browser) makes Frappe demand a CSRF token: "Invalid Request".
		const res = await fetch(`${METHOD_ROOT}${method}`, {
			method: "POST",
			credentials: "omit",
			headers: {
				"Content-Type": "application/json",
				"X-Assessment-Token": this.state.token || "",
			},
			body: JSON.stringify(data),
		});
		const json = await res.json().catch(() => ({ message: `Server error (${res.status})` }));
		if (!res.ok || json.exc) {
			throw new Error(readableError(json, "Request failed"));
		}
		return json.message;
	}

	async upload(method, attempt, blob, extra = {}) {
		const form = new FormData();
		form.append("attempt", attempt);
		form.append("file", blob, extra.filename || "recording.webm");
		Object.entries(extra).forEach(([key, value]) => {
			if (value !== undefined && key !== "filename") form.append(key, value);
		});
		const res = await fetch(`${METHOD_ROOT}${method}`, {
			method: "POST",
			credentials: "omit",
			headers: { "X-Assessment-Token": this.state.token || "" },
			body: form,
		});
		const json = await res.json().catch(() => ({ message: `Server error (${res.status})` }));
		if (!res.ok || json.exc) {
			throw new Error(readableError(json, "Upload failed"));
		}
		return json.message;
	}
}

class MediaController {
	constructor(app) {
		this.app = app;
		this.cameraStream = null;
		this.microphoneStream = null;
		this.screenStream = null;
		this.recorders = {};
		this.startedAt = {};
		this.snapshotTimer = null;
	}

	async requestMandatoryStreams() {
		try {
			this.cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
			await this.app.logAudit("Permission Granted", "Camera permission granted for assessment start.", { permission: "camera" });
			await this.app.logAudit("Camera Started", "Camera permission granted and stream started.");
		} catch (error) {
			await this.app.logAudit("Permission Denied", "Camera permission denied.", { permission: "camera", message: error.message }).catch(() => {});
			throw error;
		}
		try {
			this.microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
			await this.app.logAudit("Permission Granted", "Microphone permission granted for assessment start.", { permission: "microphone" });
			await this.app.logAudit("Microphone Started", "Microphone permission granted and stream started.");
		} catch (error) {
			await this.app.logAudit("Permission Denied", "Microphone permission denied.", { permission: "microphone", message: error.message }).catch(() => {});
			throw error;
		}
		try {
			this.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
			await this.app.logAudit("Permission Granted", "Screen share permission granted for assessment start.", { permission: "screen" });
			await this.app.logAudit("Screen Share Started", "Screen share permission granted and stream started.");
		} catch (error) {
			await this.app.logAudit("Permission Denied", "Screen share permission denied.", { permission: "screen", message: error.message }).catch(() => {});
			throw error;
		}
		this.bindTrackGuards();
		$("camera-preview").srcObject = this.cameraStream;
		await $("camera-preview").play().catch(() => {});
		return true;
	}

	bindTrackGuards() {
		this.screenStream.getVideoTracks().forEach((track) => {
			track.addEventListener("ended", () => this.app.pauseForMedia("screen"));
		});
		this.cameraStream.getVideoTracks().forEach((track) => {
			track.addEventListener("ended", () => this.app.pauseForMedia("camera"));
			track.addEventListener("mute", () => this.app.logViolation("Camera disabled", "High"));
		});
		this.microphoneStream.getAudioTracks().forEach((track) => {
			track.addEventListener("ended", () => this.app.handleMicrophoneStop());
			track.addEventListener("mute", () => this.app.handleMicrophoneStop());
		});
	}

	startRecording() {
		this.createRecorder("screen", this.screenStream, "upload_screen_recording", "screen-recording.webm");
		// Camera video + microphone audio go into one file, so HR gets a single video with sound.
		const cameraWithAudio = this.cameraStream
			? new MediaStream([
				...this.cameraStream.getVideoTracks(),
				...(this.microphoneStream?.getAudioTracks() || []),
			])
			: null;
		if (cameraWithAudio) {
			this.createRecorder("camera", cameraWithAudio, "upload_camera_recording", "camera-recording.webm");
		} else {
			this.createRecorder("microphone", this.microphoneStream, "upload_microphone_recording", "microphone-recording.webm");
		}
		this.app.logAudit("Recording Started", "Assessment recording started.").catch(() => {});
	}

	createRecorder(key, stream, uploadMethod, filename) {
		if (!stream || !window.MediaRecorder) return;
		const chunks = [];
		const isAudio = key === "microphone";
		const mimeType = isAudio
			? (MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm")
			: (MediaRecorder.isTypeSupported("video/webm;codecs=vp9,opus") ? "video/webm;codecs=vp9,opus" : "video/webm");
		// Low bitrates keep a whole-session recording under the server's upload size limit.
		const recorder = new MediaRecorder(stream, {
			mimeType,
			videoBitsPerSecond: key === "screen" ? 300000 : 250000,
			audioBitsPerSecond: 32000,
		});
		recorder.ondataavailable = (event) => {
			if (event.data && event.data.size) chunks.push(event.data);
		};
		// Resolves once the recording is uploaded (or failed), so submit can wait for it.
		recorder.uploaded = new Promise((resolve) => {
			recorder.onstop = async () => {
				if (!chunks.length || !this.app.state.attempt) return resolve();
				const blob = new Blob(chunks.splice(0), { type: recorder.mimeType });
				const duration = Math.round((Date.now() - this.startedAt[key]) / 1000);
				await this.app.api.upload(uploadMethod, this.app.state.attempt, blob, {
					filename,
					mime_type: recorder.mimeType,
					duration_seconds: duration,
				}).catch((error) => console.warn(`${key} recording upload failed`, error));
				resolve();
			};
		});
		this.recorders[key] = recorder;
		this.startedAt[key] = Date.now();
		recorder.start(1000);
	}

	async stopAndUploadAll() {
		const recorders = Object.values(this.recorders);
		recorders.forEach((recorder) => {
			if (recorder.state !== "inactive") recorder.stop();
		});
		clearInterval(this.snapshotTimer);
		this.app.setStatus("Saving your recordings… please keep this page open.");
		const allUploaded = Promise.all(recorders.map((recorder) => recorder.uploaded));
		await Promise.race([allUploaded, new Promise((resolve) => setTimeout(resolve, 180000))]);
		await this.app.logAudit("Recording Stopped", "Assessment recording stopped.").catch(() => {});
	}

	startSnapshots() {
		const interval = Math.max(15, Number(this.app.state.settings.snapshot_interval_seconds || 60));
		this.captureSnapshot("Assessment Start").catch(() => {});
		clearInterval(this.snapshotTimer);
		this.snapshotTimer = setInterval(() => {
			this.captureSnapshot("Scheduled Snapshot").catch(() => {});
		}, interval * 1000);
	}

	async captureSnapshot(reason) {
		if (!this.cameraStream || !this.app.state.attempt) return;
		const video = $("camera-preview");
		const canvas = $("snapshot-canvas");
		const width = video.videoWidth || 640;
		const height = video.videoHeight || 480;
		canvas.width = width;
		canvas.height = height;
		canvas.getContext("2d").drawImage(video, 0, 0, width, height);
		const imageData = canvas.toDataURL("image/jpeg", 0.82);
		await this.app.api.call("save_snapshot", { attempt: this.app.state.attempt, image_data: imageData, reason });
	}

	stopStreams() {
		const hadCamera = Boolean(this.cameraStream);
		const hadMicrophone = Boolean(this.microphoneStream);
		const hadScreen = Boolean(this.screenStream);
		[this.cameraStream, this.microphoneStream, this.screenStream].forEach((stream) => {
			if (stream) stream.getTracks().forEach((track) => track.stop());
		});
		if (hadCamera) this.app.logAudit("Camera Stopped", "Camera stream stopped.").catch(() => {});
		if (hadMicrophone) this.app.logAudit("Microphone Stopped", "Microphone stream stopped.").catch(() => {});
		if (hadScreen) this.app.logAudit("Screen Share Stopped", "Screen share stream stopped.").catch(() => {});
	}
}

const FILE_ANSWER_TYPES = ["File Upload", "Image-based Question", "Video Response"];
const VIDEO_ANSWER = { maxSeconds: 180, minSeconds: 10, videoBitsPerSecond: 700000, audioBitsPerSecond: 64000 };

const formatClock = (seconds) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;

// Records a video answer from the camera + microphone streams the assessment already holds.
class VideoAnswerRecorder {
	constructor(app, node, questionBank) {
		this.app = app;
		this.node = node;
		this.questionBank = questionBank;
		this.preview = node.querySelector("video");
		this.statusNode = node.querySelector("[data-video-status]");
		this.buttons = {
			record: node.querySelector('[data-action="record"]'),
			stop: node.querySelector('[data-action="stop"]'),
			retake: node.querySelector('[data-action="retake"]'),
		};
		this.recorder = null;
		this.chunks = [];
		this.timer = null;
		this.elapsed = 0;
		this.buttons.record.addEventListener("click", () => this.start());
		this.buttons.stop.addEventListener("click", () => this.stop());
		this.buttons.retake.addEventListener("click", () => this.start());
		const saved = app.state.responseFiles[questionBank];
		if (saved) this.showSaved(saved);
	}

	get isRecording() {
		return Boolean(this.recorder && this.recorder.state === "recording");
	}

	get isBusy() {
		return this.isRecording || this.uploading;
	}

	setStatus(message, tone = "") {
		this.statusNode.textContent = message;
		this.statusNode.dataset.tone = tone;
	}

	showButtons({ record = false, stop = false, retake = false }) {
		this.buttons.record.hidden = !record;
		this.buttons.stop.hidden = !stop;
		this.buttons.retake.hidden = !retake;
	}

	liveStream() {
		const tracks = [
			...(this.app.media.cameraStream?.getVideoTracks() || []),
			...(this.app.media.microphoneStream?.getAudioTracks() || []),
		].filter((track) => track.readyState === "live");
		if (!tracks.some((track) => track.kind === "video")) throw new Error("Camera is not available. Please enable your camera.");
		return new MediaStream(tracks);
	}

	start() {
		if (!window.MediaRecorder) {
			this.setStatus("Your browser cannot record video. Please use the latest Google Chrome or Microsoft Edge.", "error");
			return;
		}
		if (this.app.activeVideoRecorder && this.app.activeVideoRecorder !== this && this.app.activeVideoRecorder.isRecording) {
			this.setStatus("Finish the other video recording first.", "error");
			return;
		}
		let stream;
		try {
			stream = this.liveStream();
		} catch (error) {
			this.setStatus(error.message, "error");
			return;
		}
		const mimeType = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"].find((type) => MediaRecorder.isTypeSupported(type)) || "";
		this.chunks = [];
		this.recorder = new MediaRecorder(stream, {
			mimeType,
			videoBitsPerSecond: VIDEO_ANSWER.videoBitsPerSecond,
			audioBitsPerSecond: VIDEO_ANSWER.audioBitsPerSecond,
		});
		this.recorder.ondataavailable = (event) => {
			if (event.data && event.data.size) this.chunks.push(event.data);
		};
		this.stopped = new Promise((resolve) => {
			this.recorder.onstop = () => resolve(this.finish());
		});
		this.preview.removeAttribute("src");
		this.preview.srcObject = stream;
		this.preview.muted = true;
		this.preview.controls = false;
		this.preview.hidden = false;
		this.preview.play().catch(() => {});
		this.app.activeVideoRecorder = this;
		this.elapsed = 0;
		this.recorder.start(1000);
		this.node.classList.add("recording");
		this.showButtons({ stop: true });
		this.tick();
		this.timer = setInterval(() => this.tick(), 1000);
	}

	tick() {
		this.setStatus(`● Recording ${formatClock(this.elapsed)} / ${formatClock(VIDEO_ANSWER.maxSeconds)}`, "recording");
		if (this.elapsed >= VIDEO_ANSWER.maxSeconds) {
			this.stop();
			return;
		}
		this.elapsed += 1;
	}

	stop() {
		if (!this.isRecording) return this.stopped;
		if (this.elapsed < VIDEO_ANSWER.minSeconds) {
			this.setStatus(`Please speak for at least ${VIDEO_ANSWER.minSeconds} seconds before stopping.`, "error");
			return this.stopped;
		}
		clearInterval(this.timer);
		this.recorder.stop();
		return this.stopped;
	}

	async finish() {
		clearInterval(this.timer);
		this.node.classList.remove("recording");
		const duration = this.elapsed;
		const blob = new Blob(this.chunks.splice(0), { type: this.recorder.mimeType || "video/webm" });
		this.preview.srcObject = null;
		this.preview.src = URL.createObjectURL(blob);
		this.preview.muted = false;
		this.preview.controls = true;
		this.showButtons({});
		this.uploading = true;
		this.setStatus(`Saving your video (${formatClock(duration)})…`, "busy");
		try {
			const result = await this.uploadWithRetry(blob, duration);
			this.app.state.responseFiles[this.questionBank] = result.file_url;
			this.setStatus(`✓ Video saved (${formatClock(duration)}). Watch it above, or record again.`, "saved");
		} catch (error) {
			this.setStatus(`Video could not be saved: ${error.message || "upload failed"}. Please record again.`, "error");
		} finally {
			this.uploading = false;
			this.showButtons({ retake: true });
			this.buttons.retake.textContent = "↺ Record Again";
		}
	}

	async uploadWithRetry(blob, duration, tries = 3) {
		for (let attempt = 1; ; attempt++) {
			try {
				return await this.app.api.upload("upload_answer_file", this.app.state.attempt, blob, {
					filename: `video-introduction-${duration}s.webm`,
					question_bank: this.questionBank,
					duration_seconds: duration,
				});
			} catch (error) {
				if (attempt >= tries) throw error;
				this.setStatus(`Saving your video… retrying (${attempt + 1}/${tries})`, "busy");
				await new Promise((resolve) => setTimeout(resolve, 1500 * attempt));
			}
		}
	}

	showSaved() {
		this.setStatus("✓ Your video is saved. You can record again to replace it.", "saved");
		this.showButtons({ retake: true });
		this.buttons.retake.textContent = "↺ Record Again";
	}
}

class ProctoredAssessmentApp {
	constructor() {
		this.state = {
			token: localStorage.getItem(STORE_KEYS.token) || "",
			assignment: localStorage.getItem(STORE_KEYS.assignment) || "",
			attempt: localStorage.getItem(STORE_KEYS.attempt) || "",
			candidateId: localStorage.getItem(STORE_KEYS.candidate) || "",
			settings: {},
			context: {},
			questions: [],
			responseFiles: {},
			checks: {},
			remaining: 0,
			timer: null,
			heartbeat: null,
			paused: false,
			graceTimer: null,
			consentAccepted: false,
		};
		this.api = new AssessmentApi(this.state);
		this.media = new MediaController(this);
		this.query = new URLSearchParams(window.location.search);
	}

	async boot() {
		this.bindAuth();
		this.bindWelcome();
		this.bindConsent();
		this.bindSystemCheck();
		this.bindAssessmentActions();
		await this.bootstrapFromUrl();
		if (!this.query.get("assignment") && this.state.token && this.state.assignment) {
			try {
				await this.loadContext();
			} catch {
				this.clearStoredSession();
				this.setStatus("Your previous session has ended. Please sign in again.");
				return;
			}
			if (this.state.consentAccepted) this.showSystemPanel("Resume previous session");
			else this.showWelcomePanel();
		}
	}

	clearStoredSession() {
		Object.values(STORE_KEYS).forEach((key) => localStorage.removeItem(key));
		Object.assign(this.state, { token: "", attempt: "", consentAccepted: false, context: {} });
	}

	saveState() {
		localStorage.setItem(STORE_KEYS.token, this.state.token || "");
		localStorage.setItem(STORE_KEYS.assignment, this.state.assignment || "");
		localStorage.setItem(STORE_KEYS.attempt, this.state.attempt || "");
		localStorage.setItem(STORE_KEYS.candidate, this.state.candidateId || "");
	}

	notice(message) {
		if (window.frappe?.msgprint) frappe.msgprint(message);
		else window.alert(message);
	}

	setStatus(message) {
		$("status-banner").innerText = message;
	}

	async bootstrapFromUrl() {
		const assignment = this.query.get("assignment") || this.state.assignment;
		const token = this.query.get("token") || this.query.get("password") || "";
		if (!assignment) return;
		const storedAssignment = this.state.assignment;
		this.state.assignment = assignment;
		$("assignment-id").value = assignment;
		if (this.state.token && storedAssignment === assignment) {
			try {
				await this.loadContext();
				return;
			} catch (error) {
				// Stored session is stale (expired, or the assignment was re-issued): sign in again below.
				this.clearStoredSession();
				this.state.assignment = assignment;
			}
		}
		if (!token) return;
		$("access-token").value = token;
		const publicCtx = await this.api.call("get_public_assignment_context", {
			assignment,
			token: this.query.get("token") || "",
			password: this.query.get("password") || "",
		});
		this.state.candidateId = publicCtx.candidate_id || "";
		$("candidate-id").value = this.state.candidateId;
		await this.login(true);
	}

	bindAuth() {
		$("login-btn").addEventListener("click", async () => {
			try {
				await this.login(false);
			} catch (error) {
				this.notice(error.message || "Login failed");
			}
		});
	}

	async login(silent) {
		const assignment = ($("assignment-id").value || this.state.assignment || "").trim();
		const candidate = ($("candidate-id").value || this.state.candidateId || "").trim();
		const credential = ($("access-token").value || "").trim();
		const result = await this.api.call("candidate_auth", {
			assignment,
			candidate_id: candidate,
			token: credential,
			password: credential,
		});
		this.state.token = result.session_token;
		this.state.assignment = result.assignment;
		this.state.candidateId = candidate;
		this.saveState();
		await this.loadContext();
		if (this.state.consentAccepted) {
			this.showSystemPanel(result.applicant_name);
		} else {
			this.showWelcomePanel();
		}
		if (!silent && window.frappe?.show_alert) {
			frappe.show_alert({ message: "Signed in successfully", indicator: "green" });
		}
	}

	showConsentPanel(candidateName) {
		$("auth-panel").hidden = true;
		$("welcome-panel").hidden = true;
		$("consent-panel").hidden = false;
		$("system-panel").hidden = true;
		$("candidate-summary").innerText = candidateName || "Candidate";
		this.setStatus("Consent required");
		this.updateConsentButton();
	}

	showSystemPanel(candidateName) {
		$("auth-panel").hidden = true;
		$("welcome-panel").hidden = true;
		$("consent-panel").hidden = true;
		$("system-panel").hidden = false;
		$("candidate-summary").innerText = candidateName || "Candidate";
		this.setStatus("System check required");
	}

	async loadContext() {
		const context = await this.api.call("candidate_context", {});
		this.state.context = context;
		this.state.settings = context.settings || {};
		if (context.attempt?.candidate_consent) {
			this.state.attempt = context.attempt.name;
			this.state.consentAccepted = true;
			this.saveState();
		}
		$("candidate-summary").innerText = `${context.applicant_name || "Candidate"} · ${context.duration_minutes || 60} minutes`;
	}

	bindWelcome() {
		$("welcome-continue-btn").addEventListener("click", () => this.showConsentPanel(this.state.context.applicant_name));
		$("welcome-cancel-btn").addEventListener("click", () => {
			localStorage.removeItem(STORE_KEYS.token);
			localStorage.removeItem(STORE_KEYS.assignment);
			localStorage.removeItem(STORE_KEYS.attempt);
			localStorage.removeItem(STORE_KEYS.candidate);
			window.location.href = "/assessment-portal";
		});
	}

	showWelcomePanel() {
		const context = this.state.context || {};
		$("auth-panel").hidden = true;
		$("welcome-panel").hidden = false;
		$("consent-panel").hidden = true;
		$("system-panel").hidden = true;
		$("welcome-assessment-name").innerText = context.assessment_name || "Assessment";
		$("welcome-candidate-summary").innerText = `${context.applicant_name || "Candidate"}${context.job_position ? ` · ${context.job_position}` : ""}`;
		$("welcome-duration").innerText = `${context.duration_minutes || 60} minutes`;
		$("welcome-total-questions").innerText = context.total_questions || 0;
		$("welcome-expiry").innerText = context.valid_till || "";
		this.setStatus("Review assessment instructions");
	}

	bindConsent() {
		document.querySelectorAll("[data-consent-required]").forEach((checkbox) => {
			checkbox.addEventListener("change", () => this.updateConsentButton());
		});
		$("consent-accept-btn").addEventListener("click", () => this.acceptConsent());
		$("consent-cancel-btn").addEventListener("click", () => this.rejectConsent());
	}

	updateConsentButton() {
		const required = Array.from(document.querySelectorAll("[data-consent-required]"));
		$("consent-accept-btn").disabled = !required.length || required.some((checkbox) => !checkbox.checked);
	}

	getBrowserInfo() {
		const ua = navigator.userAgent || "";
		const browserMatch = ua.match(/(Edg|Chrome|Chromium|Firefox|Safari)\/([\d.]+)/) || [];
		const os = /Windows/i.test(ua)
			? "Windows"
			: /Mac OS X/i.test(ua)
				? "macOS"
				: /Android/i.test(ua)
					? "Android"
					: /iPhone|iPad|iPod/i.test(ua)
						? "iOS"
						: /Linux/i.test(ua)
							? "Linux"
							: "Unknown";
		return {
			browser_name: browserMatch[1] || "Unknown",
			browser_version: browserMatch[2] || "",
			operating_system: os,
			user_agent: ua,
		};
	}

	async acceptConsent() {
		try {
			const result = await this.api.call("accept_consent", {
				browser_info: JSON.stringify(this.getBrowserInfo()),
			});
			this.state.attempt = result.name;
			this.state.consentAccepted = true;
			this.saveState();
			this.showSystemPanel("Consent accepted");
		} catch (error) {
			this.notice(error.message || "Unable to save consent");
		}
	}

	async rejectConsent() {
		try {
			await this.api.call("reject_consent", {
				browser_info: JSON.stringify(this.getBrowserInfo()),
				remarks: "Candidate cancelled consent.",
			});
		} catch {
			// Candidate rejection should still leave the assessment page without requesting permissions.
		}
		localStorage.removeItem(STORE_KEYS.token);
		localStorage.removeItem(STORE_KEYS.assignment);
		localStorage.removeItem(STORE_KEYS.attempt);
		localStorage.removeItem(STORE_KEYS.candidate);
		window.location.href = "/assessment-portal";
	}

	bindSystemCheck() {
		$("check-system-btn").addEventListener("click", () => this.runChecks(true).catch((error) => this.notice(error.message || "System check failed")));
		$("retry-btn").addEventListener("click", () => this.runChecks(true).catch((error) => this.notice(error.message || "System check failed")));
		$("start-assessment-btn").addEventListener("click", () => this.startAssessment());
	}

	async runChecks(requestPermissions) {
		if (!this.state.consentAccepted || !this.state.attempt) {
			this.showConsentPanel("Consent required");
			throw new Error("Consent is required before system check.");
		}
		$("mandatory-warning").hidden = true;
		const browserOk = /(Chrome|Chromium|Edg)\//.test(navigator.userAgent) && !/Firefox\//.test(navigator.userAgent);
		const checks = {
			browser: browserOk,
			internet: navigator.onLine,
			fullscreen: Boolean(document.documentElement.requestFullscreen),
			screen: Boolean(navigator.mediaDevices?.getDisplayMedia),
			camera: Boolean(navigator.mediaDevices?.getUserMedia),
			microphone: Boolean(navigator.mediaDevices?.getUserMedia),
		};
		if (requestPermissions) {
			await this.probeMedia(checks);
		}
		this.state.checks = checks;
		Object.entries(checks).forEach(([key, passed]) => this.setCheck(key, passed));
		const allPassed = Object.values(checks).every(Boolean);
		$("start-assessment-btn").disabled = !allPassed;
		this.setStatus(allPassed ? "System check passed" : "System check failed");
		return allPassed;
	}

	async probeMedia(checks) {
		let cameraProbe = null;
		let micProbe = null;
		let screenProbe = null;
		try {
			cameraProbe = await navigator.mediaDevices.getUserMedia({ video: true });
			checks.camera = true;
			await this.logAudit("Permission Granted", "Camera permission granted during system check.", { permission: "camera" });
		} catch {
			checks.camera = false;
			await this.logAudit("Permission Denied", "Camera permission denied during system check.", { permission: "camera" }).catch(() => {});
		}
		try {
			micProbe = await navigator.mediaDevices.getUserMedia({ audio: true });
			checks.microphone = true;
			await this.logAudit("Permission Granted", "Microphone permission granted during system check.", { permission: "microphone" });
		} catch {
			checks.microphone = false;
			await this.logAudit("Permission Denied", "Microphone permission denied during system check.", { permission: "microphone" }).catch(() => {});
		}
		try {
			screenProbe = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
			checks.screen = true;
			await this.logAudit("Permission Granted", "Screen share permission granted during system check.", { permission: "screen" });
		} catch {
			checks.screen = false;
			await this.logAudit("Permission Denied", "Screen share permission denied during system check.", { permission: "screen" }).catch(() => {});
		}
		[cameraProbe, micProbe, screenProbe].forEach((stream) => {
			if (stream) stream.getTracks().forEach((track) => track.stop());
		});
	}

	setCheck(key, passed) {
		const card = document.querySelector(`[data-check="${key}"]`);
		card.classList.toggle("pass", passed);
		card.classList.toggle("fail", !passed);
		card.classList.toggle("pending", false);
		card.querySelector("span").innerText = passed ? "Passed" : "Failed";
	}

	async checkProxyVpn() {
		try {
			const res = await fetch(
				`/api/method/grid_erp.recruitment_assessment.proxy_detection.check_proxy_for_attempt`,
				{
					method: "POST",
					credentials: "omit",
					headers: { "Content-Type": "application/json", "X-Assessment-Token": this.state.token || "" },
					body: JSON.stringify({ attempt: this.state.attempt, assignment: this.state.assignment }),
				}
			);
			const json = await res.json().catch(() => ({ message: `Server error (${res.status})` }));
			const data = json.message || {};
			if (data.proxy_detected) {
				// Show warning but still allow (HR reviews violations)
				this.notice(
					`⚠️ Warning: VPN or proxy detected (${(data.reasons || []).join("; ")}). This has been logged for review.`
				);
			}
		} catch (_) {
			// Non-blocking — detection failure should not stop the exam
		}
	}

	async startAssessment() {
		try {
			if (!this.state.consentAccepted || !this.state.attempt) throw new Error("Consent is required before starting the assessment.");
			// Proxy/VPN check (non-blocking warning)
			await this.checkProxyVpn();
			const checksPassed = Object.values(this.state.checks).every(Boolean) || await this.runChecks(false);
			if (!checksPassed) throw new Error("Camera, microphone and screen sharing are mandatory for this assessment.");
			await this.media.requestMandatoryStreams();
			await document.documentElement.requestFullscreen();
			const attempt = await this.api.call("start_attempt", { permissions_confirmed: 1 });
			this.state.attempt = attempt.name;
			this.saveState();
			const assessment = await this.api.call("get_assessment", {});
			this.state.questions = assessment.questions || [];
			this.state.responseFiles = assessment.response_files || {};
			this.state.remaining = Math.max(1, (assessment.duration_minutes || 60) * 60);
			this.renderQuestions();
			this.media.startRecording();
			this.media.startSnapshots();
			this.bindProctoringEvents();
			this.startTimer();
			this.startHeartbeat();
			this.setLive(true);
			$("system-panel").hidden = true;
			$("assessment-panel").hidden = false;
			$("live-strip").hidden = false;
			this.setStatus("Assessment in progress");
		} catch (error) {
			$("mandatory-warning").hidden = false;
			this.media.stopStreams();
			await this.logViolation("Permission denied or start blocked", "High", { message: error.message }).catch(() => {});
			this.notice("Camera, microphone and screen sharing are mandatory for this assessment.");
		}
	}

	renderQuestions() {
		const box = $("assessment-box");
		box.innerHTML = "";
		this.videoRecorders = [];
		this.state.questions.forEach((question, index) => {
			const wrap = document.createElement("div");
			wrap.className = "question-card";
			const options = question.options_json ? JSON.parse(question.options_json || "[]") : [];
			const inputHtml = this.renderAnswerInput(question, options, index);
			wrap.innerHTML = `
				<div class="question-meta">
					<strong>${index + 1}. ${this.escape(question.question_text || "")}</strong>
					<span class="question-type">${this.escape(question.question_type || "")}</span>
				</div>
				<div data-question-bank="${this.escape(question.question_bank)}">${inputHtml}</div>
			`;
			box.appendChild(wrap);
			const answerNode = wrap.querySelector("[data-question-bank]");
			if (question.question_type === "Video Response") {
				this.videoRecorders.push(new VideoAnswerRecorder(this, answerNode.querySelector("[data-video-answer]"), question.question_bank));
			} else if (FILE_ANSWER_TYPES.includes(question.question_type)) {
				this.bindFileAnswer(answerNode, question.question_bank);
			}
		});
	}

	bindFileAnswer(node, questionBank) {
		const input = node.querySelector("input[type=file]");
		const status = node.querySelector("[data-file-status]");
		if (this.state.responseFiles[questionBank]) status.textContent = "✓ File saved. Choose another file to replace it.";
		input.addEventListener("change", async () => {
			const file = input.files[0];
			if (!file) return;
			status.textContent = "Uploading…";
			try {
				const result = await this.api.upload("upload_answer_file", this.state.attempt, file, { filename: file.name, question_bank: questionBank });
				this.state.responseFiles[questionBank] = result.file_url;
				status.textContent = `✓ ${file.name} saved.`;
			} catch (error) {
				status.textContent = `Upload failed: ${error.message || "please try again"}`;
			}
		});
	}

	renderAnswerInput(question, options, index) {
		if (question.question_type === "Single Choice MCQ") {
			return options.map((option) => `<label class="radio"><input type="radio" name="q_${index}" value="${this.escape(option.option_text)}"> ${this.escape(option.option_text)}</label>`).join("<br>");
		}
		if (question.question_type === "Multiple Choice") {
			return options.map((option) => `<label class="checkbox"><input type="checkbox" name="q_${index}" value="${this.escape(option.option_text)}"> ${this.escape(option.option_text)}</label>`).join("<br>");
		}
		if (question.question_type === "Video Response") {
			return `
				<div class="video-answer" data-video-answer>
					<video class="video-answer-preview" playsinline hidden></video>
					<p class="video-answer-status" data-video-status>Not recorded yet. Maximum length ${formatClock(VIDEO_ANSWER.maxSeconds)} minutes.</p>
					<div class="action-row">
						<button type="button" class="btn btn-primary" data-action="record">● Start Recording</button>
						<button type="button" class="btn btn-danger" data-action="stop" hidden>■ Stop Recording</button>
						<button type="button" class="btn btn-default" data-action="retake" hidden>↺ Record Again</button>
					</div>
				</div>`;
		}
		if (FILE_ANSWER_TYPES.includes(question.question_type)) {
			const accept = question.question_type === "Image-based Question" ? ' accept="image/*"' : "";
			return `<input type="file" class="form-control answer-input" data-file="1"${accept}><p class="file-answer-status" data-file-status></p>`;
		}
		return `<textarea class="form-control answer-input" rows="4"></textarea>`;
	}

	escape(value) {
		const div = document.createElement("div");
		div.innerText = value || "";
		return div.innerHTML;
	}

	async collectAnswers() {
		return Array.from($("assessment-box").querySelectorAll("[data-question-bank]")).map((node) => {
			const questionBank = node.dataset.questionBank;
			const question = this.state.questions.find((item) => item.question_bank === questionBank) || {};
			let answer = "";
			if (question.question_type === "Single Choice MCQ") {
				answer = node.querySelector("input[type=radio]:checked")?.value || "";
			} else if (question.question_type === "Multiple Choice") {
				answer = JSON.stringify(Array.from(node.querySelectorAll("input[type=checkbox]:checked")).map((el) => el.value));
			} else if (!FILE_ANSWER_TYPES.includes(question.question_type)) {
				answer = node.querySelector("textarea")?.value || "";
			}
			return { question_bank: questionBank, answer, response_file: this.state.responseFiles[questionBank] || "" };
		});
	}

	async autosave(event) {
		if (!this.state.attempt) return;
		await this.api.call("autosave_answers", {
			attempt: this.state.attempt,
			answers: JSON.stringify(await this.collectAnswers()),
			event,
		});
	}

	startTimer() {
		clearInterval(this.state.timer);
		this.tick();
		this.state.timer = setInterval(async () => {
			if (this.state.paused) return;
			this.tick();
			if (this.state.remaining % 15 === 0) await this.autosave("Auto Save").catch(() => {});
			if (this.state.remaining <= 0) await this.submit("Time Expired");
		}, 1000);
	}

	tick() {
		this.state.remaining -= 1;
		const mins = Math.max(0, Math.floor(this.state.remaining / 60));
		const secs = String(Math.max(0, this.state.remaining % 60)).padStart(2, "0");
		document.querySelector('[data-live="timer"]').innerText = `${mins}:${secs}`;
	}

	startHeartbeat() {
		clearInterval(this.state.heartbeat);
		this.state.heartbeat = setInterval(() => {
			const status = {
				attempt_active: true,
				recording_active: Object.values(this.media.recorders).every((recorder) => recorder.state === "recording"),
				camera_active: this.isStreamActive(this.media.cameraStream),
				microphone_active: this.isStreamActive(this.media.microphoneStream),
				screen_active: this.isStreamActive(this.media.screenStream),
				internet_connected: navigator.onLine,
				fullscreen_active: Boolean(document.fullscreenElement),
			};
			this.api.call("heartbeat", { attempt: this.state.attempt, status: JSON.stringify(status) }).catch(() => {});
			this.updateLive(status);
		}, 15000);
	}

	isStreamActive(stream) {
		return Boolean(stream && stream.getTracks().some((track) => track.readyState === "live" && !track.muted));
	}

	bindProctoringEvents() {
		if (this.eventsBound) return;
		this.eventsBound = true;
		window.addEventListener("blur", () => this.logViolation("Window lost focus", "Medium"));
		document.addEventListener("visibilitychange", () => {
			if (document.hidden) this.logViolation("Tab switched", "High");
		});
		document.addEventListener("fullscreenchange", () => {
			if (!document.fullscreenElement && this.state.attempt) this.handleFullscreenExit();
		});
		window.addEventListener("offline", () => this.logViolation("Network disconnected", "High"));
		window.addEventListener("online", () => this.logViolation("Network reconnected", "Low"));
		document.addEventListener("copy", (event) => {
			event.preventDefault();
			this.logViolation("Clipboard copy", "Medium");
		});
		document.addEventListener("paste", (event) => {
			event.preventDefault();
			this.logViolation("Clipboard paste", "Medium");
		});
		document.addEventListener("contextmenu", (event) => {
			event.preventDefault();
			this.logViolation("Right click", "Medium");
		});
		document.addEventListener("keydown", (event) => {
			const devtoolsCombo = event.key === "F12" || (event.ctrlKey && event.shiftKey && ["I", "J", "C"].includes(event.key));
			if (devtoolsCombo) this.logViolation("Developer tools detection", "High");
		});
		setInterval(() => this.detectDevtools(), 2500);
		if (window.screen && window.screen.isExtended) {
			this.logViolation("Multiple monitor detection", "Medium", { isExtended: true });
		}
	}

	async detectDevtools() {
		const threshold = 160;
		const opened = window.outerWidth - window.innerWidth > threshold || window.outerHeight - window.innerHeight > threshold;
		if (opened) await this.logViolation("Developer tools detection", "High");
	}

	async handleFullscreenExit() {
		await this.logViolation("Fullscreen exited", "High");
		this.updateLive({ fullscreen_active: false });
		await document.documentElement.requestFullscreen().catch(() => {});
	}

	async pauseForMedia(kind) {
		if (!this.state.attempt) return;
		this.state.paused = true;
		const isScreen = kind === "screen";
		const isMicrophone = kind === "microphone";
		const title = isScreen ? "Screen sharing stopped" : isMicrophone ? "Microphone stopped" : "Camera stopped";
		const grace = Number(isScreen ? this.state.settings.screen_grace_period_seconds : this.state.settings.camera_grace_period_seconds) || 60;
		$("pause-title").innerText = "Assessment Paused";
		$("pause-message").innerText = isScreen ? "Resume screen sharing to continue the assessment." : isMicrophone ? "Enable microphone to continue the assessment." : "Enable camera to continue the assessment.";
		$("resume-share-btn").innerText = isScreen ? "Resume Screen Share" : isMicrophone ? "Enable Microphone" : "Enable Camera";
		$("pause-overlay").hidden = false;
		await this.logAudit("Assessment Paused", title, { kind });
		if (isScreen) await this.logAudit("Screen Share Stopped", "Screen sharing stopped during assessment.");
		else if (isMicrophone) await this.logAudit("Microphone Stopped", "Microphone stopped during assessment.");
		else await this.logAudit("Camera Stopped", "Camera stopped during assessment.");
		await this.logViolation(title, "High");
		await this.media.captureSnapshot(title).catch(() => {});
		this.startGraceCountdown(grace);
	}

	startGraceCountdown(seconds) {
		clearInterval(this.state.graceTimer);
		let remaining = seconds;
		$("grace-text").innerText = `Auto submit in ${remaining} seconds.`;
		this.state.graceTimer = setInterval(async () => {
			remaining -= 1;
			$("grace-text").innerText = `Auto submit in ${Math.max(0, remaining)} seconds.`;
			if (remaining <= 0) {
				clearInterval(this.state.graceTimer);
				await this.submit("Proctoring Grace Period Expired");
			}
		}, 1000);
	}

	async resumePausedMedia() {
		clearInterval(this.state.graceTimer);
		try {
			if (!this.isStreamActive(this.media.screenStream)) {
				this.media.screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true });
			}
			if (!this.isStreamActive(this.media.cameraStream)) {
				this.media.cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
			}
			if (!this.isStreamActive(this.media.microphoneStream)) {
				this.media.microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
			}
			this.media.bindTrackGuards();
			this.state.paused = false;
			$("pause-overlay").hidden = true;
			await document.documentElement.requestFullscreen().catch(() => {});
			await this.logAudit("Assessment Resumed", "Candidate resumed assessment.");
			await this.logViolation("Assessment resumed", "Low");
		} catch {
			this.startGraceCountdown(30);
		}
	}

	async handleMicrophoneStop() {
		const behavior = this.state.settings.microphone_failure_behavior || "Warn Only";
		await this.logViolation("Microphone muted", "Medium");
		if (behavior === "Pause Assessment") {
			await this.pauseForMedia("microphone");
		} else if (behavior === "Auto Submit") {
			await this.submit("Microphone Failure");
		}
	}

	async logViolation(eventType, severity = "Medium", payload = {}) {
		if (!this.state.attempt) return;
		await this.api.call("save_violation", {
			attempt: this.state.attempt,
			event_type: eventType,
			severity,
			payload: JSON.stringify(payload),
		});
		await this.media.captureSnapshot(eventType).catch(() => {});
	}

	async logAudit(eventType, remarks = "", payload = {}) {
		if (!this.state.attempt) return;
		await this.api.call("save_audit_log", {
			attempt: this.state.attempt,
			event_type: eventType,
			remarks,
			payload: JSON.stringify(payload),
		});
	}

	bindAssessmentActions() {
		$("submit-btn").addEventListener("click", () => this.submit("Candidate Submission"));
		$("submit-paused-btn").addEventListener("click", () => this.submit("Candidate Submission While Paused"));
		$("resume-share-btn").addEventListener("click", () => this.resumePausedMedia());
	}

	async submit(reason) {
		if (!this.state.attempt) return;
		const byCandidate = /^Candidate Submission/.test(reason || "");
		const recorders = this.videoRecorders || [];
		if (byCandidate && recorders.some((recorder) => recorder.isBusy)) {
			this.notice("Please stop your video recording and wait until it is saved before submitting.");
			return;
		}
		const missingVideo = (this.state.questions || []).some(
			(question) => question.question_type === "Video Response" && !this.state.responseFiles[question.question_bank]
		);
		if (byCandidate && missingVideo && !window.confirm("You have not recorded your video yet. Submit anyway?")) return;
		// Time or proctoring ran out mid-recording: keep what was recorded so far.
		await Promise.all(recorders.filter((recorder) => recorder.isRecording).map((recorder) => {
			recorder.elapsed = Math.max(recorder.elapsed, VIDEO_ANSWER.minSeconds);
			return recorder.stop();
		}));
		clearInterval(this.state.timer);
		clearInterval(this.state.heartbeat);
		clearInterval(this.state.graceTimer);
		await this.autosave(reason).catch(() => {});
		await this.media.captureSnapshot("Before Submission").catch(() => {});
		const autoSubmitted = /expired|grace|failure|auto/i.test(reason || "");
		await this.logAudit(autoSubmitted ? "Assessment Auto Submitted" : "Assessment Submitted", reason || "Assessment submitted.").catch(() => {});
		await this.media.stopAndUploadAll();
		const result = await this.api.call("complete_attempt", { attempt: this.state.attempt });
		this.media.stopStreams();
		const underReview = !result.pass_fail || result.pass_fail === "Pending Review";
		this.setStatus(underReview ? "Submitted for review" : `Submitted: ${result.pass_fail}`);
		$("pause-overlay").hidden = true;
		$("submit-btn").disabled = true;
		this.setLive(false);

		// Show result panel
		$("assessment-panel").hidden = true;
		const rp = $("result-panel");
		if (rp) {
			rp.hidden = false;
			const passed = result.pass_fail === "Passed";
			$("result-icon").textContent = passed ? "🏆" : underReview ? "✅" : "📋";
			$("result-title").textContent = passed ? "Congratulations! You Passed!" : "Assessment Submitted";
			$("result-message").textContent = underReview
				? "Thank you! Your responses have been submitted. Our HR team will review them and get back to you."
				: passed
					? `You scored ${result.percentage || 0}%. Well done!`
					: `Thank you for completing the assessment. Your score: ${result.percentage || 0}%.`;

			// Show certificate download button if passed
			if (passed && this.state.attempt) {
				const certSection = $("certificate-section");
				const certBtn = $("certificate-btn");
				if (certSection && certBtn) {
					certBtn.href = `/api/method/grid_erp.recruitment_assessment.certificate.generate_certificate?attempt=${encodeURIComponent(this.state.attempt)}`;
					certSection.hidden = false;
				}
			}
		}
	}

	setLive(active) {
		document.querySelectorAll(".live-strip span").forEach((node) => {
			node.classList.toggle("active", active);
			node.classList.toggle("alert", !active);
		});
	}

	updateLive(status) {
		const mapping = {
			screen: status.screen_active,
			camera: status.camera_active,
			microphone: status.microphone_active,
			internet: status.internet_connected,
			fullscreen: status.fullscreen_active,
		};
		Object.entries(mapping).forEach(([key, active]) => {
			const node = document.querySelector(`[data-live="${key}"]`);
			if (!node) return;
			node.classList.toggle("active", bool(active));
			node.classList.toggle("alert", !bool(active));
		});
	}
}

new ProctoredAssessmentApp().boot().catch((error) => {
	if (window.frappe?.msgprint) frappe.msgprint(error.message || "Unable to load assessment portal");
});
