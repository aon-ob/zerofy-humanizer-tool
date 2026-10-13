import asyncio
import io
import json
import queue
import re
import subprocess
import threading
from typing import AsyncGenerator
import docx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.async_api import async_playwright
import pypdf
import uvicorn
import httpx

app = FastAPI(title="Zerofy AI Score Reduction Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

TARGET_URL = "https://www.zerogpt.com"
OLLAMA_API_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:e4b"
MAX_CHUNK_CHARACTERS = 13500


async def ensure_ollama_running() -> bool:
    """Checks if Ollama API is active. If offline, automatically spawns 'ollama serve' in background."""
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{OLLAMA_API_URL}/api/tags")
            if resp.status_code == 200:
                return True
    except Exception:
        pass

    print("[SERVER TERMINAL] Ollama instance offline. Spawning background process ('ollama serve')...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        for _ in range(10):
            await asyncio.sleep(1.0)
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get(f"{OLLAMA_API_URL}/api/tags")
                    if resp.status_code == 200:
                        print("[SERVER TERMINAL] Ollama daemon successfully started and listening.")
                        return True
            except Exception:
                continue
    except Exception as e:
        print(f"[SERVER TERMINAL] Could not auto-start Ollama process: {e}")

    return False


def split_text_into_chunks(text: str, max_size: int = MAX_CHUNK_CHARACTERS) -> list[str]:
    if len(text) <= max_size:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_length = 0

    for paragraph in paragraphs:
        p_len = len(paragraph) + 2

        if p_len > max_size:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            for sentence in sentences:
                s_len = len(sentence) + 1
                if current_length + s_len > max_size and current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = []
                    current_length = 0
                current_chunk.append(sentence)
                current_length += s_len
        else:
            if current_length + p_len > max_size and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            current_chunk.append(paragraph)
            current_length += p_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return [c.strip() for c in chunks if c.strip()]


def read_file_text(file_bytes: bytes, filename: str) -> str:
    ext = filename.lower().split(".")[-1]
    text = ""

    if ext == "txt":
        text = file_bytes.decode("utf-8", errors="ignore")
    elif ext == "docx":
        doc = docx.Document(io.BytesIO(file_bytes))
        text = "\n\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    elif ext == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        text = "\n\n".join([pt.strip() for pt in pages_text if pt.strip()])
    else:
        raise ValueError("Unsupported format. Only .txt, .docx, and .pdf files are supported.")

    return text.strip()


def extract_enclosing_sentence(text: str, target: str) -> str:
    """Finds the complete sentence containing target in text to ensure phrase-level context."""
    if not target or target not in text:
        return target

    paragraphs = text.split("\n\n")
    for p in paragraphs:
        if target in p:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                if target in s:
                    return s.strip()
    return target


async def extract_ai_score_sync(page) -> float | None:
    try:
        await page.wait_for_selector(".result-card", timeout=25000, state="visible")
        await asyncio.sleep(2.2)
        result_text = await page.inner_text(".result-card")

        match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:AI|GPT)", result_text, re.IGNORECASE)
        if not match:
            match = re.search(r"(\d+(?:\.\d+)?)%", result_text)

        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


async def analyze_text_sync(page, text: str):
    try:
        await page.fill("#textArea", "")
        await page.fill("#textArea", text)
        await page.click(".scoreButton")

        score = None
        retries = 0
        while score is None and retries < 5:
            score = await extract_ai_score_sync(page)
            if score is None:
                retries += 1
                await asyncio.sleep(1.0)

        if score is None:
            score = 100.0

        highlighted_elements = await page.locator("mark.highlight").all_text_contents()
        highlights = []
        for h in highlighted_elements:
            cleaned = re.sub(r"\s+", " ", h).strip()
            if cleaned and cleaned not in highlights:
                highlights.append(cleaned)

        return score, highlights
    except Exception as e:
        print(f"[SERVER TERMINAL] Page analysis error: {e}")
        return 100.0, []


def replace_segment_in_text(full_text: str, target: str, replacement: str) -> str:
    """Replaces ONLY the single exact target occurrence using word boundaries and count=1."""
    if not target or target not in full_text:
        return full_text

    pattern = r"\b" + re.escape(target) + r"\b" if len(target) < 15 else re.escape(target)
    try:
        new_text, count = re.subn(pattern, replacement, full_text, count=1)
        if count > 0:
            return new_text
    except Exception:
        pass

    return full_text.replace(target, replacement, 1)


def generate_highlighted_html(original_text: str, modified_phrases: list[dict]) -> str:
    """Generates visual diff HTML, replacing ONLY 1 match per logged item to avoid duplicate highlights."""
    html = original_text
    html = html.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    for item in modified_phrases:
        phrase = item.get("original", "")
        replacement_text = item.get("replacement", "")
        status = item.get("status", "ablated")

        if not phrase.strip():
            continue

        escaped_phrase = phrase.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        escaped_repl = replacement_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        pattern = r"\b" + re.escape(escaped_phrase) + r"\b" if len(escaped_phrase) < 15 else re.escape(escaped_phrase)

        if status == "replaced" and replacement_text:
            replacement_html = (
                f'<mark class="bg-emerald-950/80 text-emerald-300 px-1 py-0.5 rounded border border-emerald-700/80 font-medium" '
                f'title="Replaced with Ollama sentence rewrite">{escaped_repl}</mark>'
            )
        elif status == "manual":
            replacement_html = (
                f'<mark class="bg-amber-950/80 text-amber-300 px-1.5 py-0.5 rounded border border-amber-600/80 font-medium cursor-pointer" '
                f'title="⚠️ Manual Review Required: No automated rewrite passed 0% AI threshold. Original restored.">{escaped_phrase}</mark>'
            )
        else:
            replacement_html = (
                f'<mark class="bg-zinc-800/80 text-zinc-300 px-1.5 py-0.5 rounded border border-zinc-700/80 line-through decoration-zinc-400 mx-0.5" '
                f'title="Ablated token">{escaped_phrase}</mark>'
            )

        try:
            html = re.sub(pattern, replacement_html, html, count=1)
        except Exception:
            html = html.replace(escaped_phrase, replacement_html, 1)

    paragraphs = html.split("\n\n")
    formatted_paragraphs = []
    for p in paragraphs:
        if p.strip():
            p_clean = p.replace("\n", "<br>")
            formatted_paragraphs.append(f"<p class='mb-4 leading-relaxed'>{p_clean}</p>")

    return "".join(formatted_paragraphs)


async def get_ollama_rewrites(target_sentence: str, full_text: str, model_name: str) -> list[str]:
    """Generates 5 highly diverse, creative sentence-level rewrites using full paragraph context."""
    await ensure_ollama_running()

    paragraphs = full_text.split("\n\n")
    context_paragraph = full_text
    for p in paragraphs:
        if target_sentence in p:
            context_paragraph = p
            break

    prompt = (
        f'Paragraph Context:\n"{context_paragraph}"\n\n'
        f'Target sentence/phrase to rewrite: "{target_sentence}"\n\n'
        f'Generate 5 HIGHLY DIVERSE human rewrites for "{target_sentence}". Do not just swap words for basic synonyms. Vary the sentence structure radically across candidates:\n'
        f'1. Structural transformation (e.g., convert questions like "What is X?" into "Definition of X:" or "Understanding X:").\n'
        f'2. Concise declarative statement.\n'
        f'3. Conversational introductory phrasing.\n'
        f'4. Re-framed clause structure (inverted or active voice shift).\n'
        f'5. Formal or academic alternative.\n\n'
        f'Rules:\n'
        f'1. Preserve underlying meaning within the paragraph context.\n'
        f'2. Output ONLY a valid raw JSON array of 5 strings, e.g. ["option 1", "option 2", "option 3", "option 4", "option 5"].\n'
        f'3. Do NOT include markdown formatting or explanations.'
    )

    try:
        async with httpx.AsyncClient(timeout=18.0) as client:
            resp = await client.post(
                f"{OLLAMA_API_URL}/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.85,
                        "top_p": 0.95
                    }
                }
            )
            if resp.status_code == 200:
                raw_response = resp.json().get("response", "").strip()
                raw_response = re.sub(r"^```json\s*", "", raw_response, flags=re.MULTILINE)
                raw_response = re.sub(r"^```\s*", "", raw_response, flags=re.MULTILINE)
                raw_response = raw_response.strip()

                parsed = json.loads(raw_response)
                if isinstance(parsed, list):
                    return [str(s).strip() for s in parsed if str(s).strip()]
    except Exception as e:
        print(f"[OLLAMA ERROR] Failed to fetch contextual rewrites: {e}")

    return []


async def async_ablation_process(document_text: str, concurrency: int, event_queue: queue.Queue):
    """Stage 1: Identifies AI-flagged words/phrases and ablates them down to 0% AI score."""
    def put_event(event_type: str, data: dict):
        event_queue.put({"type": event_type, **data})

    modified_phrases_log = []
    clean_chunks = []
    chunk_initial_scores = []
    chunk_weights = []

    chunks = split_text_into_chunks(document_text, MAX_CHUNK_CHARACTERS)
    total_chunks = len(chunks)

    try:
        if total_chunks > 1:
            put_event("status", {
                "msg": f"Text length: {len(document_text)} chars. Split into {total_chunks} chunk(s)."
            })

        put_event("status", {"msg": f"Spinning up {concurrency}x parallel Playwright workers [STAGE 1: ABLATION]..."})

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            pages = [await context.new_page() for _ in range(concurrency)]

            put_event("status", {"msg": f"Connecting {concurrency} worker(s) to ZeroGPT..."})
            await asyncio.gather(*[page.goto(TARGET_URL) for page in pages])

            for chunk_idx, chunk_text in enumerate(chunks, 1):
                put_event("chunk_start", {
                    "chunk_index": chunk_idx,
                    "total_chunks": total_chunks,
                    "chunk_length": len(chunk_text)
                })

                current_text = chunk_text
                score, highlights = await analyze_text_sync(pages[0], current_text)

                chunk_initial_scores.append(score)
                chunk_weights.append(len(chunk_text))

                put_event("init", {
                    "initial_score": score,
                    "total_highlights": len(highlights),
                    "chunk_index": chunk_idx,
                    "total_chunks": total_chunks
                })

                step = 1

                while score > 0 and highlights:
                    target_segment = highlights[0]
                    segment_resolved = False

                    put_event("step_start", {
                        "step": step,
                        "current_score": score,
                        "segment": target_segment,
                        "chunk_index": chunk_idx,
                        "total_chunks": total_chunks
                    })

                    words = target_segment.split()

                    for k in range(1, len(words) + 1):
                        if segment_resolved:
                            break

                        task_queue = asyncio.Queue()
                        for i in range(len(words) - k + 1):
                            removed_chunk = words[i : i + k]
                            removed_str = " ".join(removed_chunk)

                            modified_words = words[:i] + words[i + k :]
                            modified_segment = " ".join(modified_words)

                            candidate_text = replace_segment_in_text(current_text, target_segment, modified_segment)
                            candidate_text = re.sub(r" +", " ", candidate_text).strip()

                            task_queue.put_nowait({
                                "removed_str": removed_str,
                                "modified_segment": modified_segment,
                                "candidate_text": candidate_text
                            })

                        solution_found = asyncio.Event()
                        step_result = {}

                        async def ablation_worker(worker_page):
                            while not task_queue.empty() and not solution_found.is_set():
                                try:
                                    spec = task_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    break

                                new_score, new_highlights = await analyze_text_sync(worker_page, spec["candidate_text"])

                                if solution_found.is_set():
                                    task_queue.task_done()
                                    break

                                is_still_flagged = any(
                                    (spec["modified_segment"] and spec["modified_segment"] in h) or (target_segment in h)
                                    for h in new_highlights
                                )

                                if not is_still_flagged or new_score == 0.0:
                                    if not solution_found.is_set():
                                        solution_found.set()
                                        step_result["spec"] = spec
                                        step_result["score"] = new_score
                                        step_result["highlights"] = new_highlights

                                task_queue.task_done()

                        await asyncio.gather(*[ablation_worker(p) for p in pages])

                        if solution_found.is_set() and "spec" in step_result:
                            spec = step_result["spec"]
                            modified_phrases_log.append({
                                "original": spec["removed_str"],
                                "replacement": "",
                                "status": "ablated"
                            })
                            current_text = spec["candidate_text"]
                            score = step_result["score"]
                            highlights = step_result["highlights"]
                            segment_resolved = True

                            put_event("word_removed", {
                                "removed": spec["removed_str"],
                                "new_score": score,
                                "remaining_segments": len(highlights),
                                "chunk_index": chunk_idx,
                                "total_chunks": total_chunks
                            })
                            break

                    step += 1

                    if not segment_resolved and target_segment in current_text:
                        modified_phrases_log.append({
                            "original": target_segment,
                            "replacement": "",
                            "status": "ablated"
                        })
                        current_text = replace_segment_in_text(current_text, target_segment, "")
                        current_text = re.sub(r" +", " ", current_text).strip()
                        score, highlights = await analyze_text_sync(pages[0], current_text)

                        put_event("segment_removed", {
                            "removed": target_segment,
                            "new_score": score,
                            "remaining_segments": len(highlights),
                            "chunk_index": chunk_idx,
                            "total_chunks": total_chunks
                        })

                clean_chunks.append(current_text)

            await browser.close()

        final_clean_text = "\n\n".join(clean_chunks)
        total_weight = sum(chunk_weights) or 1
        overall_initial_score = round(
            sum(s * w for s, w in zip(chunk_initial_scores, chunk_weights)) / total_weight, 1
        )

        highlighted_html = generate_highlighted_html(document_text, modified_phrases_log)

        put_event("complete", {
            "initial_score": overall_initial_score,
            "final_score": 0.0,
            "removed_words": [m["original"] for m in modified_phrases_log],
            "modified_details": modified_phrases_log,
            "original_text": document_text,
            "highlighted_html": highlighted_html,
            "clean_text": final_clean_text,
            "total_chunks": total_chunks
        })
    except Exception as e:
        put_event("error", {"msg": f"Engine Error: {str(e)}"})
    finally:
        put_event("DONE", {})


async def async_synonym_refine_process(
    original_text: str,
    clean_text: str,
    ablated_items: list[dict],
    concurrency: int,
    model_name: str,
    event_queue: queue.Queue
):
    """Stage 2: Iterative convergence loop. Re-analyzes the full text in ZeroGPT after every rewrite to catch newly flagged paragraphs."""
    def put_event(event_type: str, data: dict):
        event_queue.put({"type": event_type, **data})

    final_modified_log = []
    current_working_text = original_text
    processed_sentences = set()
    MAX_CONVERGENCE_LOOPS = 10
    iteration = 0

    try:
        put_event("status", {"msg": f"Starting Stage 2: Iterative Convergence Loop (Model: {model_name})..."})
        put_event("status", {"msg": f"Workers: {concurrency}x | Max Loop Passes: {MAX_CONVERGENCE_LOOPS}"})

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            pages = [await context.new_page() for _ in range(concurrency)]

            await asyncio.gather(*[page.goto(TARGET_URL) for page in pages])

            # Initial scan of current text to establish starting baseline highlights
            score, highlights = await analyze_text_sync(pages[0], current_working_text)
            put_event("init", {
                "initial_score": score,
                "total_highlights": len(highlights),
                "chunk_index": 1,
                "total_chunks": 1
            })

            # Convergence loop: continues until score is 0.0% or no new highlights remain
            while score > 0 and highlights and iteration < MAX_CONVERGENCE_LOOPS:
                iteration += 1
                target_token = highlights[0]
                target_sentence = extract_enclosing_sentence(current_working_text, target_token)

                # Avoid infinite loops if a specific sentence cannot bypass detection
                if target_sentence in processed_sentences:
                    put_event("status", {
                        "msg": f"⚠️ Sentence '{target_sentence[:30]}...' previously failed rewrites. Flagging for manual review."
                    })
                    final_modified_log.append({
                        "original": target_sentence,
                        "replacement": target_sentence,
                        "status": "manual"
                    })
                    # Temporarily substitute ablated version to unblock the scan loop
                    clean_fallback = extract_enclosing_sentence(clean_text, target_token) or ""
                    current_working_text = replace_segment_in_text(current_working_text, target_sentence, clean_fallback)
                    current_working_text = re.sub(r" +", " ", current_working_text).strip()
                    
                    # Re-scan to refresh highlights
                    score, highlights = await analyze_text_sync(pages[0], current_working_text)
                    continue

                processed_sentences.add(target_sentence)

                put_event("step_start", {
                    "step": iteration,
                    "current_score": score,
                    "segment": target_sentence,
                    "chunk_index": 1,
                    "total_chunks": 1
                })

                put_event("status", {
                    "msg": f"[Loop {iteration}] Querying Ollama ({model_name}) for rewrite: '{target_sentence[:45]}...'"
                })

                candidates = await get_ollama_rewrites(target_sentence, current_working_text, model_name)
                rewrite_accepted = False

                if candidates:
                    put_event("status", {"msg": f"Evaluating {len(candidates)} candidate rewrite(s) in ZeroGPT..."})

                    task_queue = asyncio.Queue()
                    for cand in candidates:
                        cand_text = replace_segment_in_text(current_working_text, target_sentence, cand)
                        cand_text = re.sub(r" +", " ", cand_text).strip()
                        task_queue.put_nowait({"candidate_phrase": cand, "candidate_text": cand_text})

                    solution_found = asyncio.Event()
                    step_result = {}

                    async def synonym_worker(worker_page):
                        while not task_queue.empty() and not solution_found.is_set():
                            try:
                                spec = task_queue.get_nowait()
                            except asyncio.QueueEmpty:
                                break

                            new_score, new_highlights = await analyze_text_sync(worker_page, spec["candidate_text"])

                            if solution_found.is_set():
                                task_queue.task_done()
                                break

                            is_flagged = any(
                                (spec["candidate_phrase"] and spec["candidate_phrase"] in h) or (target_sentence in h)
                                for h in new_highlights
                            )

                            if not is_flagged or new_score < score or new_score == 0.0:
                                if not solution_found.is_set():
                                    solution_found.set()
                                    step_result["candidate_phrase"] = spec["candidate_phrase"]
                                    step_result["candidate_text"] = spec["candidate_text"]
                                    step_result["score"] = new_score
                                    step_result["highlights"] = new_highlights

                            task_queue.task_done()

                    await asyncio.gather(*[synonym_worker(p) for p in pages])

                    if solution_found.is_set() and "candidate_phrase" in step_result:
                        accepted_rewrite = step_result["candidate_phrase"]
                        rewrite_accepted = True
                        current_working_text = step_result["candidate_text"]
                        
                        # Dynamically update score and highlights from the ZeroGPT worker result
                        score = step_result["score"]
                        highlights = step_result["highlights"]

                        final_modified_log.append({
                            "original": target_sentence,
                            "replacement": accepted_rewrite,
                            "status": "replaced"
                        })
                        
                        put_event("word_removed", {
                            "removed": f'"{target_sentence[:30]}..." → "{accepted_rewrite[:30]}..."',
                            "new_score": score,
                            "remaining_segments": len(highlights),
                            "chunk_index": 1,
                            "total_chunks": 1
                        })
                        
                        put_event("status", {
                            "msg": f"🔄 Document updated! Re-scanned ZeroGPT score: {score}% | Active Flags: {len(highlights)}"
                        })

                if not rewrite_accepted:
                    final_modified_log.append({
                        "original": target_sentence,
                        "replacement": target_sentence,
                        "status": "manual"
                    })
                    put_event("status", {
                        "msg": f"⚠️ No rewrite passed 0% AI threshold for '{target_sentence[:30]}...'. Restoring original and re-evaluating..."
                    })
                    
                    # Perform a full re-scan to refresh highlights for remaining sections
                    score, highlights = await analyze_text_sync(pages[0], current_working_text)

            await browser.close()

        highlighted_html = generate_highlighted_html(original_text, final_modified_log)

        put_event("complete", {
            "initial_score": 0.0,
            "final_score": score,
            "removed_words": [m["original"] for m in final_modified_log],
            "modified_details": final_modified_log,
            "original_text": original_text,
            "highlighted_html": highlighted_html,
            "clean_text": current_working_text,
            "total_chunks": 1
        })

    except Exception as e:
        put_event("error", {"msg": f"Synonym Engine Error: {str(e)}"})
    finally:
        put_event("DONE", {})

def thread_ablation_runner(document_text: str, concurrency: int, event_queue: queue.Queue):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_ablation_process(document_text, concurrency, event_queue))
    loop.close()


def thread_synonym_runner(original_text: str, clean_text: str, ablated_items: list[dict], concurrency: int, model_name: str, event_queue: queue.Queue):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(async_synonym_refine_process(original_text, clean_text, ablated_items, concurrency, model_name, event_queue))
    loop.close()


async def sse_ablation_generator(document_text: str, concurrency: int) -> AsyncGenerator[str, None]:
    event_queue = queue.Queue()
    thread = threading.Thread(target=thread_ablation_runner, args=(document_text, concurrency, event_queue), daemon=True)
    thread.start()

    while True:
        try:
            data = await asyncio.to_thread(event_queue.get, timeout=0.5)
            if data.get("type") == "DONE":
                break
            yield f"data: {json.dumps(data)}\n\n"
        except queue.Empty:
            await asyncio.sleep(0.1)


async def sse_synonym_generator(original_text: str, clean_text: str, ablated_items: list[dict], concurrency: int, model_name: str) -> AsyncGenerator[str, None]:
    event_queue = queue.Queue()
    thread = threading.Thread(target=thread_synonym_runner, args=(original_text, clean_text, ablated_items, concurrency, model_name, event_queue), daemon=True)
    thread.start()

    while True:
        try:
            data = await asyncio.to_thread(event_queue.get, timeout=0.5)
            if data.get("type") == "DONE":
                break
            yield f"data: {json.dumps(data)}\n\n"
        except queue.Empty:
            await asyncio.sleep(0.1)


@app.get("/api/ollama-models")
async def get_ollama_models():
    await ensure_ollama_running()
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_API_URL}/api/tags")
            if resp.status_code == 200:
                models = [m["name"] for m in resp.json().get("models", [])]
                if DEFAULT_MODEL not in models:
                    models.insert(0, DEFAULT_MODEL)
                return {"status": "ok", "models": models}
    except Exception:
        pass
    return {"status": "offline", "models": [DEFAULT_MODEL]}


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


@app.post("/api/deflate")
async def deflate_endpoint(request: Request):
    print("\n" + "=" * 60)
    print("[SERVER TERMINAL] /api/deflate REQUEST RECEIVED (STAGE 1: ABLATION)!")
    print("=" * 60)
    body = await request.json()
    text = body.get("text", "").strip()
    concurrency = int(body.get("concurrency", 4))
    concurrency = max(1, min(concurrency, 16))

    print(f"[SERVER TERMINAL] Input Length: {len(text)} chars | Workers: {concurrency}x")
    return StreamingResponse(sse_ablation_generator(text, concurrency), media_type="text/event-stream")


@app.post("/api/synonym-refine")
async def synonym_refine_endpoint(request: Request):
    print("\n" + "=" * 60)
    print("[SERVER TERMINAL] /api/synonym-refine REQUEST RECEIVED (STAGE 2: OLLAMA)!")
    print("=" * 60)
    body = await request.json()
    original_text = body.get("original_text", "").strip()
    clean_text = body.get("clean_text", "").strip()
    ablated_items = body.get("ablated_items", [])
    concurrency = int(body.get("concurrency", 4))
    concurrency = max(1, min(concurrency, 16))
    model_name = body.get("model", DEFAULT_MODEL)

    print(f"[SERVER TERMINAL] Replacing {len(ablated_items)} items | Model: {model_name} | Workers: {concurrency}x")
    return StreamingResponse(sse_synonym_generator(original_text, clean_text, ablated_items, concurrency, model_name), media_type="text/event-stream")


@app.get("/")
async def index():
    return FileResponse("templates/index.html")


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)