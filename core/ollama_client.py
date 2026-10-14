import asyncio
import json
import re
import subprocess
import httpx
from core.text_utils import extract_enclosing_paragraph

OLLAMA_API_URL = "[http://127.0.0.1:11434](http://127.0.0.1:11434)"
DEFAULT_MODEL = "gemma4:e4b"


async def ensure_ollama_running() -> bool:
    # Checks if Ollama API is active; if offline, spawns 'ollama serve' in background
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
            start_new_session=True,
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


async def fetch_ollama_models() -> dict:
    # Returns installed Ollama models, ensuring DEFAULT_MODEL is listed
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


def _parse_json_string_list(raw_response: str) -> list[str]:
    # Extracts a JSON array of strings even if wrapped in markdown fences
    cleaned = raw_response.strip()
    cleaned = re.sub(r"^`{3}(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\s*`{3}$", "", cleaned, flags=re.MULTILINE).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return [str(s).strip() for s in parsed if str(s).strip()]
    except Exception:
        pass

    bracket_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
    if bracket_match:
        try:
            parsed = json.loads(bracket_match.group(0))
            if isinstance(parsed, list):
                return [str(s).strip() for s in parsed if str(s).strip()]
        except Exception:
            pass

    return []


async def get_ollama_rewrites(
    target_sentence: str,
    pruned_sentence: str,
    flagged_tokens: list[str],
    full_text: str,
    model_name: str,
    retry_round: int = 1,
    failed_candidates: list[str] | None = None,
) -> list[str]:
    # Generates 6 anti-detector human rewrites informed by the 0%-AI skeleton and banned trigger tokens
    await ensure_ollama_running()
    context_paragraph = extract_enclosing_paragraph(full_text, target_sentence)
    banned_str = ", ".join(f'"{t}"' for t in flagged_tokens if t) or "none"

    if retry_round == 1:
        prompt = (
            f'Paragraph Context:\n"{context_paragraph}"\n\n'
            f'Original AI-Flagged Sentence:\n"{target_sentence}"\n\n'
            f'Words that triggered the AI detector (DO NOT USE THESE): {banned_str}\n'
            f'0% AI Skeleton (passed detection when trigger words were removed):\n"{pruned_sentence}"\n\n'
            f'Task: Generate 6 distinct, natural human rewrites of the Original Sentence that fit smoothly into the Paragraph Context and will score 0% AI on ZeroGPT.\n'
            f'Strategies to use across the 6 options:\n'
            f'1. Minimal Bridge Repair: Take the 0% AI Skeleton ("{pruned_sentence}") and fix its grammar with 1-3 simple everyday words without re-introducing {banned_str}.\n'
            f'2. Structural Shift: If it is a question (e.g., "What is X?"), turn it into a heading or colon statement (e.g., "Definition of X:" or "Defining X -").\n'
            f'3. Direct Active Voice: Cut filler phrases ("it is important to", "can be described as", "is designed to") and state the point directly.\n'
            f'4. Conversational Student Tone: Write how an undergrad student naturally explains a concept in plain English.\n'
            f'5. Clause Inversion / Punctuation Burstiness: Use a dash, colon, or parentheses to break predictable LLM sentence rhythm.\n'
            f'6. Ultra-Concise Rewrite: Convey the exact meaning in 30% fewer words using concrete verbs.\n\n'
            f'Strict Rules:\n'
            f'- NEVER use the banned trigger words: {banned_str}.\n'
            f'- NEVER use AI cliches (delve, crucial, moreover, furthermore, comprehensive, imperative, facilitate, paradigm, utilize, orchestrate).\n'
            f'- Output ONLY a raw JSON array of 6 strings: ["option 1", "option 2", "option 3", "option 4", "option 5", "option 6"]'
        )
        temperature = 0.85
    else:
        failed_sample = "; ".join(f'"{c}"' for c in (failed_candidates or [])[:3])
        prompt = (
            f'Paragraph Context:\n"{context_paragraph}"\n\n'
            f'Target Sentence:\n"{target_sentence}"\n'
            f'0% AI Skeleton:\n"{pruned_sentence}"\n'
            f'Banned Trigger Words: {banned_str}\n'
            f'Previous rewrites that STILL failed AI detection: {failed_sample}\n\n'
            f'Why they failed: They used predictable sentence length and textbook phrasing. '
            f'Generate 6 NEW, radically different, bursty, plain-English rewrites. '
            f'Keep at least 2 options very close to the 0% AI Skeleton ("{pruned_sentence}") with only tiny grammatical fixes, '
            f'and make the other 4 options use casual, punchy phrasing with colons or dashes.\n'
            f'Output ONLY a raw JSON array of 6 strings: ["option 1", "option 2", "option 3", "option 4", "option 5", "option 6"]'
        )
        temperature = 0.95

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                f"{OLLAMA_API_URL}/api/generate",
                json={
                    "model": model_name,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "top_p": 0.95,
                    },
                },
            )
            if resp.status_code == 200:
                raw_response = resp.json().get("response", "")
                candidates = _parse_json_string_list(raw_response)
                valid = [c for c in candidates if c and c != target_sentence]
                return valid[:6]
    except Exception as e:
        print(f"[OLLAMA ERROR] Failed to fetch contextual rewrites: {e}")

    return []