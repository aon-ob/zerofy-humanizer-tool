import asyncio
from contextlib import asynccontextmanager
import json
import sys

# Fix Windows asyncio subprocess support for Playwright
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.browser_pool import cluster_manager, analyze_text_fast
from core.engine import stream_ablation_pipeline, stream_synonym_pipeline, run_parallel_candidate_search
from core.ollama_client import DEFAULT_MODEL, fetch_ollama_models, get_ollama_rewrites
from core.text_utils import create_docx_bytes, read_file_text, replace_segment_in_text


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await cluster_manager.close()


app = FastAPI(title="Zerofy - AI Score Reduction & Humanizer Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/api/ollama-models")
async def get_ollama_models_endpoint():
    return await fetch_ollama_models()


@app.post("/api/extract-text")
async def extract_text_endpoint(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        extracted_text = read_file_text(contents, file.filename)
        if not extracted_text:
            raise HTTPException(status_code=400, detail="Could not extract readable text from the file.")
        return {"text": extracted_text, "filename": file.filename}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/export-docx")
async def export_docx_endpoint(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text provided to export.")
    docx_bytes = create_docx_bytes(text)
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="zerofy_clean_output.docx"'},
    )


@app.post("/api/quick-scan")
async def quick_scan_endpoint(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")

    pages = await cluster_manager.ensure_workers(1)
    score, highlights = await analyze_text_fast(pages[0], text, use_cache=False)
    return {"score": score, "highlights": highlights, "total_highlights": len(highlights)}


@app.post("/api/reroll-sentence")
async def reroll_sentence_endpoint(request: Request):
    body = await request.json()
    target_sentence = body.get("target_sentence", "").strip()
    pruned_sentence = body.get("pruned_sentence", "").strip()
    removed_tokens = body.get("removed_tokens", [])
    current_clean_text = body.get("current_clean_text", "").strip()
    current_replacement = body.get("current_replacement", "").strip() or target_sentence
    model_name = body.get("model", DEFAULT_MODEL)
    concurrency = max(1, min(int(body.get("concurrency", 4)), 12))

    if not target_sentence:
        raise HTTPException(status_code=400, detail="Missing target sentence.")

    pages = await cluster_manager.ensure_workers(concurrency)
    candidates = await get_ollama_rewrites(
        target_sentence=target_sentence,
        pruned_sentence=pruned_sentence or target_sentence,
        flagged_tokens=removed_tokens,
        full_text=current_clean_text,
        model_name=model_name,
        retry_round=2,
    )

    if not candidates:
        return {"status": "failed", "candidates": [], "best": None}

    specs = [
        {
            "candidate_phrase": c,
            "modified_segment": c,
            "candidate_text": replace_segment_in_text(current_clean_text, current_replacement, c),
        }
        for c in candidates
    ]

    win = await run_parallel_candidate_search(
        pages=pages,
        candidates=specs,
        target_segment=target_sentence,
        baseline_score=0.0,
        require_zero_score=True,
    )

    if win and win.get("passed"):
        return {
            "status": "ok",
            "best": win["spec"]["candidate_phrase"],
            "new_score": win["score"],
            "all_candidates": candidates,
        }

    return {
        "status": "partial",
        "best": candidates[0],
        "new_score": win["score"] if win else None,
        "all_candidates": candidates,
    }


@app.post("/api/deflate")
async def deflate_endpoint(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    concurrency = max(1, min(int(body.get("concurrency", 4)), 16))

    async def event_stream():
        async for ev in stream_ablation_pipeline(
            document_text=text,
            concurrency=concurrency,
            cancel_check=request.is_disconnected,
        ):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/synonym-refine")
async def synonym_refine_endpoint(request: Request):
    body = await request.json()
    original_text = body.get("original_text", "").strip()
    clean_text = body.get("clean_text", "").strip()
    ablated_items = body.get("ablated_items", [])
    sentence_records = body.get("sentence_records", None)
    initial_score = float(body.get("initial_score", 0.0))
    concurrency = max(1, min(int(body.get("concurrency", 4)), 16))
    model_name = body.get("model", DEFAULT_MODEL)
    fallback_policy = body.get("fallback_policy", "keep_zero")

    async def event_stream():
        async for ev in stream_synonym_pipeline(
            original_text=original_text,
            clean_text=clean_text,
            ablated_items=ablated_items,
            sentence_records=sentence_records,
            initial_score=initial_score,
            concurrency=concurrency,
            model_name=model_name,
            fallback_policy=fallback_policy,
            cancel_check=request.is_disconnected,
        ):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/")
async def index():
    return FileResponse("templates/index.html")


import os

if __name__ == "__main__":
    # Defaults to 8000 on your local PC; uses cloud-provided port if deployed
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port, reload=False)
