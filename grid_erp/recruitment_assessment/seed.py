from __future__ import annotations

import frappe
from frappe.utils import flt


ASSESSMENT_INVITATION_TEMPLATE = "Assessment Invitation"
TRIAL_TEMPLATE_NAME = "Trial Basic GK Proctored Test"
INTRO_TEMPLATE_NAME = "General Video Introduction"
INTRO_QUESTION_TITLE = "Video Introduction - Tell us about yourself"
INTRO_QUESTION_TEXT = """Please record a short video (1 to 3 minutes) introducing yourself. Speak clearly and look at the camera. Cover:
1. Your name and where you are from
2. Your education / qualifications
3. Your work experience and key skills
4. Why you want to join us and this role
5. Your strengths, and one area you are working to improve"""


EMAIL_SUBJECT = "Online Assessment Invitation - {{ assessment_template }}"

EMAIL_BODY = """
<div style="font-family: Arial, sans-serif; color: #1f2937; line-height: 1.6; font-size: 14px;">
	<p>Dear {{ applicant_name }},</p>
	<p>Thank you for applying for the position of {{ designation }}.</p>
	<p>You have been invited to complete the following online assessment.</p>
	<table cellpadding="0" cellspacing="0" style="border-collapse: collapse; margin: 18px 0; width: 100%; max-width: 640px;">
		<tr><td style="padding: 6px 0; font-weight: 700; width: 180px;">Assessment Name:</td><td>{{ assessment_template }}</td></tr>
		<tr><td style="padding: 6px 0; font-weight: 700;">Duration:</td><td>{{ duration }}</td></tr>
		<tr><td style="padding: 6px 0; font-weight: 700;">Expiry Date:</td><td>{{ expiry_date }}</td></tr>
		<tr><td style="padding: 6px 0; font-weight: 700;">Assessment Link:</td><td><a href="{{ assessment_link }}">{{ assessment_link }}</a></td></tr>
	</table>
	<p>Before starting the assessment please ensure:</p>
	<ul>
		<li>Google Chrome or Microsoft Edge</li>
		<li>Stable Internet Connection</li>
		<li>Webcam Enabled</li>
		<li>Microphone Enabled</li>
		<li>Screen Sharing Required</li>
	</ul>
	<p>The assessment will not start until all required permissions are granted.</p>
	<p>Best Regards,<br>HR Team</p>
</div>
"""


QUESTIONS = [
	("General Knowledge", "Easy", "Which planet is known as the Red Planet?", ["Earth", "Mars", "Jupiter", "Venus"], "Mars"),
	("General Knowledge", "Easy", "What is the capital of Australia?", ["Sydney", "Melbourne", "Canberra", "Perth"], "Canberra"),
	("General Knowledge", "Easy", "Which ocean is the largest in the world?", ["Atlantic Ocean", "Indian Ocean", "Pacific Ocean", "Arctic Ocean"], "Pacific Ocean"),
	("Sports", "Easy", "How many players are there in a cricket team on the field?", ["9", "10", "11", "12"], "11"),
	("Sports", "Easy", "Which country hosted the 2016 Summer Olympics?", ["China", "Brazil", "Japan", "United Kingdom"], "Brazil"),
	("Sports", "Easy", "In football, what is the maximum duration of regular play?", ["60 minutes", "75 minutes", "90 minutes", "120 minutes"], "90 minutes"),
	("Current Affairs", "Easy", "Which organization publishes the Human Development Index?", ["World Bank", "UNDP", "IMF", "WTO"], "UNDP"),
	("Current Affairs", "Easy", "COP meetings are mainly associated with which issue?", ["Trade", "Climate Change", "Sports", "Banking"], "Climate Change"),
	("Current Affairs", "Easy", "Which currency is used in Japan?", ["Yuan", "Won", "Yen", "Dollar"], "Yen"),
	("Indian History", "Easy", "Who was the first Prime Minister of independent India?", ["Mahatma Gandhi", "Jawaharlal Nehru", "Sardar Patel", "Dr. B. R. Ambedkar"], "Jawaharlal Nehru"),
	("Indian History", "Easy", "The Battle of Plassey was fought in which year?", ["1757", "1857", "1764", "1947"], "1757"),
	("Indian History", "Easy", "Who is known as the Father of the Indian Constitution?", ["Mahatma Gandhi", "Dr. B. R. Ambedkar", "Rajendra Prasad", "Subhas Chandra Bose"], "Dr. B. R. Ambedkar"),
	("Indian Geography", "Easy", "Which is the longest river in India?", ["Ganga", "Yamuna", "Godavari", "Narmada"], "Ganga"),
	("Indian Geography", "Easy", "Which Indian state has the largest area?", ["Maharashtra", "Rajasthan", "Madhya Pradesh", "Uttar Pradesh"], "Rajasthan"),
	("Indian Geography", "Easy", "The Tropic of Cancer passes through how many Indian states?", ["6", "8", "10", "12"], "8"),
	("Indian Constitution", "Easy", "How many houses does the Indian Parliament have?", ["One", "Two", "Three", "Four"], "Two"),
	("Indian Constitution", "Easy", "Fundamental Rights are listed in which part of the Indian Constitution?", ["Part II", "Part III", "Part IV", "Part V"], "Part III"),
	("Indian Constitution", "Easy", "Who is the constitutional head of India?", ["Prime Minister", "President", "Chief Justice", "Speaker"], "President"),
	("Science", "Easy", "What is the chemical symbol for water?", ["CO2", "H2O", "O2", "NaCl"], "H2O"),
	("Science", "Easy", "Which gas is most abundant in Earth's atmosphere?", ["Oxygen", "Nitrogen", "Carbon Dioxide", "Hydrogen"], "Nitrogen"),
	("Science", "Easy", "What is the basic unit of life?", ["Atom", "Cell", "Tissue", "Organ"], "Cell"),
	("Computer Basics", "Easy", "What does CPU stand for?", ["Central Processing Unit", "Computer Personal Unit", "Central Program Utility", "Control Processing User"], "Central Processing Unit"),
	("Computer Basics", "Easy", "Which device is used to input text into a computer?", ["Monitor", "Printer", "Keyboard", "Speaker"], "Keyboard"),
	("Computer Basics", "Easy", "What does URL stand for?", ["Uniform Resource Locator", "Universal Record Link", "User Resource Login", "Unified Router Line"], "Uniform Resource Locator"),
	("Reasoning", "Medium", "Complete the series: 2, 4, 8, 16, ?", ["20", "24", "32", "36"], "32"),
	("Reasoning", "Medium", "If CAT is coded as DBU, how is DOG coded?", ["EPH", "EPI", "CNG", "FQH"], "EPH"),
	("Reasoning", "Medium", "Find the odd one out.", ["Square", "Circle", "Triangle", "Rectangle"], "Circle"),
	("English Basics", "Easy", "Choose the correct synonym of 'Happy'.", ["Sad", "Joyful", "Angry", "Tired"], "Joyful"),
	("English Basics", "Easy", "Choose the correct article: He is ___ honest man.", ["a", "an", "the", "no article"], "an"),
	("English Basics", "Easy", "Identify the verb in the sentence: She writes neatly.", ["She", "writes", "neatly", "sentence"], "writes"),
]


def seed_all():
	create_assessment_invitation_template()
	template = get_or_create_trial_template()
	question_names = [get_or_create_question(*item) for item in QUESTIONS]
	attach_questions_to_template(template, question_names)
	intro_template = get_or_create_intro_template()
	set_default_assessment_template(intro_template.name)


def create_assessment_invitation_template():
	if frappe.db.exists("Email Template", ASSESSMENT_INVITATION_TEMPLATE):
		return
	doc = frappe.get_doc(
		{
			"doctype": "Email Template",
			"name": ASSESSMENT_INVITATION_TEMPLATE,
			"subject": EMAIL_SUBJECT,
			"use_html": 1,
			"response_html": EMAIL_BODY,
		}
	)
	doc.insert(ignore_permissions=True)


def get_or_create_trial_template():
	template_name = frappe.db.get_value("Assessment Template", {"template_name": TRIAL_TEMPLATE_NAME}, "name")
	if template_name:
		return frappe.get_doc("Assessment Template", template_name)

	doc = frappe.new_doc("Assessment Template")
	doc.template_name = TRIAL_TEMPLATE_NAME
	doc.description = (
		"General recruitment screening assessment — GK, Reasoning, English, Science, Computer Basics. "
		"30 minutes | 30 questions | Passing: 40% | Proctored (webcam + screen)"
	)
	doc.is_active = 1
	doc.default_duration_minutes = 30
	doc.default_validity_days = 3
	doc.default_passing_marks = 40
	doc.negative_marking = 0
	doc.randomize_questions = 1
	doc.shuffle_options = 1
	doc.max_attempts = 1
	doc.allow_resume = 1
	doc.evaluation_rule = "Auto Only"

	# Proctoring settings — ON by default so demo is fully working
	doc.webcam_recording = 1
	doc.webcam_snapshots = 1
	doc.microphone_recording = 0
	doc.screen_recording = 0
	doc.fullscreen_enforcement = 1
	doc.tab_switch_detection = 1
	doc.copy_paste_blocking = 1
	doc.right_click_blocking = 1
	doc.devtools_detection = 1

	doc.insert(ignore_permissions=True)
	return doc


def get_or_create_question(category, difficulty, question_text, options, correct_answer):
	title = question_text[:140]
	existing = frappe.db.get_value("Question Bank", {"question_title": title}, "name")
	if existing:
		doc = frappe.get_doc("Question Bank", existing)
	else:
		doc = frappe.new_doc("Question Bank")
		doc.question_title = title

	doc.disabled = 0
	doc.question_type = "Single Choice MCQ"
	doc.category = category
	doc.marks = flt(doc.marks or 1) or 1
	doc.difficulty = difficulty
	doc.question_text = question_text
	doc.correct_answer = correct_answer
	doc.set("options", [])
	for option in options:
		doc.append("options", {"option_text": option, "is_correct": 1 if option == correct_answer else 0})

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)
	return doc.name


def attach_questions_to_template(template, question_names):
	existing = [row.question for row in template.get("questions", []) if row.question]
	for question_name in question_names:
		if question_name not in existing:
			template.append("questions", {"question": question_name})
			existing.append(question_name)

	template.flags.ignore_validate_update_after_submit = True
	template.save(ignore_permissions=True)


def get_or_create_intro_template():
	"""General template for every opening: the candidate records a video introduction."""
	question = frappe.db.get_value("Question Bank", {"question_title": INTRO_QUESTION_TITLE}, "name")
	if not question:
		question_doc = frappe.new_doc("Question Bank")
		question_doc.question_title = INTRO_QUESTION_TITLE
		question_doc.question_type = "Video Response"
		question_doc.category = "Introduction"
		question_doc.difficulty = "Easy"
		question_doc.marks = 10
		question_doc.question_text = INTRO_QUESTION_TEXT
		question_doc.insert(ignore_permissions=True)
		question = question_doc.name

	template_name = frappe.db.get_value("Assessment Template", {"template_name": INTRO_TEMPLATE_NAME}, "name")
	if template_name:
		return frappe.get_doc("Assessment Template", template_name)

	doc = frappe.new_doc("Assessment Template")
	doc.template_name = INTRO_TEMPLATE_NAME
	doc.description = (
		"Record a short 1-3 minute video introducing yourself, right in your browser. "
		"No preparation material is needed - just speak naturally about your background and goals."
	)
	doc.is_active = 1
	doc.default_duration_minutes = 15
	doc.default_validity_days = 3
	doc.default_passing_marks = 0
	doc.negative_marking = 0
	doc.randomize_questions = 0
	doc.shuffle_options = 0
	doc.max_attempts = 1
	doc.allow_resume = 1
	doc.evaluation_rule = "Manual Only"
	doc.append("questions", {"question": question})
	doc.insert(ignore_permissions=True)
	return doc


def set_default_assessment_template(template: str):
	if not frappe.db.get_single_value("Assessment Settings", "default_assessment_template"):
		frappe.db.set_single_value("Assessment Settings", "default_assessment_template", template)
