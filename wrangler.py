import os, sys, re, csv, json, subprocess, tempfile, pathlib, datetime
from collections import defaultdict

# -------------------- CONFIG --------------------
GEMINI_CMD = os.environ.get("GEMINI_CMD", "gemini")     
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  

# -------------------- HELPERS --------------------
def run(cmd, input_text=None):
    """Run a shell command; optionally provide STDIN text; return stdout string."""
    res = subprocess.run(cmd, input=input_text.encode("utf-8") if input_text else None,
                         capture_output=True)
    if res.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{res.stderr.decode('utf-8','ignore')}")
    return res.stdout.decode("utf-8", "ignore")

def pdf_pages(pdf_path):
    out = run(["pdfinfo", pdf_path])
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else 1

def extract_page_text_or_image(pdf_path, page_idx):
    """Try text extraction; if too short, render PNG + OCR. Returns: (text, image_path_or_None)"""
    txt = run(["pdftotext", "-f", str(page_idx), "-l", str(page_idx), pdf_path, "-"])
    if len(txt.strip()) >= 180:
        return txt, None
    # fallback: image + OCR
    tmpdir = tempfile.gettempdir()
    stem = pathlib.Path(pdf_path).stem
    base = os.path.join(tmpdir, f"{stem}-p{page_idx}")
    run(["pdftoppm", "-f", str(page_idx), "-l", str(page_idx), "-png", pdf_path, base])
    img_path = f"{base}-1.png"
    ocr = run(["tesseract", img_path, "stdout"])
    return (ocr if len(ocr.strip())>0 else ""), img_path

# -------------------- GEMINI VIA CLI --------------------

EXTRACT_SYSTEM = """You are a clinical extraction assistant. From PAGE_TEXT, extract only incidental findings and explicit follow-up recommendations.
Return STRICT JSON array; keys exactly:
[
 {
  "patient_id": "string or null",
  "report_date": "YYYY-MM-DD or null",
  "modality": "CT|XR|MRI|US|Other",
  "body_part": "string",
  "finding": "concise finding (<=180 chars)",
  "recommended_followup": "verbatim or null",
  "timeframe": "e.g., '3-6 months' or 'in 6 months' or null",
  "due_by": "YYYY-MM-DD or null",
  "priority": "high|medium|low",
  "confidence": 0.0-1.0
 }
]
Rules:
- Do NOT invent follow-ups; if none present, set recommended_followup=null and timeframe=null.
- Priority=high if wording includes 'urgent','immediate','ASAP','concerning', or critical alerts.
- If report_date exists and timeframe is relative ('in N months'), compute due_by if possible.
JSON only. No prose.
"""

SELF_CHECK_SYSTEM = """Validate and normalize the JSON list against PAGE_TEXT:
- Remove items without clear textual evidence.
- Normalize dates to YYYY-MM-DD.
- If timeframe present but due_by missing and report_date present, compute due_by.
- If confidence < 0.5 set priority=low.
Output STRICT JSON array with the same keys. No prose.
"""

def gemini_extract(page_text, page_image_hint=None):
    # We ground on text (OCR/text-native). If there's an image file, we declare its presence in the prompt as a hint.
    hint = f"\n[SCANNED_PAGE_IMAGE_PATH]: {page_image_hint}" if page_image_hint else ""
    full_prompt = EXTRACT_SYSTEM + "\n---\nPAGE_TEXT:\n" + (page_text or "")[:8000] + hint
    out = run([GEMINI_CMD, "-m", GEMINI_MODEL, "-p", full_prompt])
    return out

def gemini_selfcheck(candidate_json, page_text):
    full_prompt = SELF_CHECK_SYSTEM + "\n---\nPAGE_TEXT:\n" + (page_text or "")[:8000] + "\n---\nCANDIDATE_JSON:\n" + (candidate_json or "")[:8000]
    out = run([GEMINI_CMD, "-m", GEMINI_MODEL, "-p", full_prompt])
    return out

def safe_json_loads(s):
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"(\[.*\])", s or "", re.DOTALL)
        if m:
            try: return json.loads(m.group(1))
            except Exception: return []
        return []

# -------------------- NORMALIZATION --------------------
def add_relative_due_by(row):
    if row.get("due_by") or not row.get("timeframe") or not row.get("report_date"):
        return row
    tf = str(row["timeframe"])
    try:
        base = dtp.parse(row["report_date"]).date()
    except Exception:
        return row
    m = re.search(r"in\s+(\d+)\s+(day|week|month|year)s?", tf, re.I)
    if not m: return row
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit == "day": due = base + relativedelta(days=n)
    elif unit == "week": due = base + relativedelta(weeks=n)
    elif unit == "month": due = base + relativedelta(months=n)
    else: due = base + relativedelta(years=n)
    row["due_by"] = due.isoformat()
    return row

def normalize_row(r, pdf, page):
    row = {
        "patient_id": r.get("patient_id"),
        "report_date": r.get("report_date"),
        "modality": r.get("modality") or "Other",
        "body_part": r.get("body_part") or "",
        "finding": r.get("finding") or "",
        "recommended_followup": r.get("recommended_followup"),
        "timeframe": r.get("timeframe"),
        "due_by": r.get("due_by"),
        "priority": (r.get("priority") or "low").lower(),
        "source_pdf": pathlib.Path(pdf).name,
        "page": page,
        "confidence": float(r.get("confidence") or 0.5),
    }
    return add_relative_due_by(row)

def append_rows(csv_path, rows):
    with open(csv_path, "a", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([
                r["patient_id"] or "",
                r["report_date"] or "",
                r["modality"],
                r["body_part"],
                r["finding"],
                r["recommended_followup"] or "",
                r["timeframe"] or "",
                r["due_by"] or "",
                r["priority"],
                r["source_pdf"],
                r["page"],
                f"{float(r['confidence']):.2f}",
            ])

def write_summary_md(pdf_name, rows):
    outp = pathlib.Path("out/summaries") / (pathlib.Path(pdf_name).stem + ".md")
    lines = [f"# Summary for {pdf_name}", ""]
    if not rows:
        lines.append("_No incidental findings with follow-up found._")
    for r in rows:
        lines += [
            f"- **Page {r['page']} ({r['modality']} • {r['body_part']})**: {r['finding']}",
            f"  - Follow-up: {r['recommended_followup'] or '—'}",
            f"  - Timeframe: {r['timeframe'] or '—'}  •  Due by: {r['due_by'] or '—'}  •  Priority: {r['priority']}  •  Confidence: {r['confidence']:.2f}",
        ]
    outp.write_text("\n".join(lines))

def aggregate_dashboard(csv_path):
    stats = {"total_rows":0,"due_within_30":0,"by_priority":{},"by_modality":{}}
    try:
        import pandas as pd
        df = pd.read_csv(csv_path)
        stats["total_rows"] = int(df.shape[0])
        today = datetime.date.today()
        def within_30(x):
            try:
                d = dtp.parse(str(x)).date()
                delta = (d - today).days
                return -365 <= delta <= 30
            except Exception:
                return False
        if "due_by" in df.columns:
            stats["due_within_30"] = int(df["due_by"].apply(within_30).sum())
        stats["by_priority"] = df["priority"].value_counts(dropna=False).to_dict() if "priority" in df.columns else {}
        stats["by_modality"] = df["modality"].value_counts(dropna=False).to_dict() if "modality" in df.columns else {}
    except Exception:
        pass
    pathlib.Path("out/risk_dashboard.json").write_text(json.dumps(stats, indent=2))

# -------------------- PIPELINE --------------------
def process_pdf(pdf_path):
    n = pdf_pages(pdf_path)
    all_rows = []
    for p in range(1, n+1):
        text, img = extract_page_text_or_image(pdf_path, p)
        # 1) extraction via gemini CLI
        raw = gemini_extract(text, img)
        items = safe_json_loads(raw)
        # 2) self-check normalization
        checked_raw = gemini_selfcheck(json.dumps(items), text)
        checked = safe_json_loads(checked_raw)
        for r in checked:
            all_rows.append(normalize_row(r, pdf_path, p))
    return all_rows

def sweep_folder(folder):
    pdfs = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".pdf")]
    grand = []
    for pdf in pdfs:
        rows = process_pdf(pdf)
        append_rows("out/tasks.csv", rows)
        write_summary_md(os.path.basename(pdf), rows)
        grand.extend(rows)
    aggregate_dashboard("out/tasks.csv")
    print(f"Processed {len(pdfs)} PDFs • extracted {len(grand)} follow-up items.")

# -------------------- Q&A REPL --------------------
QA_SYSTEM = """You answer questions ONLY using the provided CONTEXT (CSV rows + summary bullets).
If a fact is not present, say you cannot find it. Prefer citing source_pdf and page.
Return concise, actionable answers for clinicians."""

def load_context():
    tasks = []
    if os.path.exists("out/tasks.csv"):
        with open("out/tasks.csv") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tasks.append(row)
    summaries = []
    sdir = pathlib.Path("out/summaries")
    if sdir.exists():
        for p in list(sdir.glob("*.md"))[:50]:
            summaries.append(p.read_text()[:4000])
    return tasks[-400:], "\n\n".join(summaries)[:12000]

def qa_answer(q):
    rows, summaries = load_context()
    context_blob = json.dumps(rows)[:12000]
    prompt = (QA_SYSTEM +
              "\n\nCONTEXT TASK_ROWS (JSON rows):\n" + context_blob +
              "\n\nCONTEXT SUMMARIES (markdown excerpts):\n" + summaries +
              "\n\nUSER QUESTION:\n" + q)
    out = run([GEMINI_CMD, "-m", GEMINI_MODEL, "-p", prompt])
    return out.strip()

def repl():
    print("Follow-Up Wrangler QA — commands: help | show metrics | open <pdf> | quit")
    while True:
        try:
            q = input("qa> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye"); break
        if not q: continue
        if q in ("quit","exit"): break
        if q == "help":
            print("Ask natural questions (e.g., 'Who needs follow-up in 30 days?').")
            print("Commands: 'show metrics' (reads out/risk_dashboard.json), 'open <pdf>' shows that summary.")
            continue
        if q.startswith("open "):
            name = q.split(" ",1)[1].strip()
            p = pathlib.Path("out/summaries")/(pathlib.Path(name).stem + ".md")
            print(p.read_text() if p.exists() else f"No summary for {name}")
            continue
        if q == "show metrics":
            p = pathlib.Path("out/risk_dashboard.json")
            print(p.read_text() if p.exists() else "No metrics yet."); continue
        print(qa_answer(q) or "(no answer)")

# -------------------- MAIN --------------------
if __name__ == "__main__":
    if "--qa" in sys.argv:
        repl(); sys.exit(0)
    folder = sys.argv[1] if len(sys.argv)>1 else "sample_reports"
    os.makedirs("out/summaries", exist_ok=True)
    sweep_folder(folder)
