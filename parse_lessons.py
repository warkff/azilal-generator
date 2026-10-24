"""Extract (title, day, month) lessons from free Arabic text.

Handles typed text, speech transcripts and OCR output. Supported date
shapes per line:
  - «توبة الى الله 12-10»  / «12/10 توبة الى الله»  / «توبة: 12.10.2026»
  - «توبة الى الله 12 أكتوبر» / «12 شتنبر توبة»
Arabic-Indic digits (٠١٢٣) are normalized. Lines without a date are
returned as `unparsed` so the caller can show them for correction.
"""
import re

AR_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

MONTH_WORDS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4,
    "ماي": 5, "مايو": 5, "يونيو": 6, "يوليوز": 7, "يوليو": 7,
    "غشت": 8, "أغسطس": 8, "اغسطس": 8, "شتنبر": 9, "سبتمبر": 9,
    "أكتوبر": 10, "اكتوبر": 10, "نونبر": 11, "نوفمبر": 11,
    "دجنبر": 12, "ديسمبر": 12,
}
_MONTH_ALT = "|".join(sorted(MONTH_WORDS, key=len, reverse=True))

_NUM_DATE = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})(?:\s*[-/.]\s*(\d{2,4}))?")
_WORD_DATE = re.compile(r"(\d{1,2})\s+(?:شهر\s+)?(" + _MONTH_ALT + r")\b")
_BULLET = re.compile(r"^\s*(?:[-•*]|\d{1,2}\s*[-.)،:]|\(\d{1,2}\))\s*")
_SEP_TRIM = " \t:：-–—،,؛;|/\\"


def _clean_title(text, span):
    title = (text[:span[0]] + " " + text[span[1]:]).strip()
    title = _BULLET.sub("", title)
    title = re.sub(r"\s+", " ", title).strip(_SEP_TRIM)
    title = re.sub(r"^(في|يوم|بتاريخ|تاريخ)\s+", "", title).strip(_SEP_TRIM)
    title = re.sub(r"\s+(في|يوم|بتاريخ)$", "", title).strip(_SEP_TRIM)
    return title


def parse_line(line):
    """Return {title, day, month|None, year|None} or None."""
    text = line.translate(AR_INDIC).strip()
    if not text:
        return None
    m = _WORD_DATE.search(text)
    if m:
        day, month = int(m.group(1)), MONTH_WORDS[m.group(2)]
        title = _clean_title(text, m.span())
        return {"title": title, "day": day, "month": month, "year": None} if title else None
    m = _NUM_DATE.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        if 1 <= day <= 31 and 1 <= month <= 12:
            title = _clean_title(text, m.span())
            return {"title": title, "day": day, "month": month, "year": year} if title else None
    return None


def parse_lessons(text, default_month=None, default_year=None):
    """Parse a whole message. Returns (lessons, unparsed_lines).
    Lessons get month/year defaults applied and are sorted by day."""
    lessons, unparsed = [], []
    for raw in re.split(r"[\n\r]+|(?<=\S)\s*[؛;،]\s*", text or ""):
        line = raw.strip()
        if not line:
            continue
        parsed = parse_line(line)
        if parsed:
            if parsed["month"] is None:
                parsed["month"] = default_month
            if parsed["year"] is None:
                parsed["year"] = default_year
            lessons.append(parsed)
        else:
            unparsed.append(line)
    lessons.sort(key=lambda l: (l["month"] or 99, l["day"]))
    return lessons, unparsed
