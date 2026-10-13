let cleanTextOutput = "";
let timerInterval = null;
let startTime = 0;
let lastAblationResult = null; // Stores results from Stage 1

const DEFAULT_MODEL = "gemma4:e4b";

async function checkOllamaModels() {
    const badge = document.getElementById("ollamaStatusBadge");
    const select = document.getElementById("ollamaModelSelect");

    try {
        const resp = await fetch("/api/ollama-models");
        const data = await resp.json();

        if (data.status === "ok" && data.models.length > 0) {
            if (badge) badge.innerHTML = `<span class="text-emerald-400 font-mono">Ollama: Online (${data.models.length} models)</span>`;
            if (select) {
                select.innerHTML = "";
                data.models.forEach(model => {
                    const opt = document.createElement("option");
                    opt.value = model;
                    opt.textContent = model;
                    opt.className = "bg-zinc-950 text-zinc-200";
                    if (model === DEFAULT_MODEL) opt.selected = true;
                    select.appendChild(opt);
                });
            }
        } else {
            if (badge) badge.innerHTML = `<span class="text-amber-400 font-mono">Ollama: Auto-starting daemon...</span>`;
        }
    } catch (e) {
        if (badge) badge.innerHTML = `<span class="text-zinc-500 font-mono">Ollama: Unreachable</span>`;
    }
}

function startTimer() {
    stopTimer();
    startTime = Date.now();
    const timerText = document.getElementById("timerText");
    if (timerText) timerText.textContent = "00:00";

    timerInterval = setInterval(() => {
        const elapsedMs = Date.now() - startTime;
        const totalSec = Math.floor(elapsedMs / 1000);
        const minutes = String(Math.floor(totalSec / 60)).padStart(2, '0');
        const seconds = String(totalSec % 60).padStart(2, '0');
        if (timerText) {
            timerText.textContent = `${minutes}:${seconds}`;
        }
    }, 1000);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function showDebug(msg) {
    console.log("[DEBUG]", msg);
    const box = document.getElementById("debugBox");
    if (box) {
        box.classList.remove("hidden");
        box.innerText = `[${new Date().toLocaleTimeString()}] ${msg}`;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const inputText = document.getElementById("inputText");
    const charCount = document.getElementById("charCount");

    if (inputText) {
        inputText.addEventListener("input", () => {
            const words = inputText.value.trim() ? inputText.value.trim().split(/\s+/).length : 0;
            charCount.textContent = `${inputText.value.length} characters · ${words} words`;
        });
    }

    checkOllamaModels();
    showDebug("Page loaded and ready.");
});

function resetWorkspace() {
    document.getElementById('inputText').value = '';
    document.getElementById('charCount').textContent = '0 characters · 0 words';
    lastAblationResult = null;
    
    const stage2Badge = document.getElementById("stage2Badge");
    if (stage2Badge) {
        stage2Badge.className = "px-2 py-0.5 rounded text-[10px] font-mono text-zinc-500 bg-obsidian-surface border border-obsidian-border opacity-50";
        stage2Badge.textContent = "Stage 2: Ollama Locked";
    }

    document.getElementById("synonymTriggerBanner")?.classList.add("hidden");
    document.getElementById("emptyState")?.classList.remove("hidden");
    document.getElementById("resultsPanel")?.classList.add("hidden");
}

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    showDebug(`Uploading file: ${file.name}...`);
    const formData = new FormData();
    formData.append("file", file);

    try {
        const response = await fetch("/api/extract-text", {
            method: "POST",
            body: formData
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || "Failed to parse file.");
        }

        const inputText = document.getElementById("inputText");
        inputText.value = data.text;
        const words = data.text.trim() ? data.text.trim().split(/\s+/).length : 0;
        document.getElementById("charCount").textContent = `${data.text.length} characters · ${words} words`;
        showDebug(`Successfully imported ${data.filename} (${data.text.length} chars).`);
    } catch (err) {
        alert("File Import Error: " + err.message);
        showDebug("Import Error: " + err.message);
    } finally {
        event.target.value = "";
    }
}

function logMessage(text, type = 'info') {
    const logsFeed = document.getElementById("logsFeed");
    if (!logsFeed) return;
    const div = document.createElement("div");
    let color = "text-zinc-400";
    if (type === 'success') color = "text-emerald-400 font-semibold";
    if (type === 'warn') color = "text-amber-400";
    if (type === 'error') color = "text-red-400 font-semibold";
    if (type === 'step') color = "text-indigo-300";

    div.className = `${color} flex items-start gap-2`;
    div.innerHTML = `<span class="opacity-40">></span> <span>${text}</span>`;
    logsFeed.appendChild(div);
    logsFeed.scrollTop = logsFeed.scrollHeight;
}

// Stage 1: Run Ablation Pass First
async function runAblationPass() {
    showDebug("runAblationPass() triggered!");

    const startBtn = document.getElementById("startBtn");
    const inputText = document.getElementById("inputText");
    const concurrencySelect = document.getElementById("concurrencySelect");
    const logsFeed = document.getElementById("logsFeed");
    const emptyState = document.getElementById("emptyState");
    const resultsPanel = document.getElementById("resultsPanel");

    const text = inputText.value.trim();
    const concurrency = parseInt(concurrencySelect?.value || "4", 10);

    if (!text) {
        alert("Please paste your text or import a file before starting Stage 1.");
        return;
    }

    startBtn.disabled = true;
    startBtn.classList.add("opacity-50");
    emptyState.classList.add("hidden");
    resultsPanel.classList.add("hidden");
    logsFeed.innerHTML = "";

    startTimer();
    logMessage(`Initiating STAGE 1: Ablation Deflation (${concurrency}x workers)...`, "info");

    try {
        const response = await fetch("/api/deflate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, concurrency })
        });

        if (!response.ok) {
            throw new Error(`HTTP Error ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let streamBuffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            streamBuffer += decoder.decode(value, { stream: true });
            const events = streamBuffer.split("\n\n");
            streamBuffer = events.pop();

            for (const eventStr of events) {
                const trimmed = eventStr.trim();
                if (trimmed.startsWith("data: ")) {
                    try {
                        const payload = JSON.parse(trimmed.replace("data: ", ""));
                        handleSSEEvent(payload, "ablation");
                    } catch (e) {
                        console.error("JSON parse error:", e, trimmed);
                    }
                }
            }
        }
    } catch (err) {
        showDebug("Fetch Error: " + err.message);
        logMessage("Error: " + err.message, "error");
    } finally {
        stopTimer();
        startBtn.disabled = false;
        startBtn.classList.remove("opacity-50");
    }
}

// Stage 2: Run Ollama Synonym Replacement Pass on Ablation Results ONLY
async function runSynonymPass() {
    if (!lastAblationResult || !lastAblationResult.modified_details || lastAblationResult.modified_details.length === 0) {
        alert("No AI words were found during Stage 1 ablation to replace.");
        return;
    }

    const runSynonymBtn = document.getElementById("runSynonymBtn");
    const concurrencySelect = document.getElementById("concurrencySelect");
    const ollamaModelSelect = document.getElementById("ollamaModelSelect");

    const concurrency = parseInt(concurrencySelect?.value || "4", 10);
    const ollamaModel = ollamaModelSelect?.value || DEFAULT_MODEL;

    runSynonymBtn.disabled = true;
    runSynonymBtn.classList.add("opacity-50");

    startTimer();
    logMessage(`Initiating STAGE 2: Ollama Contextual Rewrites using model '${ollamaModel}'...`, "info");

    try {
        const response = await fetch("/api/synonym-refine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                original_text: lastAblationResult.original_text,
                clean_text: lastAblationResult.clean_text,
                ablated_items: lastAblationResult.modified_details,
                concurrency: concurrency,
                model: ollamaModel
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP Error ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let streamBuffer = "";

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            streamBuffer += decoder.decode(value, { stream: true });
            const events = streamBuffer.split("\n\n");
            streamBuffer = events.pop();

            for (const eventStr of events) {
                const trimmed = eventStr.trim();
                if (trimmed.startsWith("data: ")) {
                    try {
                        const payload = JSON.parse(trimmed.replace("data: ", ""));
                        handleSSEEvent(payload, "synonym");
                    } catch (e) {
                        console.error("JSON parse error:", e, trimmed);
                    }
                }
            }
        }
    } catch (err) {
        showDebug("Fetch Error: " + err.message);
        logMessage("Error: " + err.message, "error");
    } finally {
        stopTimer();
        runSynonymBtn.disabled = false;
        runSynonymBtn.classList.remove("opacity-50");
    }
}

function handleSSEEvent(data, passType) {
    if (data.type === "status") {
        logMessage(data.msg, "info");
    } else if (data.type === "chunk_start") {
        logMessage(`[Chunk ${data.chunk_index}/${data.total_chunks}] Processing block (${data.chunk_length} chars)...`, "warn");
    } else if (data.type === "init") {
        document.getElementById("scoreBadge").textContent = `Score: ${data.initial_score}%`;
        logMessage(`Initial AI Score: ${data.initial_score}% | Segments Flagged: ${data.total_highlights}`, "warn");
    } else if (data.type === "step_start") {
        logMessage(`Step ${data.step}: Testing phrase "${data.segment.substring(0, 35)}..."`, "step");
    } else if (data.type === "word_removed") {
        document.getElementById("scoreBadge").textContent = `Score: ${data.new_score}%`;
        logMessage(`SUCCESS: Replaced/Deflated ${data.removed} -> Score: ${data.new_score}%`, "success");
    } else if (data.type === "segment_removed") {
        logMessage(`Segment deflated -> Score: ${data.new_score}%`, "warn");
    } else if (data.type === "error") {
        logMessage(`Engine Error: ${data.msg}`, "error");
    } else if (data.type === "complete") {
        const finalTime = document.getElementById("timerText")?.textContent || "00:00";
        logMessage(`Pass completed in ${finalTime}! Rendering workstation results...`, "success");

        if (passType === "ablation") {
            lastAblationResult = data;
            
            const stage2Badge = document.getElementById("stage2Badge");
            if (stage2Badge) {
                stage2Badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
                stage2Badge.textContent = "Stage 2: Ollama Unlocked";
            }
            
            document.getElementById("flaggedCountSpan").textContent = data.removed_words.length;
            if (data.removed_words.length > 0) {
                document.getElementById("synonymTriggerBanner")?.classList.remove("hidden");
            }
        }

        renderResults(data);
    }
}

function renderResults(data) {
    const resultsPanel = document.getElementById("resultsPanel");

    document.getElementById("resInitialScore").textContent = `${data.initial_score}%`;
    document.getElementById("resFinalScore").textContent = `${data.final_score}%`;
    document.getElementById("resWordCount").textContent = data.removed_words.length;

    document.getElementById("highlightedContainer").innerHTML = data.highlighted_html;
    document.getElementById("cleanTextContainer").textContent = data.clean_text;
    cleanTextOutput = data.clean_text;

    const chipsContainer = document.getElementById("removedChips");
    chipsContainer.innerHTML = "";
    if (data.modified_details && data.modified_details.length > 0) {
        data.modified_details.forEach((item, idx) => {
            const span = document.createElement("span");
            if (item.status === "replaced") {
                span.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-950/60 border border-emerald-800 text-xs text-emerald-300 font-mono";
                span.innerHTML = `<i class="fa-solid fa-check text-[10px] text-emerald-400"></i> "${item.original}" → "${item.replacement}"`;
            } else if (item.status === "manual") {
                span.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-amber-950/60 border border-amber-800 text-xs text-amber-300 font-mono";
                span.innerHTML = `<i class="fa-solid fa-triangle-exclamation text-[10px] text-amber-400"></i> "${item.original}" (Manual Review Required)`;
            } else {
                span.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-obsidian-base border border-obsidian-border text-xs text-zinc-300 font-mono";
                span.innerHTML = `<span class="w-1.5 h-1.5 rounded-full bg-zinc-400"></span> "${item.original}"`;
            }
            chipsContainer.appendChild(span);
        });
    } else {
        chipsContainer.innerHTML = `<span class="text-xs text-zinc-500">No phrase modifications were required.</span>`;
    }

    resultsPanel.classList.remove("hidden");
    resultsPanel.classList.add("flex");
}

function switchTab(tab) {
    const reviewBtn = document.getElementById("tabReviewBtn");
    const cleanBtn = document.getElementById("tabCleanBtn");
    const tabReview = document.getElementById("tabReview");
    const tabClean = document.getElementById("tabClean");

    if (tab === 'review') {
        reviewBtn.className = "px-3 py-1.5 rounded-md text-xs font-medium bg-zinc-100 text-black shadow-sm transition-all flex items-center gap-2";
        cleanBtn.className = "px-3 py-1.5 rounded-md text-xs font-medium text-zinc-400 hover:text-white transition-all flex items-center gap-2";
        tabReview.classList.remove("hidden");
        tabClean.classList.add("hidden");
    } else {
        cleanBtn.className = "px-3 py-1.5 rounded-md text-xs font-medium bg-zinc-100 text-black shadow-sm transition-all flex items-center gap-2";
        reviewBtn.className = "px-3 py-1.5 rounded-md text-xs font-medium text-zinc-400 hover:text-white transition-all flex items-center gap-2";
        tabClean.classList.remove("hidden");
        tabReview.classList.add("hidden");
    }
}

function copyCleanText() {
    navigator.clipboard.writeText(cleanTextOutput).then(() => {
        alert("Clean 0% AI text copied to clipboard!");
    });
}

// Attach functions explicitly to window scope
window.runAblationPass = runAblationPass;
window.runSynonymPass = runSynonymPass;
window.resetWorkspace = resetWorkspace;
window.handleFileUpload = handleFileUpload;
window.switchTab = switchTab;
window.copyCleanText = copyCleanText;