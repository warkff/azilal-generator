"""Generator pipeline: validated method (LibreOffice macro fill from clean
templates + drawing transplant + date-format style clones).
Stdlib only.
"""
import calendar
import os
import random
import re
import shutil
import subprocess
import threading
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from xml.sax.saxutils import escape as xml_escape

BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(BASE, "templates")
WORK_ROOT = os.environ.get("GEN_WORK", "/tmp/genwork")
LO_PROFILE = os.environ.get("LO_PROFILE", "/tmp/genprofile")
EPOCH = date(1899, 12, 30)

MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو",
             "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر"]

MEMBERS = {
    "rais":  {"first": "محمد", "last": "الرايس",
              "preacher": "محمد الرايس واعظ رسمي",
              "mosques": ["مسجد درب الشمس", "مسجد حي الصناع", "مسجد البنكيت"]},
    "ahmad": {"first": "احمد", "last": "اوخريب",
              "preacher": "احمد اوخريب واعظ رسمي",
              "mosques": ["مسجد اغير ترسال", "مسجد حي الصناع", "مسجد درب الشمس"]},
    "zaki":  {"first": "محمد", "last": "الزاكي",
              "preacher": "محمد الزاكي واعظ رسمي",
              "mosques": ["مسجد حي الصناع", "مسجد درب الشمس", "مسجد البنكيت"]},
}

_LOCK = threading.Lock()

# ---------------- surgical xlsx helpers (string-level, no reserialization) ----

def _load(path):
    z = zipfile.ZipFile(path)
    names = z.namelist()
    data = {n: z.read(n) for n in names}
    infos = {n: z.getinfo(n) for n in names}
    z.close()
    return names, data, infos


def _save(names, data, infos, out):
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            zo.writestr(infos[n], data[n])


def _cell_re(ref):
    return re.compile(r'<c r="' + re.escape(ref) + r'"(?:\s[^>]*?)?(?:/>|>.*?</c>)', re.S)


def _get_cell(xml, ref):
    m = _cell_re(ref).search(xml)
    return m.group(0) if m else None


def _cell_style(elem):
    if elem is None:
        return None
    m = re.search(r'\ss="(\d+)"', elem)
    return m.group(1) if m else None


def _add_numfmt(styles, fid=180):
    if "<numFmts" in styles:
        m = re.search(r'<numFmts count="(\d+)">', styles)
        styles = styles.replace(m.group(0), f'<numFmts count="{int(m.group(1)) + 1}">', 1)
        styles = styles.replace(
            "</numFmts>", f'<numFmt numFmtId="{fid}" formatCode="dd/mm/yyyy"/></numFmts>', 1)
    else:
        styles = styles.replace(
            "<fonts",
            f'<numFmts count="1"><numFmt numFmtId="{fid}" formatCode="dd/mm/yyyy"/></numFmts><fonts', 1)
    return styles


def _clone_xf(styles, base_idx, fid=180):
    m = re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', styles, re.S)
    inner = m.group(2)
    xfs = re.findall(r'<xf\b[^>]*?(?:/>|>.*?</xf>)', inner, re.S)
    base = xfs[base_idx]
    new = base
    if 'numFmtId="' in new:
        new = re.sub(r'numFmtId="\d+"', f'numFmtId="{fid}"', new, count=1)
    else:
        new = new.replace("<xf", f'<xf numFmtId="{fid}"', 1)
    if 'applyNumberFormat="' in new:
        new = re.sub(r'applyNumberFormat="\d+"', 'applyNumberFormat="1"', new, count=1)
    else:
        new = new.replace("<xf", '<xf applyNumberFormat="1"', 1)
    styles = styles[:m.start()] + f'<cellXfs count="{len(xfs) + 1}">' + inner + new + "</cellXfs>" + styles[m.end():]
    return styles, len(xfs)


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_RID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _sheet_part(data, wanted):
    wb = ET.fromstring(data["xl/workbook.xml"])
    rels = ET.fromstring(data["xl/_rels/workbook.xml.rels"])
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    for sh in wb.find("m:sheets", _NS):
        if sh.get("name") == wanted:
            t = relmap[sh.get(_RID)]
            return "xl/" + t if not t.startswith("xl/") else t
    raise KeyError(wanted)


def _fmt_dates(data, sheet_name, first_row, count):
    """Point col-E cells at cloned xfs carrying dd/mm/yyyy (uncached: one
    clone per row — the construction Excel accepted)."""
    part = _sheet_part(data, sheet_name)
    styles = data["xl/styles.xml"].decode()
    if "dd/mm/yyyy" not in styles:
        styles = _add_numfmt(styles)
    sheet = data[part].decode()
    for r in range(first_row, first_row + count):
        ref = f"E{r}"
        cell = _get_cell(sheet, ref)
        if cell is None:
            continue
        styles, idx = _clone_xf(styles, int(_cell_style(cell)))
        sheet = re.sub(r'(<c r="' + ref + r'" s=")\d+(")',
                       r"\g<1>" + str(idx) + r"\g<2>", sheet, count=1)
    data["xl/styles.xml"] = styles.encode()
    data[part] = sheet.encode()


def _transplant_drawings(data, template_path):
    tpl = zipfile.ZipFile(template_path)
    for n in tpl.namelist():
        if n.startswith("xl/drawings/"):
            data[n] = tpl.read(n)


def _transplant_title_paragraph(data, month_name):
    """Replace the program title <a:p> with the original workbook's paragraph
    (correct run segmentation / year placement), swapping in the month."""
    orig = zipfile.ZipFile(os.path.join(TEMPLATES, "program_original.xlsx"))
    od = orig.read("xl/drawings/drawing1.xml").decode()
    oi = od.find("برنامج الأنشطة")
    op = od[od.rfind("<a:p>", 0, oi):od.find("</a:p>", oi) + len("</a:p>")]
    op = op.replace("شتنير", month_name)
    d = data["xl/drawings/drawing1.xml"].decode()
    di = d.find("برنامج الأنشطة")
    start = d.rfind("<a:p>", 0, di)
    end = d.find("</a:p>", di) + len("</a:p>")
    data["xl/drawings/drawing1.xml"] = (d[:start] + op + d[end:]).encode()

# ---------------- LibreOffice macro fill -------------------------------------


def _basic_str(s):
    return '"' + str(s).replace('"', '""') + '"'


def _macro_header(log_name):
    return [
        "Sub LogIt(msg As String)",
        "  Dim f As Integer : f = FreeFile",
        f'  Open "/tmp/{log_name}" For Append As #f',
        "  Print #f, msg",
        "  Close #f",
        "End Sub",
        "Sub T(sh As Object, a As String, v As String) : sh.getCellRangeByName(a).String = v : End Sub",
        "Sub N(sh As Object, a As String, v As Double) : sh.getCellRangeByName(a).Value = v : End Sub",
        "Sub Dt(sh As Object, a As String, y As Integer, m As Integer, d As Integer) : sh.getCellRangeByName(a).Value = DateSerial(y, m, d) : End Sub",
        'Sub Tok(sh As Object, a As String, mon As String, yr As String)',
        "  Dim c As Object : c = sh.getCellRangeByName(a)",
        '  c.String = Replace(Replace(c.String, "«الشهر»", mon), "«السنة»", yr)',
        "End Sub",
        "Sub SaveDoc(doc As Object, outPath As String)",
        "  Dim sp(1) As New com.sun.star.beans.PropertyValue",
        '  sp(0).Name = "FilterName" : sp(0).Value = "Calc MS Excel 2007 XML"',
        '  sp(1).Name = "Overwrite" : sp(1).Value = True',
        "  doc.storeToURL(ConvertToURL(outPath), sp())",
        "  doc.close(True)",
        "End Sub",
    ]


def _fill_lines(sh_name, lessons, member, year, month, kind):
    L = [f'  sh = doc.Sheets.getByName({sh_name})']
    last_shell = 26 if kind == "program" else 25
    first_extra = last_shell + 1
    last_row = 17 + len(lessons)
    if last_row >= first_extra:
        L.append("  Dim srcA As New com.sun.star.table.CellRangeAddress")
        L.append("  Dim dstA As New com.sun.star.table.CellAddress")
        for r in range(first_extra, last_row + 1):
            L.append(f'  srcA = sh.getCellRangeByName("B{last_shell}:L{last_shell}").RangeAddress')
            L.append(f'  dstA = sh.getCellRangeByName("B{r}").CellAddress')
            L.append("  sh.copyRange(dstA, srcA)")
    for i, les in enumerate(lessons):
        r = 18 + i
        att = les["attendance"]
        L.append(f'  N sh, "B{r}", {i + 1}')
        L.append(f'  T sh, "C{r}", {_basic_str(les["title"])}')
        L.append(f'  T sh, "D{r}", {_basic_str(les.get("type") or "درس ديني")}')
        L.append(f'  Dt sh, "E{r}", {year}, {month}, {les["day"]}')
        if kind == "program":
            L.append(f'  T sh, "F{r}", {_basic_str(member["preacher"])}')
            L.append(f'  T sh, "G{r}", {_basic_str(les["mosque"])}')
        else:
            L.append(f'  T sh, "F{r}", "30د"')
            L.append(f'  T sh, "G{r}", {_basic_str(member["preacher"])}')
        L.append(f'  T sh, "H{r}", "المساجد"')
        L.append(f'  T sh, "I{r}", "حضري"')
        L.append(f'  T sh, "J{r}", "العموم"')
        L.append(f'  N sh, "K{r}", {att}')
    return L


def _build_macro(req, workdir):
    member = MEMBERS[req["member"]]
    month, year = req["month"], req["year"]
    month_name = MONTHS_AR[month - 1]
    lessons = req["lessons"]
    kinds = req["kinds"]
    L = _macro_header("gen_log.txt")
    L.append("Sub Main")
    L.append("  On Error Goto ErrH")
    L.append("  Dim doc As Object, sh As Object")
    L.append("  Dim props(1) As New com.sun.star.beans.PropertyValue")
    L.append('  props(0).Name = "Hidden" : props(0).Value = True')
    L.append('  props(1).Name = "UpdateDocMode" : props(1).Value = 0')
    if "program" in kinds:
        L.append(f'  doc = StarDesktop.loadComponentFromURL(ConvertToURL("{workdir}/in_program.xlsx"), "_blank", 0, props())')
        L += _fill_lines('"الأنشطة الثقافية"', lessons, member, year, month, "program")
        L.append(f'  SaveDoc doc, "{workdir}/out_program.xlsx"')
        L.append('  LogIt "program done"')
    if "stats" in kinds:
        fridays = sum(1 for d in range(1, calendar.monthrange(year, month)[1] + 1)
                      if date(year, month, d).weekday() == 4)
        total = len(lessons) + fridays + 2
        L.append(f'  doc = StarDesktop.loadComponentFromURL(ConvertToURL("{workdir}/in_stats.xlsx"), "_blank", 0, props())')
        L.append('  sh = doc.Sheets.getByName("المنجز من الأنشطة القارة")')
        L.append(f'  Tok sh, "B6", {_basic_str(month_name)}, {_basic_str(year)}')
        L.append(f'  Tok sh, "B10", {_basic_str(month_name)}, {_basic_str(year)}')
        L.append(f'  N sh, "C16", {total}')
        L.append('  sh = doc.Sheets.getByName("الأنشطة الثقافية")')
        L.append(f'  Tok sh, "C8", {_basic_str(month_name)}, {_basic_str(year)}')
        L.append(f'  Tok sh, "B12", {_basic_str(month_name)}, {_basic_str(year)}')
        L += _fill_lines('"الأنشطة الثقافية"', lessons, member, year, month, "stats")
        L.append('  sh = doc.Sheets.getByName("الأعضاء والمؤطرون")')
        L.append(f'  Tok sh, "D6", {_basic_str(month_name)}, {_basic_str(year)}')
        L.append(f'  Tok sh, "B10", {_basic_str(month_name)}, {_basic_str(year)}')
        L.append(f'  T sh, "E30", {_basic_str(member["first"])}')
        L.append(f'  T sh, "F30", {_basic_str(member["last"])}')
        L.append(f'  N sh, "G30", {len(lessons)}')
        L.append('  N sh, "M30", 2')
        L.append(f'  N sh, "T30", {fridays}')
        L.append(f'  N sh, "W30", {total}')
        L.append(f'  SaveDoc doc, "{workdir}/out_stats.xlsx"')
        L.append('  LogIt "stats done"')
    L.append("  Exit Sub")
    L.append("ErrH:")
    L.append('  LogIt "ERROR " & Err & ": " & Error$')
    L.append("End Sub")
    return "\n".join(L)


def _ensure_profile():
    mod = os.path.join(LO_PROFILE, "user", "basic", "Standard", "Module1.xba")
    if os.path.exists(mod):
        return
    os.makedirs(os.path.dirname(mod), exist_ok=True)
    # initialize a fresh profile with a dummy conversion
    dummy = os.path.join(TEMPLATES, "program.xlsx")
    subprocess.run(["soffice", "--headless", "--norestore",
                    f"-env:UserInstallation=file://{LO_PROFILE}",
                    "--convert-to", "xlsx", "--outdir", "/tmp", dummy],
                   capture_output=True, timeout=120)
    os.makedirs(os.path.dirname(mod), exist_ok=True)


def _write_macro(code):
    path = os.path.join(LO_PROFILE, "user", "basic", "Standard", "Module1.xba")
    xba = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">\n'
           '<script:module xmlns:script="http://openoffice.org/2000/script" '
           'script:name="Module1" script:language="StarBasic">'
           + xml_escape(code) + "</script:module>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xba)


def _run_lo():
    try:
        os.remove("/tmp/gen_log.txt")
    except OSError:
        pass
    subprocess.run(["soffice", "--headless", "--norestore",
                    f"-env:UserInstallation=file://{LO_PROFILE}",
                    "macro:///Standard.Module1.Main"],
                   capture_output=True, timeout=240)
    log = ""
    if os.path.exists("/tmp/gen_log.txt"):
        with open("/tmp/gen_log.txt", encoding="utf-8") as f:
            log = f.read()
    if "ERROR" in log:
        raise RuntimeError("LibreOffice macro error: " + log.strip())
    return log

# ---------------- public entry ------------------------------------------------


def normalize(req):
    member = MEMBERS[req["member"]]
    lessons = []
    for i, les in enumerate(req["lessons"]):
        att = les.get("attendance")
        if att in (None, "", 0):
            att = random.randint(40, 199)
        mosque = (les.get("mosque") or "").strip()
        if not mosque:
            mosque = member["mosques"][i % len(member["mosques"])]
        lessons.append({"title": str(les["title"]).strip(),
                        "day": int(les["day"]),
                        "type": (les.get("type") or "درس ديني"),
                        "mosque": mosque,
                        "attendance": int(att)})
    kinds = req.get("kinds") or (["program", "stats"] if req.get("type") == "both"
                                  else [req.get("type", "program")])
    return {"member": req["member"], "month": int(req["month"]),
            "year": int(req["year"]), "lessons": lessons, "kinds": kinds}


_GEN_LOCK = threading.Lock()


def _generate_locked(raw_req):
    """Returns dict {filename: bytes}."""
    req = normalize(raw_req)
    member = MEMBERS[req["member"]]
    month_name = MONTHS_AR[req["month"] - 1]
    full_name = member["preacher"].replace(" واعظ رسمي", "")
    with _LOCK:
        _ensure_profile()
        workdir = os.path.join(WORK_ROOT, "job")
        shutil.rmtree(workdir, ignore_errors=True)
        os.makedirs(workdir, exist_ok=True)
        if "program" in req["kinds"]:
            shutil.copy(os.path.join(TEMPLATES, "program.xlsx"),
                        f"{workdir}/in_program.xlsx")
        if "stats" in req["kinds"]:
            shutil.copy(os.path.join(TEMPLATES, "stats.xlsx"),
                        f"{workdir}/in_stats.xlsx")
        _write_macro(_build_macro(req, workdir))
        _run_lo()
        out = {}
        for kind, ar in (("program", "البرنامج_الشهري"), ("stats", "الإحصاء_الشهري")):
            if kind not in req["kinds"]:
                continue
            lo_out = f"{workdir}/out_{kind}.xlsx"
            if not os.path.exists(lo_out):
                raise RuntimeError(f"LibreOffice did not produce {kind} output")
            names, data, infos = _load(lo_out)
            tpl = os.path.join(TEMPLATES, f"{kind}.xlsx")
            _transplant_drawings(data, tpl)
            if kind == "program":
                _transplant_title_paragraph(data, month_name)
            _fmt_dates(data, "الأنشطة الثقافية", 18, len(req["lessons"]))
            final = f"{workdir}/final_{kind}.xlsx"
            _save(names, data, infos, final)
            with open(final, "rb") as f:
                out[f"{ar}_{month_name}_{req['year']}_{full_name}.xlsx"] = f.read()
        return out


def generate(req):
    """Serialize generations: the free host has ~512MB RAM, and each
    LibreOffice run is heavy — never run two at once."""
    with _GEN_LOCK:
        return _generate_locked(req)
