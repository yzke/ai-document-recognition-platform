# Recognition Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add low-risk recognition-stage improvements: OCR confidence metrics, optional bbox-based text ordering, and single-page OCR rerun.

**Architecture:** Keep the current Flask/RapidOCR pipeline. Extend page result metadata without changing storage format incompatibly; add one rerun endpoint that reuses the existing OCR service. Frontend exposes settings and buttons only where they are relevant.

**Tech Stack:** Flask, RapidOCR ONNX Runtime, PyMuPDF, vanilla JS/CSS.

## Global Constraints

- Do not add new OCR engines in this round.
- Do not implement field extraction or material generation in this round.
- Preserve current default recognition behavior unless the user enables an option.
- Keep changes small and compatible with existing history data.

---

### Task 1: OCR Confidence And Optional Reordering

**Files:**
- Modify: `ocr_app/ocr.py`
- Modify: `ocr_app/routes.py`
- Modify: `templates/index.html`

**Interfaces:**
- Produces: `OcrService.ocr_page(..., reorder=False, low_conf_threshold=0.75) -> dict`
- Produces page metadata: `avg_score`, `min_score`, `low_conf_count`, `low_conf_threshold`

- [ ] Add optional bbox ordering and confidence summary in `ocr_app/ocr.py`.
- [ ] Parse `ocr_reorder` and `low_conf_threshold` in `ocr_app/routes.py`.
- [ ] Add advanced settings controls in `templates/index.html`.
- [ ] Run `python3 -m py_compile app.py ocr_app/*.py`.

### Task 2: Single Page OCR Rerun

**Files:**
- Modify: `ocr_app/ocr.py`
- Modify: `ocr_app/routes.py`

**Interfaces:**
- Produces: `OcrService.rerun_page(job_id, page_no, dpi, keywords, reorder, low_conf_threshold) -> dict`
- Produces API: `POST /api/jobs/<job_id>/ocr/<page_no>/rerun`

- [ ] Implement page result replacement and candidate recomputation.
- [ ] Mark existing AI result as stale when OCR reruns.
- [ ] Rebuild `full_text.txt` after rerun when possible.
- [ ] Run Python syntax check.

### Task 3: Frontend Controls And Feedback

**Files:**
- Modify: `static/app.js`
- Modify: `static/app.css`
- Modify: `templates/index.html`

**Interfaces:**
- Consumes: page metadata `avg_score`, `low_conf_count`, `stale_after_ocr`.
- Consumes: rerun API response from Task 2.

- [ ] Show low-confidence chips in page list.
- [ ] Add a “重跑本页 OCR” button.
- [ ] Show OCR confidence summary above OCR text.
- [ ] Show stale AI result warning after OCR rerun.
- [ ] Run `node --check static/app.js`.

