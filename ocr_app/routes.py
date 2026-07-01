import json
import shutil
import threading
import time
import uuid
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_from_directory

from .config import (
    BASE_DIR,
    DEFAULT_KEYWORDS,
    DEFAULT_VLM_MODEL,
    MAX_WORKERS,
    OCR_INTRA_THREADS,
    OCR_INTER_THREADS,
    PAGE_DIR_NAME,
    TEXT_DIR_NAME,
    VLM_MODELS,
)
from .ocr import OcrService
from .storage import JobStore, get_keyword_history, parse_keywords, record_keyword_history, safe_name
from .vlm import VlmService

store = JobStore()
ocr_service = None
vlm_service = None


def get_services():
    global ocr_service, vlm_service
    if ocr_service is None:
        # VLM needs OCR rendering; OCR needs VLM scheduling. Wire after both exist.
        ocr_service = OcrService(store, lambda job_id, page_no: vlm_service.schedule(job_id, page_no))
        vlm_service = VlmService(store, ocr_service.render_page)
    return ocr_service, vlm_service


def create_app():
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "templates"),
        static_folder=str(BASE_DIR / "static"),
    )

    @app.get("/")
    def index():
        return render_template("index.html", default_keywords=DEFAULT_KEYWORDS, models=VLM_MODELS)

    @app.get("/api/jobs")
    def list_jobs():
        return jsonify({"jobs": store.list_jobs()})

    @app.get("/api/keyword-history")
    def keyword_history():
        return jsonify({"items": get_keyword_history()})

    @app.post("/api/jobs")
    def create_job():
        ocr, _ = get_services()
        upload = request.files.get("pdf")
        if upload is None or not upload.filename:
            return jsonify({"error": "请选择 PDF 文件"}), 400
        keywords = parse_keywords(request.form.get("keywords", DEFAULT_KEYWORDS))
        record_keyword_history(keywords)
        try:
            dpi = max(120, min(260, int(request.form.get("dpi", "180") or 180)))
            workers = max(1, min(MAX_WORKERS, int(request.form.get("workers", "6") or 6)))
            vlm_workers = max(1, min(MAX_WORKERS, int(request.form.get("vlm_workers", "2") or 2)))
            max_pages = max(0, int(request.form.get("max_pages", "0") or 0))
        except ValueError:
            return jsonify({"error": "DPI、OCR 并发、AI 并发和限页必须是数字"}), 400
        model = request.form.get("vlm_model") or DEFAULT_VLM_MODEL
        model = model if model in VLM_MODELS else DEFAULT_VLM_MODEL

        job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        paths = store.job_path(job_id)
        paths.mkdir(parents=True, exist_ok=True)
        pdf_path = paths / safe_name(upload.filename)
        upload.save(pdf_path)

        now = time.time()
        store.create(job_id, {
            "id": job_id,
            "filename": pdf_path.name,
            "_pdf_path": str(pdf_path),
            "_api_key": (request.form.get("api_key") or "").strip(),
            "status": "queued",
            "message": "任务已创建",
            "keywords": keywords,
            "page_range_raw": request.form.get("page_range", ""),
            "page_numbers": [],
            "dpi": dpi,
            "workers": workers,
            "vlm_workers": vlm_workers,
            "max_pages": max_pages,
            "auto_vlm_candidates": request.form.get("auto_vlm_candidates") == "on",
            "vlm_model": model,
            "ocr_intra_threads": OCR_INTRA_THREADS,
            "ocr_inter_threads": OCR_INTER_THREADS,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "finished_at": None,
            "total_pdf_pages": 0,
            "total_pages": 0,
            "done_pages": 0,
            "candidate_pages": [],
            "page_results": [],
            "vlm_results": {},
        })
        threading.Thread(target=ocr.process_job, args=(job_id, pdf_path, keywords, dpi, workers, max_pages), daemon=True).start()
        return jsonify({"job_id": job_id})

    @app.post("/api/jobs/<job_id>/stop")
    def stop_job(job_id):
        job = store.load(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        store.set_cancelled(job_id)
        if job.get("status") in {"queued", "running"}:
            store.update(job_id, status="stopping", message="正在停止，已开始页面会先完成")
        return jsonify({"ok": True})

    @app.get("/api/jobs/<job_id>")
    def get_job(job_id):
        job = store.load(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(store.public_job(job))

    @app.post("/api/jobs/<job_id>/vlm")
    def start_batch_vlm(job_id):
        _, vlm = get_services()
        job = store.load(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        payload = request.json or {}
        api_key = (payload.get("api_key") or "").strip()
        if api_key:
            with store.lock:
                store.jobs[job_id]["_api_key"] = api_key
        pages = []
        for page in payload.get("pages") or []:
            try:
                page_no = int(page)
            except (TypeError, ValueError):
                continue
            if page_no >= 1:
                pages.append(page_no)
        pages = sorted(set(pages))
        if not pages:
            return jsonify({"error": "请选择页面"}), 400
        results = []
        for page_no in pages:
            ok, message = vlm.schedule(job_id, page_no)
            results.append({"page": page_no, "ok": ok, "status": message})
        return jsonify({"ok": any(r["ok"] for r in results), "results": results})

    @app.post("/api/jobs/<job_id>/vlm/<int:page_no>")
    def start_vlm(job_id, page_no):
        _, vlm = get_services()
        if not store.load(job_id):
            return jsonify({"error": "任务不存在"}), 404
        api_key = (request.json or {}).get("api_key", "").strip()
        if api_key:
            with store.lock:
                store.jobs[job_id]["_api_key"] = api_key
        ok, message = vlm.schedule(job_id, page_no)
        return jsonify({"ok": ok, "status": message}), 200 if ok else 400

    @app.get("/api/jobs/<job_id>/vlm/<int:page_no>")
    def get_vlm(job_id, page_no):
        job = store.load(job_id)
        if not job:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify((job.get("vlm_results") or {}).get(str(page_no), {"status": "none", "message": "未识别"}))

    @app.get("/job/<job_id>/text/<int:page_no>")
    def get_page_text(job_id, page_no):
        text_path = store.job_path(job_id) / TEXT_DIR_NAME / f"page_{page_no:04d}.txt"
        if not text_path.exists():
            return jsonify({"error": "页面文本不存在"}), 404
        return jsonify({"page": page_no, "text": text_path.read_text(encoding="utf-8", errors="ignore")})

    @app.get("/job/<job_id>/image/<int:page_no>")
    def get_page_image(job_id, page_no):
        return send_from_directory(store.job_path(job_id) / PAGE_DIR_NAME, f"page_{page_no:04d}.png")

    @app.get("/job/<job_id>/full_text.txt")
    def get_full_text(job_id):
        return send_from_directory(store.job_path(job_id), "full_text.txt", as_attachment=True)

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "max_workers": MAX_WORKERS, "ocr_intra_threads": OCR_INTRA_THREADS, "default_vlm_model": DEFAULT_VLM_MODEL})

    return app


def run_smoke(pdf, pages, dpi, workers):
    ocr, _ = get_services()
    job_id = "smoke_" + uuid.uuid4().hex[:8]
    paths = store.job_path(job_id)
    paths.mkdir(parents=True, exist_ok=True)
    pdf_path = paths / safe_name(Path(pdf).name)
    shutil.copy2(pdf, pdf_path)
    now = time.time()
    keywords = parse_keywords(DEFAULT_KEYWORDS)
    store.create(job_id, {
        "id": job_id,
        "filename": pdf_path.name,
        "_pdf_path": str(pdf_path),
        "_api_key": "",
        "status": "queued",
        "message": "",
        "keywords": keywords,
        "page_range_raw": "",
        "page_numbers": [],
        "dpi": dpi,
        "workers": workers,
        "vlm_workers": 2,
        "max_pages": pages,
        "auto_vlm_candidates": False,
        "vlm_model": DEFAULT_VLM_MODEL,
        "ocr_intra_threads": OCR_INTRA_THREADS,
        "ocr_inter_threads": OCR_INTER_THREADS,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
        "total_pdf_pages": 0,
        "total_pages": 0,
        "done_pages": 0,
        "candidate_pages": [],
        "page_results": [],
        "vlm_results": {},
    })
    ocr.process_job(job_id, pdf_path, keywords, dpi, workers, pages)
    print(json.dumps(store.public_job(store.load(job_id)), ensure_ascii=False, indent=2))
