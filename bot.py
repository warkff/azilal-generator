"""Conversation engine for the WhatsApp report bot (transport-agnostic).

Flow: member -> type -> month -> lessons (text / voice transcript / OCR,
accumulated) -> verification (confirm 👌 or edit) -> generate -> files.

`handle(sender, text, media_text=None)` returns a list of actions:
  {"type": "text", "body": str}
  {"type": "files", "files": {filename: bytes}}
`media_text` is the transcript/OCR of an attached voice note or image.
"""
import calendar
import re
from datetime import date

import pipeline
from parse_lessons import parse_lessons, parse_line

MEMBER_KEYS = ["rais", "ahmad", "zaki"]
MEMBER_NAMES = {"rais": "محمد الرايس", "ahmad": "أحمد أوخريب", "zaki": "محمد الزاكي"}
TYPE_NAMES = {"program": "البرنامج الشهري", "stats": "الإحصاء الشهري", "both": "البرنامج + الإحصاء"}
MONTH_WORDS = {pipeline.MONTHS_AR[i]: i + 1 for i in range(12)}
CONFIRM = {"👌", "نعم", "اه", "أجل", "تمام", "موافق", "ok", "okay", "yes", "صحيح", "تأكيد", "اكد", "أكد"}
DONE_WORDS = {"تم", "صافي", "انتهى", "كملت", "خلاص", "done"}

_sessions = {}


def _new_session():
    return {"state": "member", "member": None, "type": None,
            "month": None, "year": None, "lessons": [], "unparsed": []}


def _ask_member():
    return ("مرحباً بك في بوت تقارير المجلس العلمي المحلي بأزيلال 📋\n"
            "شكون العضو؟ رد برقم:\n1️⃣ محمد الرايس\n2️⃣ أحمد أوخريب\n3️⃣ محمد الزاكي")


def _ask_type():
    return "شنو نوع الملف؟\n1️⃣ البرنامج الشهري\n2️⃣ الإحصاء الشهري\n3️⃣ بجوج"


def _ask_month():
    return "أي شهر وسنة؟ (مثال: 10-2026 أو أكتوبر 2026)"


def _ask_lessons(s):
    return (f"صيفط الدروس ديال {pipeline.MONTHS_AR[s['month'] - 1]} {s['year']} "
            "بأي طريقة بغيتي:\n✍️ كتابةً\n🎤 رسالة صوتية\n📷 صورة مكتوبة\n\n"
            "كل درس بعنوانو وتاريخو، مثال:\nتوبة الى الله 12-10\n\n"
            "تقدر تصيفطهم على دفعات، وملي تسالي كتب: تم")


def _summary(s):
    lines = [f"📋 *التحقق قبل التوليد*",
             f"العضو: {MEMBER_NAMES[s['member']]}",
             f"النوع: {TYPE_NAMES[s['type']]}",
             f"الشهر: {pipeline.MONTHS_AR[s['month'] - 1]} {s['year']}",
             f"الدروس ({len(s['lessons'])}):"]
    for i, les in enumerate(s["lessons"], 1):
        lines.append(f"{i}. {les['title']} — {les['day']:02d}/{les['month']:02d}")
    if s["type"] in ("stats", "both"):
        fridays = sum(1 for d in range(1, calendar.monthrange(s["year"], s["month"])[1] + 1)
                      if date(s["year"], s["month"], d).weekday() == 4)
        total = len(s["lessons"]) + fridays + 2
        lines.append(f"الإحصاء: خطب الجمعة {fridays} + الحديث والسيرة 2 ← المجموع {total}")
    if s["unparsed"]:
        lines.append("⚠️ أسطر ما تفهماتش (غادي تتجاهل إلا ما صلحتيهاش):")
        for u in s["unparsed"]:
            lines.append(f"• {u}")
    lines.append("\nرد بـ 👌 أو «نعم» للتأكيد، أو صلّح:\n"
                 "• «2: عنوان جديد 15-10» لتعديل درس\n"
                 "• «حذف 2» لحذف درس\n"
                 "• «زيد: عنوان 20-10» لزيادة درس")
    return "\n".join(lines)


def _parse_month(text):
    text = (text or "").strip()
    m = re.search(r"(\d{1,2})\s*[-/ ]\s*(\d{4})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    for word, num in MONTH_WORDS.items():
        if word in text:
            y = re.search(r"(\d{4})", text)
            return num, int(y.group(1)) if y else date.today().year
    if text.isdigit() and 1 <= int(text) <= 12:
        return int(text), date.today().year
    return None


def _apply_edit(s, text):
    """Try to apply an edit command in verify state. Returns True if handled."""
    m = re.match(r"^\s*حذف\s*(\d+)\s*$", text)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(s["lessons"]):
            s["lessons"].pop(idx)
            return True
        return False
    m = re.match(r"^\s*زيد\s*[:：]?\s*(.+)$", text)
    if m:
        parsed = parse_line(m.group(1))
        if parsed:
            parsed["month"] = parsed["month"] or s["month"]
            parsed["year"] = parsed["year"] or s["year"]
            s["lessons"].append(parsed)
            s["lessons"].sort(key=lambda l: (l["month"] or 99, l["day"]))
            return True
        return False
    m = re.match(r"^\s*(\d+)\s*[:.\\-]\s*(.+)$", text)
    if m:
        idx = int(m.group(1)) - 1
        parsed = parse_line(m.group(2))
        if parsed and 0 <= idx < len(s["lessons"]):
            parsed["month"] = parsed["month"] or s["lessons"][idx]["month"]
            parsed["year"] = parsed["year"] or s["lessons"][idx]["year"]
            s["lessons"][idx] = parsed
            s["lessons"].sort(key=lambda l: (l["month"] or 99, l["day"]))
            return True
    return False


def handle(sender, text, media_text=None):
    """One user turn. Returns list of actions."""
    text = (text or "").strip()
    incoming = media_text if media_text is not None else text
    s = _sessions.get(sender)
    if s is None or text in ("بداية", "ابدأ", "سلام", "مرحبا", "start", "بدء"):
        _sessions[sender] = _new_session()
        return [{"type": "text", "body": _ask_member()}]
    st = s["state"]

    if st == "member":
        pick = {"1": "rais", "2": "ahmad", "3": "zaki"}.get(text)
        if pick is None:
            for key, name in MEMBER_NAMES.items():
                if name.split()[-1] in text or name in text:
                    pick = key
        if pick is None:
            return [{"type": "text", "body": "رد برقم العضو:\n1️⃣ محمد الرايس\n2️⃣ أحمد أوخريب\n3️⃣ محمد الزاكي"}]
        s["member"] = pick
        s["state"] = "type"
        return [{"type": "text", "body": _ask_type()}]

    if st == "type":
        pick = {"1": "program", "2": "stats", "3": "both"}.get(text)
        if pick is None:
            if "برنامج" in text and "إحصاء" not in text and "احصاء" not in text:
                pick = "program"
            elif "إحصاء" in text or "احصاء" in text:
                pick = "stats"
            elif "بجوج" in text or "كلشي" in text:
                pick = "both"
        if pick is None:
            return [{"type": "text", "body": _ask_type()}]
        s["type"] = pick
        s["state"] = "month"
        return [{"type": "text", "body": _ask_month()}]

    if st == "month":
        parsed = _parse_month(text)
        if parsed is None:
            return [{"type": "text", "body": "ما فهمتش الشهر. " + _ask_month()}]
        s["month"], s["year"] = parsed
        s["state"] = "lessons"
        return [{"type": "text", "body": _ask_lessons(s)}]

    if st == "lessons":
        if text in DONE_WORDS:
            if not s["lessons"]:
                return [{"type": "text", "body": "ما زال ما وصلني حتى درس. صيفط الدروس أولاً، أو «بداية» للرجوع."}]
            s["state"] = "verify"
            return [{"type": "text", "body": _summary(s)}]
        lessons, unparsed = parse_lessons(incoming, s["month"], s["year"])
        s["lessons"].extend(lessons)
        s["lessons"].sort(key=lambda l: (l["month"] or 99, l["day"]))
        s["unparsed"].extend(unparsed)
        if lessons:
            note = f"✅ تزادو {len(lessons)} دروس (المجموع {len(s['lessons'])})."
            if unparsed:
                note += f"\n⚠️ {len(unparsed)} سطر ما تفهمش وغادي يبان في التحقق."
            note += "\nزيد صيفط ولا كتب: تم"
            return [{"type": "text", "body": note}]
        return [{"type": "text", "body": "ما قدرت نستخرج حتى درس من هادشي. جرب الصيغة: عنوان الدرس + التاريخ، مثال: توبة الى الله 12-10"}]

    if st == "verify":
        if text in CONFIRM or "👌" in text:
            req = {"member": s["member"], "type": s["type"],
                   "month": s["month"], "year": s["year"],
                   "lessons": [{"title": l["title"], "day": l["day"]} for l in s["lessons"]]}
            try:
                files = pipeline.generate(req)
            except Exception as e:
                return [{"type": "text", "body": f"وقع مشكل في التوليد: {e}\nرد بـ 👌 للمحاولة مجدداً أو «بداية» للرجوع."}]
            _sessions[sender] = _new_session()
            return [{"type": "text", "body": "✅ تم التوليد! ها هو الملف:"},
                    {"type": "files", "files": files},
                    {"type": "text", "body": "إلا بغيتي تقرير آخر، كتب: بداية"}]
        if _apply_edit(s, text):
            return [{"type": "text", "body": "تم التعديل ✏️\n\n" + _summary(s)}]
        lessons, _ = parse_lessons(text, s["month"], s["year"])
        if lessons:
            s["lessons"].extend(lessons)
            s["lessons"].sort(key=lambda l: (l["month"] or 99, l["day"]))
            return [{"type": "text", "body": "تزادو ✏️\n\n" + _summary(s)}]
        return [{"type": "text", "body": "ما فهمتش. رد بـ 👌 للتأكيد، أو عدل بصيغة «رقم: عنوان تاريخ» / «حذف رقم» / «زيد: عنوان تاريخ»، أو «بداية» للرجوع."}]

    _sessions[sender] = _new_session()
    return [{"type": "text", "body": _ask_member()}]
