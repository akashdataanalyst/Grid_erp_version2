from __future__ import annotations

"""Bulk Question Import from Excel or CSV into the Question Bank.

Expected columns (case-insensitive, order flexible):
  question_title  – required
  question_type   – Single Choice MCQ / Multiple Choice / True/False / Descriptive / Coding
  category        – optional
  difficulty      – Easy / Medium / Hard
  marks           – float
  negative_marking – float
  question_text   – full HTML/text body (optional, falls back to title)
  correct_answer  – for MCQ: option text; for Multiple Choice: comma-separated
  option_1 … option_6 – MCQ options (up to 6)
"""

import io

import frappe
from frappe import _
from frappe.utils import cstr, flt


REQUIRED_COLUMNS = {"question_title"}
VALID_TYPES = {
    "Single Choice MCQ",
    "Multiple Choice",
    "True/False",
    "Descriptive",
    "Coding",
    "File Upload",
}
VALID_DIFFICULTIES = {"Easy", "Medium", "Hard"}


def _normalize_header(h: str) -> str:
    return h.strip().lower().replace(" ", "_")


def _parse_excel(file_content: bytes) -> list[dict]:
    try:
        import openpyxl
    except ImportError:
        frappe.throw(_("openpyxl not installed. Run: pip install openpyxl --break-system-packages"))

    wb = openpyxl.load_workbook(io.BytesIO(file_content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []

    headers = [_normalize_header(str(h or "")) for h in rows[0]]
    records = []
    for row in rows[1:]:
        if not any(row):
            continue
        records.append(dict(zip(headers, [cstr(v or "").strip() for v in row])))
    return records


def _parse_csv(file_content: bytes) -> list[dict]:
    import csv

    text = file_content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    records = []
    for row in reader:
        normalized = {_normalize_header(k): cstr(v or "").strip() for k, v in row.items()}
        records.append(normalized)
    return records


def _row_to_question(row: dict) -> dict | None:
    title = row.get("question_title", "").strip()
    if not title:
        return None

    q_type = row.get("question_type", "").strip()
    if q_type not in VALID_TYPES:
        q_type = "Single Choice MCQ"

    difficulty = row.get("difficulty", "").strip()
    if difficulty not in VALID_DIFFICULTIES:
        difficulty = "Medium"

    # Build options list for MCQ
    options = []
    for i in range(1, 7):
        opt = row.get(f"option_{i}", "").strip()
        if opt:
            options.append(opt)

    # Correct answer
    correct = row.get("correct_answer", "").strip()

    return {
        "question_title": title,
        "question_type": q_type,
        "category": row.get("category", "").strip() or "General",
        "difficulty": difficulty,
        "marks": flt(row.get("marks", 1) or 1),
        "negative_marking": flt(row.get("negative_marking", 0) or 0),
        "question_text": row.get("question_text", "").strip() or title,
        "correct_answer": correct,
        "_options": options,
    }


@frappe.whitelist()
def import_questions_from_file(file_url: str, section: str | None = None) -> dict:
    """Import questions from an uploaded Excel or CSV file.

    ``file_url`` is the URL returned after uploading a file in ERPNext
    (e.g. /private/files/questions.xlsx).

    Returns a summary dict.
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection({"System Manager", "HR Manager", "HR User", "Recruiter"}):
        frappe.throw(_("Not permitted"), frappe.PermissionError)

    # Read file from Frappe file manager
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = file_doc.get_full_path()
    with open(file_path, "rb") as f:
        content = f.read()

    fname = (file_doc.file_name or "").lower()
    if fname.endswith(".xlsx") or fname.endswith(".xls"):
        rows = _parse_excel(content)
    elif fname.endswith(".csv"):
        rows = _parse_csv(content)
    else:
        frappe.throw(_("Unsupported file type. Please upload .xlsx or .csv"))

    created = []
    skipped = []
    errors = []

    for idx, row in enumerate(rows, start=2):  # 2 because row 1 is headers
        qdata = _row_to_question(row)
        if not qdata:
            skipped.append({"row": idx, "reason": "Empty question title"})
            continue

        # Skip duplicate titles
        if frappe.db.exists("Question Bank", {"question_title": qdata["question_title"]}):
            skipped.append({"row": idx, "reason": f"Already exists: {qdata['question_title']}"})
            continue

        try:
            doc = frappe.new_doc("Question Bank")
            doc.question_title = qdata["question_title"]
            doc.question_type = qdata["question_type"]
            doc.category = qdata["category"]
            doc.difficulty = qdata["difficulty"]
            doc.marks = qdata["marks"]
            doc.negative_marking = qdata["negative_marking"]
            doc.question_text = qdata["question_text"]
            doc.correct_answer = qdata["correct_answer"]
            if section:
                doc.assessment_section = section

            for opt_text in qdata["_options"]:
                doc.append("options", {"option_text": opt_text})

            doc.insert(ignore_permissions=True)
            created.append(doc.name)
        except Exception as exc:
            errors.append({"row": idx, "title": qdata.get("question_title", ""), "error": str(exc)})

    frappe.db.commit()

    return {
        "created": len(created),
        "skipped": len(skipped),
        "errors": len(errors),
        "created_names": created[:20],  # first 20 for display
        "error_details": errors[:10],
        "message": _("Import complete: {0} created, {1} skipped, {2} errors").format(
            len(created), len(skipped), len(errors)
        ),
    }


@frappe.whitelist()
def get_import_template() -> str:
    """Return a CSV template string for download."""
    import csv, io

    headers = [
        "question_title", "question_type", "category", "difficulty",
        "marks", "negative_marking", "question_text",
        "correct_answer", "option_1", "option_2", "option_3", "option_4",
    ]
    sample = [
        "What is Python?", "Single Choice MCQ", "Programming", "Easy",
        "1", "0", "What is Python?",
        "A programming language", "A snake", "A programming language", "A database", "A browser",
    ]

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerow(sample)
    return buf.getvalue()
