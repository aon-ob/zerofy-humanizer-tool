import asyncio
from collections import OrderedDict
import hashlib
import re
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

TARGET_URL = "https://www.zerogpt.com"


class LRUScoreCache:
    # In-memory SHA-256 cache of ZeroGPT analysis results so identical texts return in 0ms
    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self.cache: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()

    def _hash(self, text: str) -> str:
        norm = re.sub(r"\s+", " ", text).strip()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    def get(self, text: str) -> tuple[float, list[str]] | None:
        key = self._hash(text)
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        score, highlights = self.cache[key]
        return score, list(highlights)

    def put(self, text: str, score: float, highlights: list[str]):
        key = self._hash(text)
        self.cache[key] = (score, list(highlights))
        self.cache.move_to_end(key)
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)


class BrowserClusterManager:
    # Persistent Playwright Chromium cluster keeping warm ZeroGPT worker tabs alive across runs
    def __init__(self):
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._pages: list[Page] = []
        self._lock = asyncio.Lock()
        self.cache = LRUScoreCache(capacity=1024)

    async def ensure_workers(self, count: int, status_cb=None) -> list[Page]:
        # Returns count warm Playwright pages connected and fully hydrated on ZeroGPT
        async with self._lock:
            if self._playwright is None:
                if status_cb:
                    status_cb("Starting headless Chromium engine...")
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
                self._context = await self._browser.new_context()

            self._pages = [p for p in self._pages if not p.is_closed()]

            needed = count - len(self._pages)
            if needed > 0:
                if status_cb:
                    status_cb(f"Connecting {needed} worker tab(s) to ZeroGPT (Pool size: {count})...")
                new_pages = [await self._context.new_page() for _ in range(needed)]
                await asyncio.gather(*[p.goto(TARGET_URL, timeout=45000) for p in new_pages])
                await asyncio.gather(
                    *[p.wait_for_selector("#textArea", timeout=25000, state="visible") for p in new_pages]
                )
                self._pages.extend(new_pages)

            return self._pages[:count]

    async def close(self):
        async with self._lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None
            self._pages.clear()


cluster_manager = BrowserClusterManager()


async def extract_ai_score_sync(page: Page) -> float | None:
    try:
        await page.wait_for_selector(".result-card", timeout=25000, state="visible")
        await asyncio.sleep(2.0)
        result_text = await page.inner_text(".result-card")

        match = re.search(r"(\d+(?:\.\d+)?)%\s*(?:AI|GPT)", result_text, re.IGNORECASE)
        if not match:
            match = re.search(r"(\d+(?:\.\d+)?)%", result_text)

        if match:
            return float(match.group(1))
    except Exception:
        pass
    return None


async def analyze_text_fast(page: Page, text: str, use_cache: bool = True) -> tuple[float, list[str]]:
    # Submits text to ZeroGPT and reliably extracts (score, highlights) with LRU caching
    if not text or not text.strip():
        return 0.0, []

    if use_cache:
        cached = cluster_manager.cache.get(text)
        if cached is not None:
            return cached

    for attempt in range(2):
        try:
            await page.wait_for_selector("#textArea", timeout=15000, state="visible")
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

            if score is not None:
                highlighted_elements = await page.locator("mark.highlight").all_text_contents()
                highlights = []
                for h in highlighted_elements:
                    cleaned = re.sub(r"\s+", " ", h).strip()
                    if cleaned and cleaned not in highlights:
                        highlights.append(cleaned)

                cluster_manager.cache.put(text, score, highlights)
                return score, highlights

            print(f"[SERVER TERMINAL] Score card timeout on attempt {attempt + 1}, reloading ZeroGPT tab...")
            await page.goto(TARGET_URL, timeout=35000)
        except Exception as e:
            print(f"[SERVER TERMINAL] Page analysis error (attempt {attempt + 1}): {e}")
            try:
                await page.goto(TARGET_URL, timeout=35000)
            except Exception:
                pass

    return 100.0, []