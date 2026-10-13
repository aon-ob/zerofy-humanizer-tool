# ZeroGPT AI Humanizer

A local web application built with FastAPI, Playwright, and local LLMs (via Ollama) to automatically rewrite text and pass ZeroGPT detection.

## 📌 What It Does
AI detectors often give false positives because they rely heavily on statistical word patterns and repetitive sentence structures. I built this tool to test how these detectors work and see how easily they can be bypassed through automated token ablation and contextual rephrasing.

The tool uses a **two-stage pipeline**:
1. **Stage 1 (Ablation Pass):** Spawns parallel Playwright workers to scan the text in ZeroGPT and selectively remove flagged words to find the minimal subset needed to drop the AI score to 0%.
2. **Stage 2 (Ollama Rephrasing Pass):** Takes the flagged sections and uses a local LLM (`gemma4:e4b`) with full paragraph context to generate natural human rewrites. It continuously re-scans the text in ZeroGPT after each rewrite until the entire document stays clean.

---

## ✨ Features
- **Multi-Worker Cluster:** Runs concurrent Playwright browser sessions to speed up text checking.
- **Context-Aware Rewrites:** Feeds the entire paragraph context to Ollama so rewrites flow naturally instead of swapping isolated words.
- **Iterative Rescanning Loop:** Re-checks the document in real time after every rewrite to catch and fix any secondary paragraphs that get flagged.
- **Interactive Diff Dashboard:** Shows real-time score drops, green highlights for accepted rewrites, and amber flags for sentences that need manual review.
- **Live SSE Telemetry:** Uses FastAPI Server-Sent Events to stream terminal logs and step progress to the frontend UI.

---

## 🛠️ Tech Stack
- **Backend:** Python 3.10+, FastAPI, Uvicorn, Asyncio
- **Browser Automation:** Playwright (Chromium)
- **Local AI Engine:** Ollama (`gemma4:e4b`)
- **Frontend:** HTML5, TailwindCSS, JavaScript (ES6+), Server-Sent Events
- **File Processing:** `python-docx`, `pypdf`

---

## 🚀 Quickstart

### 1. Prerequisites
Make sure you have [Ollama](https://ollama.com/) installed and running locally with your model pulled:

```bash
ollama pull gemma4:e4b
```

### 2. Installation

```bash
# Clone repository
git clone [https://github.com/YOUR_GITHUB_USERNAME/zerogpt-ai-humanizer.git](https://github.com/YOUR_GITHUB_USERNAME/zerogpt-ai-humanizer.git)
cd zerogpt-ai-humanizer

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies & Playwright browser
pip install -r requirements.txt
playwright install chromium
```

### 3. Run the App

```bash
python app.py
```

Open your browser and navigate to **`http://127.0.0.1:8000`**.

---

## ⚠️ Disclaimer
This project was created for educational purposes, portfolio demonstration, and evaluating the technical limitations of automated AI detection tools.
