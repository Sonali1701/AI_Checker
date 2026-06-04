"""Streamlit UI for the AI Checker — handwritten answer-sheet grader."""
from __future__ import annotations

import os

import streamlit as st
from dotenv import load_dotenv

from grader import generate_rubric as claude_generate_rubric
from grader import grade_answer_sheet as claude_grade
from grader import pdf_or_image_to_pngs
from grader_gemini import generate_rubric as gemini_generate_rubric
from grader_gemini import grade_answer_sheet as gemini_grade
from mathpix import build_transcript, ocr_all_pages, synthesize_diagram_regions
from pdf_renderer import build_evaluated_pdf

load_dotenv()

st.set_page_config(page_title="AI Checker — Handwritten Grader", page_icon="📝", layout="wide")

st.title("📝 AI Checker")
st.caption("Upload a question paper, an answer key (or let AI generate one), and a student's handwritten answer sheet — get an evaluated PDF back.")


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

with st.sidebar:
    st.header("Settings")
    provider = st.radio(
        "AI provider",
        ["Claude (Anthropic)", "Gemini (Google)"],
        index=0,
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
            ["gemini-2.5-pro", "gemini-2.5-flash"],
            index=0,
            help="2.5 Pro is most accurate; 2.5 Flash is faster and cheaper.",
        )
    st.divider()
    st.subheader("Mathpix OCR (optional)")
    use_mathpix = st.checkbox(
        "Use Mathpix for precise mark placement",
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

# --- Step 1: exam details + uploads ---
st.subheader("1. Exam details")
dcol1, dcol2 = st.columns(2)
with dcol1:
    student_class = st.text_input(
        "Class / Grade",
        placeholder="e.g. Class 9",
        help="Grading is calibrated to this class level (difficulty, depth, language expectations).",
    )
with dcol2:
    subject = st.text_input(
        "Subject",
        placeholder="e.g. English",
        help="Subject-specific marking conventions are applied.",
    )

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


st.subheader("2. Upload files")
col1, col2 = st.columns(2)
with col1:
    qp_file = st.file_uploader("Question paper (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"])
    _show_file_status(qp_file)
with col2:
    sa_file = st.file_uploader("Student answer sheet (PDF/PNG/JPG)", type=["pdf", "png", "jpg", "jpeg"])
    _show_file_status(sa_file)

# --- Step 3: answer key source ---
st.subheader("3. Answer key")
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
        height=400,
        help="Edit freely — your changes here are what the grader will use.",
    )
    # Keep session_state in sync with edits
    st.session_state["rubric_md"] = rubric_md_value

# --- Step 4: evaluate ---
st.subheader("4. Evaluate")

if provider == "Claude (Anthropic)":
    creds_ready = bool(api_key)
    cred_name = "Anthropic API key"
else:
    using_adc = gemini_vertex and not api_key
    creds_ready = bool(api_key) or (using_adc and bool(gemini_project))
    cred_name = "GCP project" if using_adc else "Google API key"
if not creds_ready:
    st.warning(f"Enter your {cred_name} in the sidebar to begin.")

# Readiness check
if key_source == "Upload an answer key / rubric file":
    ready = bool(qp_file and ak_file and sa_file and creds_ready)
else:
    ready = bool(qp_file and sa_file and creds_ready and st.session_state.get("rubric_md", "").strip())

if st.button("Evaluate", type="primary", disabled=not ready):
    try:
        # Validate uploads aren't empty (stale/incomplete uploads can yield 0 bytes).
        missing = []
        if not st.session_state.get("qp_pngs_cached") and not qp_file.getvalue():
            missing.append("question paper")
        if not sa_file.getvalue():
            missing.append("student answer sheet")
        if key_source == "Upload an answer key / rubric file" and not ak_file.getvalue():
            missing.append("answer key")
        if missing:
            st.error(
                f"These uploads came through empty: {', '.join(missing)}. "
                "Please re-upload them above and click Evaluate again."
            )
            st.stop()

        with st.spinner("Reading uploads..."):
            qp_pngs = st.session_state.get("qp_pngs_cached") or pdf_or_image_to_pngs(qp_file.getvalue(), qp_file.name)
            sa_pngs = pdf_or_image_to_pngs(sa_file.getvalue(), sa_file.name)

            if key_source == "Upload an answer key / rubric file":
                ak_pngs = pdf_or_image_to_pngs(ak_file.getvalue(), ak_file.name)
                ak_text = None
            else:
                ak_pngs = None
                ak_text = st.session_state["rubric_md"]

        ocr_pages = None
        ocr_transcript = None
        if use_mathpix:
            with st.spinner(f"Running Mathpix OCR on {len(sa_pngs)} page(s)..."):
                ocr_pages = ocr_all_pages(sa_pngs)
                for p in ocr_pages:
                    synthesize_diagram_regions(p)
                ocr_transcript = build_transcript(ocr_pages)
            n_diag = sum(1 for p in ocr_pages for l in p.lines if "D" in l.line_id and "L" not in l.line_id)
            n_text = sum(len(p.lines) for p in ocr_pages) - n_diag
            empty_pages = [p.page for p in ocr_pages if not p.lines]
            st.caption(f"OCR'd {n_text} text lines + {n_diag} diagram regions across {len(ocr_pages)} pages.")
            if empty_pages:
                st.caption(
                    f"ℹ️ No OCR content on page(s) {', '.join(map(str, empty_pages))} "
                    "(cover/form or faint scan) — marks there use heuristic placement."
                )

        with st.spinner(f"Grading with {model}... this can take 1-3 minutes."):
            report = _dispatch_grade(
                question_paper_pngs=qp_pngs,
                student_pngs=sa_pngs,
                answer_key_pngs=ak_pngs,
                answer_key_text=ak_text,
                ocr_transcript=ocr_transcript,
                ocr_pages=ocr_pages,
            )

        st.success(f"Score: {report.total_score} / {report.max_total}")
        st.write("**Remarks:**", report.overall_remarks)

        with st.expander("Per-question scores"):
            st.table([q.model_dump() for q in report.questions])

        with st.spinner("Building evaluated PDF..."):
            pdf_bytes = build_evaluated_pdf(
                student_pdf_bytes=sa_file.getvalue(),
                student_filename=sa_file.name,
                student_pngs=sa_pngs,
                report=report,
                ocr_pages=ocr_pages,
            )

        st.download_button(
            "⬇️ Download evaluated PDF",
            data=pdf_bytes,
            file_name=f"evaluated_{sa_file.name.rsplit('.', 1)[0]}.pdf",
            mime="application/pdf",
        )
    except Exception as e:
        st.error(f"Something went wrong: {e}")
        st.exception(e)
