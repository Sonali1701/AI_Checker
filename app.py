"""Streamlit UI for the AI Checker — handwritten answer-sheet grader."""
from __future__ import annotations

import base64
import io
import json
import os
import zipfile
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv

from grader import MarksItem, MarksScheme
from grader import extract_marks_scheme as claude_extract_marks
from grader import generate_rubric as claude_generate_rubric
from grader import grade_answer_sheet as claude_grade
from grader import pdf_or_image_to_pngs
from grader_gemini import extract_marks_scheme as gemini_extract_marks
from grader_gemini import generate_rubric as gemini_generate_rubric
from grader_gemini import grade_answer_sheet as gemini_grade
from mathpix import build_transcript, ocr_all_pages, synthesize_diagram_regions
from pdf_renderer import build_evaluated_pdf
from costs import compute_cost, DEFAULT_USD_TO_INR

load_dotenv()

st.set_page_config(
    page_title="AI Checker — Handwritten Grader",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# On Streamlit Community Cloud there's no .env — secrets come from st.secrets. Mirror them
# into os.environ so the same os.environ.get(...) reads below work locally and when deployed.
# (For Sheets, paste the whole service-account JSON as GOOGLE_SERVICE_ACCOUNT_JSON.)
try:
    for _k in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT",
               "GOOGLE_CLOUD_LOCATION", "MATHPIX_APP_KEY", "MATHPIX_APP_ID",
               "GOOGLE_SERVICE_ACCOUNT_JSON"):
        if _k not in os.environ and _k in st.secrets:
            _v = st.secrets[_k]
            # A plain secret is a string; the service account may be pasted as a TOML table
            # (a Mapping) — JSON-encode that so resolve_sa_info() can parse it downstream.
            os.environ[_k] = _v if isinstance(_v, str) else json.dumps(dict(_v))
except Exception:
    pass  # no secrets configured (e.g. local run) — fine


# ---------------------------------------------------------------------------
# Look & feel (Physics Wallah brand-leaning indigo)
# ---------------------------------------------------------------------------
_CSS = """
<style>
/* Hide Streamlit dev chrome for a production look */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
[data-testid="stToolbar"] {display: none;}
[data-testid="stDecoration"] {display: none;}
header[data-testid="stHeader"] {background: transparent;}

/* Layout width + spacing */
.block-container {padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1080px;}

/* Brand hero */
.pw-hero {
  background: linear-gradient(120deg, #1E1B4B 0%, #4338CA 58%, #4F46E5 100%);
  border-radius: 16px; padding: 20px 26px; color: #fff;
  display: flex; align-items: center; gap: 18px; margin-bottom: 18px;
  box-shadow: 0 10px 28px rgba(67,56,202,.28);
}
.pw-badge {
  width: 54px; height: 54px; border-radius: 13px; background: #fff;
  color: #312E81; font-weight: 800; font-size: 21px; letter-spacing: .5px;
  display: flex; align-items: center; justify-content: center; flex: 0 0 auto;
  box-shadow: 0 2px 8px rgba(0,0,0,.18);
}
.pw-hero img.pw-logo {width: 54px; height: 54px; border-radius: 13px; background: #fff; object-fit: contain; flex: 0 0 auto;}
.pw-hero h1 {margin: 0; font-size: 1.55rem; line-height: 1.1; font-weight: 800; color:#fff;}
.pw-hero p {margin: 3px 0 0; opacity: .85; font-size: .9rem;}

/* Step header chip */
.pw-step {display: flex; align-items: center; gap: 10px; margin: 0 0 4px;}
.pw-step .n {width: 26px; height: 26px; border-radius: 50%; background: #4F46E5; color: #fff;
  display: flex; align-items: center; justify-content: center; font-weight: 700; font-size: .82rem; flex: 0 0 auto;}
.pw-step .t {font-weight: 700; font-size: 1.06rem; color: #1E1B4B;}
.pw-substep {color: #6B7280; font-size: .85rem; margin: 0 0 8px 36px;}

/* Score banner */
.pw-score {background: linear-gradient(120deg, #1E1B4B, #4338CA 70%); color: #fff; border-radius: 16px;
  padding: 18px 24px; box-shadow: 0 10px 28px rgba(67,56,202,.28);}
.pw-score .lab {opacity: .8; font-size: .8rem; letter-spacing: 1px; text-transform: uppercase;}
.pw-score .big {font-size: 2.5rem; font-weight: 800; line-height: 1.05; margin: 2px 0;}
.pw-score .sub {opacity: .9; font-size: .95rem;}

/* Containers: soft card shadow */
[data-testid="stVerticalBlockBorderWrapper"] {border-radius: 14px;}

/* Buttons */
.stButton > button, .stDownloadButton > button {border-radius: 10px; font-weight: 600;}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)


def _logo_data_uri() -> str | None:
    """Embed a logo if the user drops one in. Otherwise we render a 'PW' badge."""
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("assets/pw_logo.png", "pw_logo.png", "assets/logo.png", "logo.png"):
        p = os.path.join(here, name)
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    return "data:image/png;base64," + base64.b64encode(f.read()).decode()
            except Exception:
                return None
    return None


def brand_header() -> None:
    uri = _logo_data_uri()
    logo = f'<img class="pw-logo" src="{uri}"/>' if uri else '<div class="pw-badge">PW</div>'
    st.markdown(
        f'<div class="pw-hero">{logo}'
        f'<div><h1>AI Checker</h1>'
        f'<p>Automated handwritten answer-sheet evaluation</p></div></div>',
        unsafe_allow_html=True,
    )


def step_header(n: str, title: str, subtitle: str | None = None) -> None:
    st.markdown(
        f'<div class="pw-step"><div class="n">{n}</div><div class="t">{title}</div></div>'
        + (f'<div class="pw-substep">{subtitle}</div>' if subtitle else ""),
        unsafe_allow_html=True,
    )


def score_banner(score: float, mx: float) -> None:
    pct = (score / mx * 100) if mx else 0.0
    st.markdown(
        f'<div class="pw-score"><div class="lab">Total score</div>'
        f'<div class="big">{score:g} / {mx:g}</div>'
        f'<div class="sub">{pct:.0f}%</div></div>',
        unsafe_allow_html=True,
    )


brand_header()


# ---------------------------------------------------------------------------
# Provider dispatch (unchanged behaviour)
# ---------------------------------------------------------------------------
def _dispatch_generate_rubric(qp_pngs):
    if provider == "Claude (Anthropic)":
        return claude_generate_rubric(
            qp_pngs, model=model, api_key=api_key,
            student_class=student_class, subject=subject,
        )
    return gemini_generate_rubric(
        qp_pngs, model=model, api_key=api_key,
        use_vertex=gemini_vertex, project=gemini_project, location=gemini_location,
        student_class=student_class, subject=subject,
    )


def _dispatch_extract_marks(qp_pngs):
    if provider == "Claude (Anthropic)":
        return claude_extract_marks(
            qp_pngs, model=model, api_key=api_key,
            student_class=student_class, subject=subject,
        )
    return gemini_extract_marks(
        qp_pngs, model=model, api_key=api_key,
        use_vertex=gemini_vertex, project=gemini_project, location=gemini_location,
        student_class=student_class, subject=subject,
    )


def _dispatch_grade(**kwargs):
    if provider == "Claude (Anthropic)":
        return claude_grade(
            **kwargs, model=model, api_key=api_key,
            student_class=student_class, subject=subject,
        )
    return gemini_grade(
        **kwargs, model=model, api_key=api_key,
        use_vertex=gemini_vertex, project=gemini_project, location=gemini_location,
        student_class=student_class, subject=subject,
    )


# ---------------------------------------------------------------------------
# Sidebar — settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Settings")
    provider = st.radio(
        "AI provider",
        ["Claude (Anthropic)", "Gemini (Google)"],
        index=1,  # default to Gemini — the marks scheme is locked in code, so Flash grades accurately at a fraction of Opus's cost
    )

    if provider == "Claude (Anthropic)":
        api_key = st.text_input(
            "Anthropic API key",
            value=os.environ.get("ANTHROPIC_API_KEY", ""),
            type="password",
            help="Or set ANTHROPIC_API_KEY in your environment / .env file.",
        )
        model = st.selectbox(
            "Model",
            ["claude-opus-4-7", "claude-sonnet-4-6"],
            index=0,
            help="Opus 4.7 is most accurate; Sonnet 4.6 is faster and cheaper.",
        )
        gemini_vertex = False
        gemini_project = gemini_location = None
    else:
        gemini_mode = st.radio(
            "Gemini endpoint",
            ["Developer API (AI Studio key, AIza…)",
             "Vertex AI Express (Vertex API key, AQ.…)",
             "Vertex AI with ADC (service account / gcloud)"],
            index=0,
            help="Vertex Express keys start with 'AQ.' and use API-key auth on the Vertex endpoint. Developer API keys start with 'AIza' from aistudio.google.com.",
        )
        gemini_vertex = gemini_mode.startswith("Vertex")
        gemini_project = gemini_location = None
        if gemini_mode == "Vertex AI with ADC (service account / gcloud)":
            api_key = ""
            gemini_project = st.text_input("GCP project", value=os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
            gemini_location = st.text_input("Region", value=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
            st.caption("Run `gcloud auth application-default login` once on this machine.")
        else:
            api_key = st.text_input(
                "Google API key",
                value=os.environ.get("GOOGLE_API_KEY", ""),
                type="password",
                help="AI Studio keys: AIza... · Vertex Express keys: AQ...",
            )
        model = st.selectbox(
            "Model",
            ["gemini-2.5-flash", "gemini-2.5-pro"],
            index=0,
            help="2.5 Flash (default) is ~85% cheaper than Pro and is enough now that the "
                 "marks scheme is locked in code. Switch to 2.5 Pro for harder/higher-class papers.",
        )

    with st.expander("🔍 Mathpix OCR (precise mark placement)"):
        use_mathpix = st.checkbox(
            "Use Mathpix",
            value=bool(os.environ.get("MATHPIX_APP_KEY")),
            help="Runs Mathpix OCR on every student page. Gives word-level bounding boxes so ticks/crosses land exactly on the right words. Best for math/science papers.",
        )
        mathpix_key_input = st.text_input(
            "Mathpix app_key",
            value=os.environ.get("MATHPIX_APP_KEY", ""),
            type="password",
            help="Or set MATHPIX_APP_KEY in your environment.",
        )
        if mathpix_key_input:
            os.environ["MATHPIX_APP_KEY"] = mathpix_key_input

    with st.expander("💸 Cost meter & Google Sheet log"):
        usd_to_inr = st.number_input(
            "USD → INR rate",
            min_value=1.0, max_value=200.0, value=DEFAULT_USD_TO_INR, step=0.5,
            help="Used to show each run's cost in ₹. Update to today's rate.",
        )
        from sheets_log import DEFAULT_SPREADSHEET_ID, has_service_account, service_account_email
        _sa_found = has_service_account()
        log_to_sheet = st.checkbox(
            "Log each run to Google Sheet",
            value=_sa_found,
            help="Appends a cost row to a Google Sheet after every evaluation (service-account auth).",
        )
        sheet_id = st.text_input("Spreadsheet ID", value=DEFAULT_SPREADSHEET_ID) if log_to_sheet else DEFAULT_SPREADSHEET_ID
        sheet_tab = st.text_input("Worksheet/tab name (blank = first)", value="") if log_to_sheet else ""
        # Local override: a path to the key file. On Streamlit Cloud leave this blank and put the
        # JSON in st.secrets as GOOGLE_SERVICE_ACCOUNT_JSON (the bootstrap above loads it).
        sa_path_input = st.text_input(
            "Service account JSON path (local only — blank on Cloud)",
            value="",
            help="Local key-file path. On Streamlit Cloud, leave blank and set GOOGLE_SERVICE_ACCOUNT_JSON in Secrets.",
        ) if log_to_sheet else None
        if log_to_sheet:
            _email = service_account_email(sa_path_input or None)
            if _email:
                st.caption(f"Share the sheet (Editor) with:\n`{_email}`")
            else:
                st.warning(
                    "No service-account credentials found. On Streamlit Cloud, add the key JSON to "
                    "Secrets as GOOGLE_SERVICE_ACCOUNT_JSON. Locally, set that env var or enter a path "
                    "above. Then share the sheet (Editor) with the service account's email."
                )

# Credentials readiness — computed once here so every step below can gate on it.
if provider == "Claude (Anthropic)":
    creds_ready = bool(api_key)
    cred_name = "Anthropic API key"
else:
    using_adc = gemini_vertex and not api_key
    creds_ready = bool(api_key) or (using_adc and bool(gemini_project))
    cred_name = "GCP project" if using_adc else "Google API key"


def _show_file_status(f):
    """Confirm an upload actually carried bytes. UploadedFile is a BytesIO over
    record.data with size == len(record.data), so size 0 means the upload itself
    delivered nothing (source file / browser / size-limit issue), not a read bug."""
    if f is None:
        return
    size = getattr(f, "size", None)
    if size is None:
        try:
            size = len(f.getvalue())
        except Exception:
            size = 0
    if size and size > 0:
        st.caption(f"✅ {f.name} · {size/1024:.0f} KB")
    else:
        st.error(f"⚠️ {f.name} uploaded as 0 bytes — re-upload it, or check the source file isn't empty/locked.")


# ---------------------------------------------------------------------------
# Step 1 — exam details
# ---------------------------------------------------------------------------
with st.container(border=True):
    step_header("1", "Exam details", "Grading is calibrated to the class level and subject conventions.")
    dcol1, dcol2 = st.columns(2)
    with dcol1:
        student_class = st.text_input("Class / Grade", placeholder="e.g. Class 10")
    with dcol2:
        subject = st.text_input("Subject", placeholder="e.g. Mathematics")


# ---------------------------------------------------------------------------
# Step 2 — uploads
# ---------------------------------------------------------------------------
with st.container(border=True):
    step_header("2", "Upload files", "One question paper, and one or more answer sheets to grade against it.")
    col1, col2 = st.columns(2)
    with col1:
        qp_file = st.file_uploader("Question paper (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"])
        _show_file_status(qp_file)
    with col2:
        sa_files = st.file_uploader(
            "Student answer sheet(s) — select multiple for batch grading",
            type=["pdf", "png", "jpg", "jpeg"],
            accept_multiple_files=True,
        )
        for _f in (sa_files or []):
            _show_file_status(_f)
        if sa_files and len(sa_files) > 1:
            st.caption(f"📦 Batch: **{len(sa_files)}** sheets will be graded against this paper, one by one.")


# ---------------------------------------------------------------------------
# Step 3 — answer key
# ---------------------------------------------------------------------------
with st.container(border=True):
    step_header("3", "Answer key", "Upload a marking scheme, or let AI draft one from the question paper.")
    key_source = st.radio(
        "How should the AI know the correct answers?",
        ["Upload an answer key / rubric file", "Let AI generate a rubric from the question paper"],
        horizontal=True,
    )

    ak_file = None
    if key_source == "Upload an answer key / rubric file":
        ak_file = st.file_uploader("Answer key / rubric (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"])
        _show_file_status(ak_file)
    else:
        gen_disabled = not (qp_file and api_key)
        if gen_disabled:
            st.info("Upload a question paper and enter your API key (sidebar) to generate a rubric.")
        if st.button("✨ Generate rubric from question paper", disabled=gen_disabled):
            try:
                qp_bytes = qp_file.getvalue()
                if not qp_bytes:
                    st.error(
                        "The question paper file came through empty. This can happen if the "
                        "upload didn't finish or the file got cleared on a refresh — please "
                        "re-upload the question paper above and click again."
                    )
                    st.stop()
                with st.spinner(f"Generating rubric with {model}..."):
                    qp_pngs = pdf_or_image_to_pngs(qp_bytes, qp_file.name)
                    rubric_md = _dispatch_generate_rubric(qp_pngs)
                if not rubric_md or not rubric_md.strip():
                    st.error("The model returned an empty rubric. Try again or switch provider/model.")
                else:
                    st.session_state["rubric_md"] = rubric_md
                    st.session_state["qp_pngs_cached"] = qp_pngs
                    st.success("Rubric generated. Review and edit below, then click Evaluate.")
                    st.rerun()
            except Exception as e:
                st.error(f"Rubric generation failed: {e}")

        # IMPORTANT: don't use `key=` here — Streamlit binds widget state to the key
        # and ignores `value=` on subsequent reruns, which caused the empty-box bug.
        rubric_md_value = st.text_area(
            "Editable rubric (Markdown)",
            value=st.session_state.get("rubric_md", ""),
            height=320,
            help="Edit freely — your changes here are what the grader will use.",
        )
        # Keep session_state in sync with edits
        st.session_state["rubric_md"] = rubric_md_value


# ---------------------------------------------------------------------------
# Step 4 — maximum marks (the locked denominator)
# ---------------------------------------------------------------------------
with st.container(border=True):
    step_header(
        "4", "Maximum marks (denominator)",
        "Read straight from the printed “[N Marks]” tags, then locked — so every student is "
        "scored out of the same total and the AI can't drift on a sub-part's max.",
    )
    ms_disabled = not qp_file
    if ms_disabled:
        st.info("Upload a question paper to detect maximum marks.")
    if st.button("🔢 Detect max marks from question paper", disabled=ms_disabled):
        try:
            qp_bytes = qp_file.getvalue()
            if not qp_bytes:
                st.error("The question paper came through empty — re-upload it and try again.")
                st.stop()
            from grader import marks_scheme_from_pdf
            # 1) Deterministic: read the printed "[N Marks]" tags straight off the PDF text
            #    layer — free, instant, and can't miscount. This is the locked denominator.
            scheme = marks_scheme_from_pdf(qp_bytes, qp_file.name)
            method = "the printed marks tags (regex, no AI)"
            if scheme is None:
                # 2) Fallback: scanned paper / image with no text layer → ask the model.
                if not creds_ready:
                    st.error(
                        "This paper has no text layer (it looks scanned), so the printed marks "
                        f"couldn't be read directly. Enter your {cred_name} in the sidebar to "
                        "detect them with AI instead."
                    )
                    st.stop()
                with st.spinner(f"No text layer found — reading printed marks with {model}..."):
                    qp_pngs = pdf_or_image_to_pngs(qp_bytes, qp_file.name)
                    scheme = _dispatch_extract_marks(qp_pngs)
                st.session_state["qp_pngs_cached"] = qp_pngs
                method = f"AI ({model})"
            else:
                # Regex path didn't render images; drop any stale cache so grading re-renders THIS paper.
                st.session_state.pop("qp_pngs_cached", None)
            st.session_state["marks_items"] = [
                {"qid": it.qid, "max": float(it.max), "part": it.description}
                for it in scheme.items
            ]
            st.session_state["marks_method"] = method
            st.rerun()
        except Exception as e:
            st.error(f"Could not detect marks: {e}")

    _marks_items = st.session_state.get("marks_items")
    if _marks_items:
        _method = st.session_state.get("marks_method")
        if _method:
            st.caption(f"✅ Read from {_method}. Edit any value below — the locked total updates automatically.")
        edited = st.data_editor(
            _marks_items,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "qid": st.column_config.TextColumn("Question / part", help="e.g. Q16.a, Q18.iii"),
                "max": st.column_config.NumberColumn("Max marks", min_value=0.0, step=0.5),
                "part": st.column_config.TextColumn("What it is"),
            },
            key="marks_editor",
        )
        st.session_state["marks_items"] = edited
        _locked_total = sum(float(r.get("max", 0) or 0) for r in edited if str(r.get("qid", "")).strip())
        st.metric("🔒 Locked total (denominator)", f"{_locked_total:g}")
    else:
        st.caption("No marks detected yet — without this, the AI infers the total itself and it can vary between students.")


# ---------------------------------------------------------------------------
# Step 5 — evaluate
# ---------------------------------------------------------------------------
def _checklist_row(ok: bool, label: str) -> str:
    return f"{'✅' if ok else '⬜'} {label}"


with st.container(border=True):
    step_header("5", "Evaluate", "When everything below is ticked, run the evaluation.")

    _key_ok = (
        bool(ak_file) if key_source == "Upload an answer key / rubric file"
        else bool(st.session_state.get("rubric_md", "").strip())
    )
    _n_sheets = len(sa_files) if sa_files else 0
    ready = bool(qp_file and sa_files and creds_ready and _key_ok)

    cca, ccb = st.columns(2)
    with cca:
        st.markdown(_checklist_row(creds_ready, f"{cred_name} set"))
        st.markdown(_checklist_row(bool(qp_file), "Question paper uploaded"))
        st.markdown(_checklist_row(bool(sa_files), f"Answer sheet(s) uploaded{f' — {_n_sheets}' if _n_sheets else ''}"))
    with ccb:
        st.markdown(_checklist_row(_key_ok, "Answer key / rubric ready"))
        st.markdown(_checklist_row(bool(st.session_state.get("marks_items")), "Max marks locked (recommended)"))

    if not creds_ready:
        st.warning(f"Enter your {cred_name} in the sidebar to begin.")

    _btn_label = f"🚀 Evaluate {_n_sheets} sheet{'s' if _n_sheets != 1 else ''}" if _n_sheets else "🚀 Evaluate"
    run = st.button(_btn_label, type="primary", disabled=not ready, use_container_width=True)


# ---------------------------------------------------------------------------
# Run: grade every uploaded sheet against the SAME paper / key / locked marks scheme.
# ---------------------------------------------------------------------------
if run:
    try:
        # Validate the SHARED inputs aren't empty (stale/incomplete uploads yield 0 bytes).
        if not st.session_state.get("qp_pngs_cached") and not qp_file.getvalue():
            st.error("The question paper came through empty. Please re-upload it and click Evaluate again.")
            st.stop()
        if key_source == "Upload an answer key / rubric file" and not ak_file.getvalue():
            st.error("The answer key came through empty. Please re-upload it and click Evaluate again.")
            st.stop()

        # Build the shared inputs ONCE — identical for every student (consistency by design).
        with st.status("Preparing shared inputs…", expanded=False) as prep:
            qp_pngs = st.session_state.get("qp_pngs_cached") or pdf_or_image_to_pngs(qp_file.getvalue(), qp_file.name)
            if key_source == "Upload an answer key / rubric file":
                ak_pngs = pdf_or_image_to_pngs(ak_file.getvalue(), ak_file.name)
                ak_text = None
            else:
                ak_pngs = None
                ak_text = st.session_state["rubric_md"]

            marks_scheme = None
            _items = st.session_state.get("marks_items")
            if _items:
                try:
                    m_items = [
                        MarksItem(qid=str(r["qid"]).strip(), max=float(r["max"]),
                                  description=str(r.get("part") or ""))
                        for r in _items if str(r.get("qid", "")).strip()
                    ]
                    if m_items:
                        marks_scheme = MarksScheme(total=sum(i.max for i in m_items), items=m_items)
                except Exception as e:
                    st.warning(f"Couldn't apply the marks scheme ({e}); grading with AI-inferred totals.")
                    marks_scheme = None
            prep.update(label="Shared inputs ready ✓", state="complete")

        provider_label = "Gemini" if provider != "Claude (Anthropic)" else "Claude"
        results: list[dict] = []
        n = len(sa_files)
        prog = st.progress(0.0, text=f"Grading 0/{n}…")

        for i, sa in enumerate(sa_files, start=1):
            name = sa.name
            prog.progress((i - 1) / n, text=f"Grading {i}/{n}: {name}")
            try:
                sa_bytes = sa.getvalue()
                if not sa_bytes:
                    raise ValueError("file came through empty (0 bytes) — re-upload it")
                sa_pngs = pdf_or_image_to_pngs(sa_bytes, name)

                ocr_pages = ocr_transcript = None
                if use_mathpix:
                    ocr_pages = ocr_all_pages(sa_pngs)
                    for p in ocr_pages:
                        synthesize_diagram_regions(p)
                    ocr_transcript = build_transcript(ocr_pages)

                usage: dict = {}
                report = _dispatch_grade(
                    question_paper_pngs=qp_pngs,
                    student_pngs=sa_pngs,
                    answer_key_pngs=ak_pngs,
                    answer_key_text=ak_text,
                    ocr_transcript=ocr_transcript,
                    ocr_pages=ocr_pages,
                    marks_scheme=marks_scheme,
                    usage_out=usage,
                )

                mathpix_pages = len(sa_pngs) if use_mathpix else 0
                cost = compute_cost(usage, mathpix_pages=mathpix_pages, usd_to_inr=usd_to_inr)

                log_msg = None
                if log_to_sheet:
                    try:
                        from sheets_log import append_cost_row, cost_row
                        row = cost_row(
                            student_file=name, provider=provider_label, model=cost.model or model,
                            student_class=student_class, subject=subject,
                            score=report.total_score, max_score=report.max_total,
                            input_tokens=cost.billed_input_tokens, output_tokens=cost.output_tokens,
                            mathpix_pages=cost.mathpix_pages, llm_cost_usd=cost.llm_cost_usd,
                            mathpix_cost_usd=cost.mathpix_cost_usd, total_usd=cost.total_usd,
                            usd_to_inr=usd_to_inr, total_inr=cost.total_inr,
                            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        )
                        append_cost_row(row, spreadsheet_id=sheet_id, sa=sa_path_input or None,
                                        worksheet=sheet_tab or None)
                        log_msg = ("ok", "Logged to the Google Sheet.")
                    except Exception as e:
                        log_msg = ("err", f"Sheet log failed: {e}")

                pdf_bytes = build_evaluated_pdf(
                    student_pdf_bytes=sa_bytes,
                    student_filename=name,
                    student_pngs=sa_pngs,
                    report=report,
                    ocr_pages=ocr_pages,
                )

                results.append({
                    "ok": True,
                    "student_name": name,
                    "score": report.total_score,
                    "max": report.max_total,
                    "remarks": report.overall_remarks,
                    "questions": [q.model_dump() for q in report.questions],
                    "cost": cost,
                    "provider_label": provider_label,
                    "model": cost.model or model,
                    "pdf_bytes": pdf_bytes,
                    "usd_to_inr": usd_to_inr,
                    "log_msg": log_msg,
                })
            except Exception as e:
                # One bad sheet must not abort the whole batch.
                results.append({"ok": False, "student_name": name, "error": str(e)})
            prog.progress(i / n, text=f"Graded {i}/{n}")

        st.session_state["results"] = results
        st.rerun()
    except Exception as e:
        st.error(f"Something went wrong: {e}")
        st.exception(e)


# ---------------------------------------------------------------------------
# Results panel — rendered from session_state so it persists across reruns.
# NOTE: no nested expanders (Streamlit forbids them) — detail uses plain sections.
# ---------------------------------------------------------------------------
def render_result_detail(res: dict, idx: int) -> None:
    if not res.get("ok"):
        st.error(f"❌ {res['student_name']}: {res.get('error', 'failed')}")
        return
    c1, c2 = st.columns([2, 1])
    with c1:
        score_banner(res["score"], res["max"])
        pct = (res["score"] / res["max"]) if res["max"] else 0.0
        st.progress(min(max(pct, 0.0), 1.0))
        st.caption(f"Student file: `{res['student_name']}`")
    with c2:
        st.download_button(
            "⬇️ Download evaluated PDF",
            data=res["pdf_bytes"],
            file_name=f"evaluated_{res['student_name'].rsplit('.', 1)[0]}.pdf",
            mime="application/pdf",
            use_container_width=True,
            key=f"dl_{idx}",
        )
        lm = res.get("log_msg")
        if lm and lm[0] == "ok":
            st.caption(f"📊 {lm[1]}")
        elif lm and lm[0] == "err":
            st.caption(f"⚠️ {lm[1]}")

    if res.get("remarks"):
        st.markdown("**Overall remarks**")
        st.info(res["remarks"])

    cost = res["cost"]
    plabel = res["provider_label"]
    st.markdown("**💸 Cost of this run**")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Input tokens", f"{cost.billed_input_tokens:,}")
    m2.metric("Output tokens", f"{cost.output_tokens:,}")
    m3.metric(f"{plabel} cost", f"${cost.llm_cost_usd:.4f}")
    m4.metric(f"Mathpix ({cost.mathpix_pages} pg)", f"${cost.mathpix_cost_usd:.4f}")
    m5.metric("Total", f"${cost.total_usd:.4f}", help=f"≈ ₹{cost.total_inr:.2f} at {res['usd_to_inr']:g}/USD")
    st.caption(f"**Total: ${cost.total_usd:.4f} ≈ ₹{cost.total_inr:.2f}**  ·  model `{res['model']}`")
    if not cost.rates_found:
        st.caption(f"⚠️ No pricing entry for `{res['model']}` in costs.py — {plabel} cost shown as $0.")

    qrows = [
        {
            "Question": q.get("qid", ""),
            "Score": q.get("score", 0),
            "Max": q.get("max_score", 0),
            "Remark": q.get("remark", "") or "",
        }
        for q in res["questions"]
    ]
    st.markdown("**Per-question scores**")
    st.dataframe(
        qrows,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Question": st.column_config.TextColumn(width="small"),
            "Score": st.column_config.NumberColumn(width="small"),
            "Max": st.column_config.NumberColumn(width="small"),
            "Remark": st.column_config.TextColumn(width="large"),
        },
    )


_results = st.session_state.get("results")
if _results:
    ok = [r for r in _results if r.get("ok")]
    failed = [r for r in _results if not r.get("ok")]
    st.markdown("###  ")
    with st.container(border=True):
        hc1, hc2 = st.columns([3, 1])
        with hc1:
            st.markdown(f"### 📋 Results — {len(_results)} sheet{'s' if len(_results) != 1 else ''}")
        with hc2:
            if st.button("🔄 New batch", use_container_width=True, key="new_batch"):
                st.session_state.pop("results", None)
                st.rerun()

        if len(_results) > 1:
            # ---- Batch summary + ZIP + table + per-student detail ----
            if ok:
                avg_pct = sum((r["score"] / r["max"] * 100 if r["max"] else 0) for r in ok) / len(ok)
                tot_usd = sum(r["cost"].total_usd for r in ok)
                tot_inr = sum(r["cost"].total_inr for r in ok)
                s1, s2, s3, s4 = st.columns(4)
                s1.metric("Graded", f"{len(ok)}/{len(_results)}")
                s2.metric("Average", f"{avg_pct:.0f}%")
                s3.metric("Total cost", f"${tot_usd:.3f}", help=f"≈ ₹{tot_inr:.2f}")
                s4.metric("Failed", f"{len(failed)}")

                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                    for r in ok:
                        zf.writestr(
                            f"evaluated_{r['student_name'].rsplit('.', 1)[0]}.pdf",
                            r["pdf_bytes"],
                        )
                st.download_button(
                    f"⬇️ Download all {len(ok)} evaluated PDFs (ZIP)",
                    data=buf.getvalue(),
                    file_name="evaluated_sheets.zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="dl_zip",
                )

            trows = []
            for r in _results:
                if r.get("ok"):
                    pct = (r["score"] / r["max"] * 100) if r["max"] else 0
                    trows.append({
                        "Student": r["student_name"],
                        "Score": f"{r['score']:g} / {r['max']:g}",
                        "%": f"{pct:.0f}%",
                        "Cost (₹)": f"{r['cost'].total_inr:.2f}",
                        "Status": "✅",
                    })
                else:
                    trows.append({
                        "Student": r["student_name"], "Score": "—", "%": "—",
                        "Cost (₹)": "—", "Status": f"❌ {str(r.get('error', ''))[:50]}",
                    })
            st.dataframe(trows, use_container_width=True, hide_index=True)

            for idx, r in enumerate(_results):
                label = f"{'✅' if r.get('ok') else '❌'} {r['student_name']}"
                if r.get("ok"):
                    label += f"  —  {r['score']:g}/{r['max']:g}"
                with st.expander(label, expanded=False):
                    render_result_detail(r, idx)
        else:
            # ---- Single sheet → full detail at top level ----
            render_result_detail(_results[0], 0)
