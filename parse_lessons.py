"""Extract (title, day, month) lessons from free Arabic text.

Handles typed text, speech transcripts and OCR output. Supported date
shapes per line:
  - «توبة الى الله 12-10»  / «12/10 توبة الى الله»  / «توبة: 12.10.2026»
  - «توبة الى الله 12 أكتوبر» / «12 شتنبر توبة» / «12 من أكتوبر»
  - day-only, session month is used: «توبة الى الله يوم 12»
  - number words, common in speech: «توبة يوم ثالث» / «الصلاة يوم خمسة»
  - word + month: «الثالث أكتوبر»
  - space-separated (last resort): «توبة 12 10»
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

# Number words -> day number. Ordinals ("يوم ثالث") and cardinals
# ("يوم خمسة"), masculine and feminine; the "ال" forms are generated.
_WORD_NUM_BASE = {}
for _n, _words in {
    1: ["أول", "أولى", "واحد", "واحدة"],
    2: ["ثاني", "ثانية", "اثنان", "اثنين", "اثنتان", "جوج"],
    3: ["ثالث", "ثالثة", "ثلاثة", "ثلاث"],
    4: ["رابع", "رابعة", "أربعة", "أربع", "اربعة", "اربع"],
    5: ["خامس", "خامسة", "خمسة", "خمس"],
    6: ["سادس", "سادسة", "ستة", "ست"],
    7: ["سابع", "سابعة", "سبعة", "سبع"],
    8: ["ثامن", "ثامنة", "ثمانية", "ثمان"],
    9: ["تاسع", "تاسعة", "تسعة", "تسع"],
    10: ["عاشر", "عاشرة", "عشرة", "عشر"],
    11: ["حادي عشر", "حادية عشرة", "أحد عشر", "إحدى عشر", "احد عشر"],
    12: ["ثاني عشر", "ثانية عشرة", "اثنا عشر", "اثنتا عشر"],
    13: ["ثالث عشر", "ثالثة عشرة", "ثلاثة عشر"],
    14: ["رابع عشر", "رابعة عشرة", "أربعة عشر"],
    15: ["خامس عشر", "خامسة عشرة", "خمسة عشر"],
    16: ["سادس عشر", "سادسة عشرة", "ستة عشر"],
    17: ["سابع عشر", "سابعة عشرة", "سبعة عشر"],
    18: ["ثامن عشر", "ثامنة عشرة", "ثمانية عشر"],
    19: ["تاسع عشر", "تاسعة عشرة", "تسعة عشر"],
    20: ["عشرون"],
    21: ["حادي وعشرون", "واحد وعشرون"],
    22: ["ثاني وعشرون", "اثنان وعشرون"],
    23: ["ثالث وعشرون", "ثلاثة وعشرون"],
    24: ["رابع وعشرون", "أربعة وعشرون"],
    25: ["خامس وعشرون", "خمسة وعشرون"],
    26: ["سادس وعشرون", "ستة وعشرون"],
    27: ["سابع وعشرون", "سبعة وعشرون"],
    28: ["ثامن وعشرون", "ثمانية وعشرون"],
    29: ["تاسع وعشرون", "تسعة وعشرون"],
    30: ["ثلاثون"],
    31: ["حادي وثلاثون", "واحد وثلاثون"],
}.items():
    for _w in _words:
        _WORD_NUM_BASE[_w] = _n
# Colloquial day forms: عشرين/ثلاثين instead of عشرون/ثلاثون, and
# "واحد وعشرين" style compounds.
for _w, _n in list(_WORD_NUM_BASE.items()):
    if "وعشرون" in _w:
        _WORD_NUM_BASE[_w.replace("وعشرون", "وعشرين")] = _n
    if "وثلاثون" in _w:
        _WORD_NUM_BASE[_w.replace("وثلاثون", "وثلاثين")] = _n
_WORD_NUM_BASE["عشرين"] = 20
_WORD_NUM_BASE["ثلاثين"] = 30
_WORD_NUM_BASE["اثنين وعشرين"] = 22
_WORD_NUM = dict(_WORD_NUM_BASE)
for _w, _n in _WORD_NUM_BASE.items():
    if not _w.startswith("ال"):
        _WORD_NUM["ال" + _w] = _n
_WORD_ALT = "|".join(sorted(_WORD_NUM, key=len, reverse=True))

# Invisible characters WhatsApp/keyboards insert around Latin digits inside
# RTL text (RLM/LRM, embeddings, isolates) and non-ASCII dashes: all of
# them break date matching while staying invisible in screenshots, so they
# are stripped/normalized before any pattern runs.
_BIDI_RE = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_DASH_RE = re.compile(r"[\u2010-\u2015\u2212]")


def _normalize(text):
    text = _BIDI_RE.sub("", text)
    text = _DASH_RE.sub("-", text)
    return text


_NUM_DATE = re.compile(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})(?:\s*[-/.]\s*(\d{2,4}))?")
_WORD_DATE = re.compile(r"(\d{1,2})\s+(?:(?:شهر|من)\s+)?(" + _MONTH_ALT + r")\b")
_WORD_MONTH_DATE = re.compile(r"(" + _WORD_ALT + r")\s+(?:(?:شهر|من)\s+)?(" + _MONTH_ALT + r")\b")
_MARKER_NUM = re.compile(r"\b(?:اليوم|يوم|نهار|بتاريخ|في)\s+(\d{1,2})\b")
_MARKER_WORD = re.compile(r"\b(?:اليوم|يوم|نهار|بتاريخ)\s+(" + _WORD_ALT + r")\b")
_SPACE_NUM = re.compile(r"(\d{1,2})\s+(\d{1,2})\b")
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
    text = _normalize(line.translate(AR_INDIC)).strip()
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
    m = _WORD_MONTH_DATE.search(text)
    if m:
        day, month = _WORD_NUM[m.group(1)], MONTH_WORDS[m.group(2)]
        title = _clean_title(text, m.span())
        return {"title": title, "day": day, "month": month, "year": None} if title else None
    m = _MARKER_NUM.search(text)
    if m:
        day = int(m.group(1))
        if 1 <= day <= 31:
            title = _clean_title(text, m.span())
            return {"title": title, "day": day, "month": None, "year": None} if title else None
    m = _MARKER_WORD.search(text)
    if m:
        day = _WORD_NUM[m.group(1)]
        title = _clean_title(text, m.span())
        return {"title": title, "day": day, "month": None, "year": None} if title else None
    m = _SPACE_NUM.search(text)
    if m:
        day, month = int(m.group(1)), int(m.group(2))
        if 1 <= day <= 31 and 1 <= month <= 12:
            title = _clean_title(text, m.span())
            return {"title": title, "day": day, "month": month, "year": None} if title else None
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
