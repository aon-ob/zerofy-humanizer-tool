# ZeroGPT AI Humanizer

A real-time NLP benchmarking workspace designed to evaluate the susceptibility of proprietary statistical AI detectors (e.g., ZeroGPT) to automated token ablation and local LLM sentence-level rephrasing.

---

## 🔬 Research & System Overview

Commercial AI detectors rely on statistical perplexity and burstiness metrics to classify human versus machine-generated text. This project explores the structural vulnerabilities of these classifiers by implementing an automated **Two-Stage Adversarial Convergence Pipeline**:

1. **Stage 1 (Token-Level Ablation Pass):** Identifies specific AI-flagged words and clauses, systematically ablating minimum token subsets using a parallel Playwright cluster until the overall document detection score reaches `0.0%`.
2. **Stage 2 (Contextual Ollama Re-Insertion & Convergence Loop):** Extracts full enclosing sentences for flagged tokens, queries a local LLM (`Ollama` / `gemma4:e4b`) with paragraph-level context for high-variance structural rewrites, and re-analyzes the live document in ZeroGPT until score convergence is achieved. Failed rewrites are restored and flagged for manual review.

---

## 🛠 Tech Stack

- **Backend Framework:** Asynchronous FastAPI (Python)
- **Browser Automation:** Playwright Chromium cluster (multi-worker parallel evaluation)
- **Real-Time Telemetry:** Server-Sent Events (SSE) streaming pipeline
- **Local LLM Inference:** Ollama API (`gemma4:e4b`) with custom temperature and top-p sampling
- **Front-End Workspace:** Tailored Diff & Analysis GUI (TailwindCSS, HTML5, Vanilla JS)

---

## 🏗 Key Architectural Features

- **Parallel Worker Pool:** Supports running up to 16 concurrent Playwright browser sessions to accelerate web interaction throughput.
- **Iterative ZeroGPT Convergence Loop:** Re-scans the full text state dynamically following every Ollama rewrite pass to detect and resolve secondary paragraph flags.
- **Context-Aware Prompt Engineering:** Feeds full surrounding paragraph context into local LLMs to force structural shifts (e.g., reformatting questions like *"What is X?"* into heading structures like *"Definition of X:"*).
- **Exact Single-Occurrence Match (`count=1`):** Employs strict regex word boundaries (`\b`) and bounded substitutions to prevent token ambiguity across repetitive words.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com/) running locally with the target model pulled:
  ```bash
  ollama pull gemma4:e4b
