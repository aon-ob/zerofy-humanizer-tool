let cleanTextOutput = "";
let timerInterval = null;
let startTime = 0;
let lastAblationResult = null;
let currentResultData = null;
let activeAbortController = null;
let activeInspectorIdx = null;

const DEFAULT_MODEL = "gemma4:e4b";

function showToast(message, type = "success") {
    const container = document.getElementById("toastContainer");
    if (!container) return;
    const toast = document.createElement("div");
    let borderCol = "border-emerald-600/80 bg-emerald-950/90 text-emerald-200";
    let icon = "fa-circle-check text-emerald-400";
    if (type === "warn") {
        borderCol = "border-amber-600/80 bg-amber-950/90 text-amber-200";
        icon = "fa-triangle-exclamation text-amber-400";
    } else if (type === "error") {
        borderCol = "border-rose-600/80 bg-rose-950/90 text-rose-200";
        icon = "fa-circle-exclamation text-rose-400";
    } else if (type === "info") {
        borderCol = "border-indigo-600/80 bg-indigo-950/90 text-indigo-200";
        icon = "fa-circle-info text-indigo-400";
    }
    toast.className = `pointer-events-auto flex items-center gap-2.5 px-4 py-2.5 rounded-lg border ${borderCol} text-xs font-medium shadow-xl transition-all duration-300`;
    toast.innerHTML = `<i class="fa-solid ${icon}"></i><span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 300);
    }, 3200);
}

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
            if (badge) badge.innerHTML = `<span class="text-amber-400 font-mono">Ollama: Standby</span>`;
        }
    } catch (e) {
        if (badge) badge.innerHTML = `<span class="text-zinc-500 font-mono">Ollama: Offline</span>`;
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
        const minutes = String(Math.floor(totalSec / 60)).padStart(2, "0");
        const seconds = String(totalSec % 60).padStart(2, "0");
        if (timerText) timerText.textContent = `${minutes}:${seconds}`;
    }, 500);
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function setProgress(pct) {
    const bar = document.getElementById("pipelineProgressBar");
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
}

function setJobRunningUI(isRunning) {
    const startBtn = document.getElementById("startBtn");
    const autoBtn = document.getElementById("autoPipelineBtn");
    const cancelBtn = document.getElementById("cancelBtn");
    const runSynonymBtn = document.getElementById("runSynonymBtn");

    if (startBtn) {
        startBtn.disabled = isRunning;
        startBtn.classList.toggle("opacity-50", isRunning);
    }
    if (autoBtn) {
        autoBtn.disabled = isRunning;
        autoBtn.classList.toggle("opacity-50", isRunning);
    }
    if (runSynonymBtn) {
        runSynonymBtn.disabled = isRunning;
        runSynonymBtn.classList.toggle("opacity-50", isRunning);
    }
    if (cancelBtn) {
        cancelBtn.classList.toggle("hidden", !isRunning);
        cancelBtn.classList.toggle("flex", isRunning);
    }
}

function cancelActiveJob() {
    if (activeAbortController) {
        activeAbortController.abort();
        activeAbortController = null;
        logMessage("Job cancelled by user.", "warn");
        showToast("Active job stopped.", "warn");
        stopTimer();
        setJobRunningUI(false);
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
});

function resetWorkspace() {
    cancelActiveJob();
    document.getElementById("inputText").value = "";
    document.getElementById("charCount").textContent = "0 characters · 0 words";
    lastAblationResult = null;
    currentResultData = null;
    setProgress(0);

    const stage2Badge = document.getElementById("stage2Badge");
    if (stage2Badge) {
        stage2Badge.className = "px-2 py-0.5 rounded text-[10px] font-mono text-zinc-500 bg-obsidian-surface border border-obsidian-border opacity-60";
        stage2Badge.textContent = "Stage 2: Zero-Baseline LLM";
    }

    closeDiffInspector();
    document.getElementById("synonymTriggerBanner")?.classList.add("hidden");
    document.getElementById("emptyState")?.classList.remove("hidden");
    document.getElementById("resultsPanel")?.classList.add("hidden");
    showToast("Workspace cleared.", "info");
}

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    try {
        showToast(`Importing ${file.name}...`, "info");
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
        showToast(`Imported ${data.filename} (${words} words)`, "success");
    } catch (err) {
        showToast("Import Error: " + err.message, "error");
    } finally {
        event.target.value = "";
    }
}

function logMessage(text, type = "info") {
    const logsFeed = document.getElementById("logsFeed");
    if (!logsFeed) return;
    const div = document.createElement("div");
    let color = "text-zinc-400";
    if (type === "success") color = "text-emerald-400 font-semibold";
    if (type === "warn") color = "text-amber-400";
    if (type === "error") color = "text-rose-400 font-semibold";
    if (type === "step") color = "text-indigo-300";

    div.className = `${color} flex items-start gap-2`;
    div.innerHTML = `<span class="opacity-40">></span> <span>${text}</span>`;
    logsFeed.appendChild(div);
    logsFeed.scrollTop = logsFeed.scrollHeight;
}

async function runQuickScanInput() {
    const text = document.getElementById("inputText")?.value.trim();
    if (!text) {
        showToast("Paste some text first to run a Quick Score Check.", "warn");
        return;
    }
    logMessage("Running Quick ZeroGPT Score Check...", "info");
    showToast("Scanning passage in ZeroGPT...", "info");
    try {
        const resp = await fetch("/api/quick-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Quick scan failed");
        document.getElementById("scoreBadge").textContent = `Score: ${data.score}%`;
        logMessage(`Quick Scan Result: ${data.score}% AI | ${data.total_highlights} segment(s) flagged.`, data.score === 0 ? "success" : "warn");
        showToast(`ZeroGPT Score: ${data.score}% AI (${data.total_highlights} flagged)`, data.score === 0 ? "success" : "warn");
    } catch (e) {
        showToast("Quick scan error: " + e.message, "error");
    }
}

async function verifyCleanTextScore() {
    const text = document.getElementById("cleanTextContainer")?.value.trim() || cleanTextOutput.trim();
    if (!text) return;
    const btn = document.getElementById("verifyCleanBtn");
    if (btn) btn.disabled = true;
    showToast("Verifying Clean Copy in ZeroGPT...", "info");
    logMessage("Verifying Clean Copy in ZeroGPT...", "info");
    try {
        const resp = await fetch("/api/quick-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Verification failed");
        document.getElementById("resFinalScore").textContent = `${data.score}%`;
        document.getElementById("scoreBadge").textContent = `Score: ${data.score}%`;
        if (data.score === 0) {
            showToast("Verified 0.0% AI in ZeroGPT!", "success");
            logMessage("Verified Clean Copy: 0.0% AI!", "success");
        } else {
            showToast(`Clean Copy scored ${data.score}% AI (${data.total_highlights} flagged)`, "warn");
            logMessage(`Verification Score: ${data.score}% AI | Flagged: ${data.highlights.join(" | ")}`, "warn");
        }
    } catch (e) {
        showToast("Verification error: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function runFullAutoPipeline() {
    const completedStage1 = await runAblationPass(true);
    if (completedStage1 && lastAblationResult && lastAblationResult.modified_details?.length > 0) {
        logMessage("Auto-chaining into Stage 2: Zero-Baseline Ollama Humanizer...", "step");
        await runSynonymPass();
    }
}

async function runAblationPass(isAutoChain = false) {
    const inputText = document.getElementById("inputText");
    const concurrencySelect = document.getElementById("concurrencySelect");
    const logsFeed = document.getElementById("logsFeed");
    const emptyState = document.getElementById("emptyState");
    const resultsPanel = document.getElementById("resultsPanel");

    const text = inputText.value.trim();
    const concurrency = parseInt(concurrencySelect?.value || "6", 10);

    if (!text) {
        showToast("Please paste your text or import a file first.", "warn");
        return false;
    }

    activeAbortController = new AbortController();
    setJobRunningUI(true);
    emptyState.classList.add("hidden");
    resultsPanel.classList.add("hidden");
    closeDiffInspector();
    logsFeed.innerHTML = "";
    setProgress(5);

    startTimer();
    logMessage(`Initiating Stage 1 Smart Pruning (${concurrency}x warm workers)...`, "info");

    let success = false;
    try {
        const response = await fetch("/api/deflate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text, concurrency }),
            signal: activeAbortController.signal
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
                    const payload = JSON.parse(trimmed.replace("data: ", ""));
                    handleSSEEvent(payload, "ablation");
                    if (payload.type === "complete") success = true;
                }
            }
        }
    } catch (err) {
        if (err.name !== "AbortError") {
            logMessage("Error: " + err.message, "error");
            showToast("Stage 1 Error: " + err.message, "error");
        }
    } finally {
        if (!isAutoChain || !success) {
            stopTimer();
            setJobRunningUI(false);
            activeAbortController = null;
        }
    }
    return success;
}

async function runSynonymPass() {
    if (!lastAblationResult || !lastAblationResult.modified_details || lastAblationResult.modified_details.length === 0) {
        showToast("No AI phrases needed pruning in Stage 1.", "info");
        return;
    }

    const concurrencySelect = document.getElementById("concurrencySelect");
    const ollamaModelSelect = document.getElementById("ollamaModelSelect");
    const fallbackPolicySelect = document.getElementById("fallbackPolicySelect");

    const concurrency = parseInt(concurrencySelect?.value || "6", 10);
    const ollamaModel = ollamaModelSelect?.value || DEFAULT_MODEL;
    const fallbackPolicy = fallbackPolicySelect?.value || "keep_zero";

    activeAbortController = new AbortController();
    setJobRunningUI(true);
    closeDiffInspector();
    setProgress(10);

    startTimer();
    logMessage(`Initiating Stage 2 Zero-Baseline Humanizer (${ollamaModel})...`, "info");

    try {
        const response = await fetch("/api/synonym-refine", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                original_text: lastAblationResult.original_text,
                clean_text: lastAblationResult.clean_text,
                ablated_items: lastAblationResult.modified_details,
                sentence_records: lastAblationResult.sentence_records,
                initial_score: lastAblationResult.initial_score,
                concurrency: concurrency,
                model: ollamaModel,
                fallback_policy: fallbackPolicy
            }),
            signal: activeAbortController.signal
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
                    const payload = JSON.parse(trimmed.replace("data: ", ""));
                    handleSSEEvent(payload, "synonym");
                }
            }
        }
    } catch (err) {
        if (err.name !== "AbortError") {
            logMessage("Error: " + err.message, "error");
            showToast("Stage 2 Error: " + err.message, "error");
        }
    } finally {
        stopTimer();
        setJobRunningUI(false);
        activeAbortController = null;
    }
}

function handleSSEEvent(data, passType) {
    if (data.type === "status") {
        logMessage(data.msg, "info");
    } else if (data.type === "chunk_start") {
        logMessage(`[Chunk ${data.chunk_index}/${data.total_chunks}] Scanning block (${data.chunk_length} chars)...`, "warn");
    } else if (data.type === "init") {
        document.getElementById("scoreBadge").textContent = `Score: ${data.initial_score}%`;
        logMessage(`Baseline AI Score: ${data.initial_score}% | Flagged Segments: ${data.total_highlights}`, "warn");
    } else if (data.type === "step_start") {
        if (typeof data.progress === "number") setProgress(data.progress);
        logMessage(`Step ${data.step}: Processing "${data.segment.substring(0, 42)}..."`, "step");
    } else if (data.type === "word_removed") {
        document.getElementById("scoreBadge").textContent = `Score: ${data.new_score}%`;
        logMessage(`RESOLVED: ${data.removed} -> Score: ${data.new_score}%`, "success");
    } else if (data.type === "segment_removed") {
        logMessage(`Segment pruned -> Score: ${data.new_score}%`, "warn");
    } else if (data.type === "error") {
        logMessage(`${data.msg}`, "error");
    } else if (data.type === "complete") {
        setProgress(100);
        const finalTime = document.getElementById("timerText")?.textContent || "00:00";
        logMessage(`${passType === "ablation" ? "Stage 1" : "Stage 2"} completed in ${finalTime}!`, "success");

        if (passType === "ablation") {
            lastAblationResult = data;
            const stage2Badge = document.getElementById("stage2Badge");
            if (stage2Badge) {
                stage2Badge.className = "px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30";
                stage2Badge.textContent = "Stage 2: Unlocked";
            }
            document.getElementById("flaggedCountSpan").textContent = data.removed_words.length;
            document.getElementById("flaggedSentCountSpan").textContent = (data.sentence_records || []).length || data.removed_words.length;
            if (data.removed_words.length > 0) {
                document.getElementById("synonymTriggerBanner")?.classList.remove("hidden");
            } else {
                document.getElementById("synonymTriggerBanner")?.classList.add("hidden");
            }
        } else {
            document.getElementById("synonymTriggerBanner")?.classList.add("hidden");
        }

        renderResults(data);
        showToast(`${passType === "ablation" ? "Stage 1" : "Stage 2"} finished! Final AI Score: ${data.final_score}%`, "success");
    }
}

function renderResults(data) {
    currentResultData = data;
    const resultsPanel = document.getElementById("resultsPanel");

    const initScore = data.initial_score ?? (lastAblationResult ? lastAblationResult.initial_score : 0.0);
    document.getElementById("resInitialScore").textContent = `${initScore}%`;
    document.getElementById("resFinalScore").textContent = `${data.final_score}%`;
    document.getElementById("resWordCount").textContent = (data.modified_details || []).length;

    const manualCount = (data.modified_details || []).filter(m => m.status === "manual").length;
    document.getElementById("resManualCount").textContent = manualCount;

    document.getElementById("highlightedContainer").innerHTML = data.highlighted_html;
    document.getElementById("cleanTextContainer").value = data.clean_text;
    document.getElementById("splitOriginalContainer").textContent = data.original_text;
    document.getElementById("splitCleanContainer").textContent = data.clean_text;
    cleanTextOutput = data.clean_text;

    const chipsContainer = document.getElementById("removedChips");
    chipsContainer.innerHTML = "";
    if (data.modified_details && data.modified_details.length > 0) {
        data.modified_details.forEach((item, idx) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.onclick = () => openDiffInspector(idx);
            const origShort = item.original.length > 36 ? item.original.substring(0, 36) + "..." : item.original;
            const replShort = (item.replacement || "").length > 36 ? item.replacement.substring(0, 36) + "..." : item.replacement;

            if (item.status === "replaced") {
                btn.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-emerald-950/60 hover:bg-emerald-900/80 border border-emerald-800 text-xs text-emerald-300 font-mono cursor-pointer transition-all";
                btn.innerHTML = `<i class="fa-solid fa-check text-[10px] text-emerald-400"></i> "${origShort}" -> "${replShort}"`;
            } else if (item.status === "manual") {
                btn.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-amber-950/60 hover:bg-amber-900/80 border border-amber-800 text-xs text-amber-300 font-mono cursor-pointer transition-all";
                btn.innerHTML = `<i class="fa-solid fa-triangle-exclamation text-[10px] text-amber-400"></i> "${origShort}" (Click to Review)`;
            } else {
                btn.className = "inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-rose-950/40 hover:bg-rose-900/60 border border-rose-800/60 text-xs text-rose-300 font-mono cursor-pointer transition-all";
                btn.innerHTML = `<i class="fa-solid fa-scissors text-[10px] text-rose-400"></i> "${origShort}"`;
            }
            chipsContainer.appendChild(btn);
        });
    } else {
        chipsContainer.innerHTML = `<span class="text-xs text-zinc-500">Passage already 0% AI - no modifications needed.</span>`;
    }

    resultsPanel.classList.remove("hidden");
    resultsPanel.classList.add("flex");
}

function openDiffInspector(idx) {
    if (!currentResultData || !currentResultData.modified_details || !currentResultData.modified_details[idx]) return;
    activeInspectorIdx = idx;
    const item = currentResultData.modified_details[idx];

    document.querySelectorAll(".diff-mark").forEach(el => el.classList.remove("active-mark"));
    const markEl = document.querySelector(`.diff-mark[data-diff-idx="${idx}"]`);
    if (markEl) markEl.classList.add("active-mark");

    const card = document.getElementById("diffInspectorCard");
    document.getElementById("inspOriginalText").textContent = item.original;
    document.getElementById("inspCustomInput").value = item.replacement || "";
    document.getElementById("inspCandidatesRow").classList.add("hidden");
    card.classList.remove("hidden");
    card.classList.add("flex");
}

function closeDiffInspector() {
    activeInspectorIdx = null;
    document.querySelectorAll(".diff-mark").forEach(el => el.classList.remove("active-mark"));
    const card = document.getElementById("diffInspectorCard");
    if (card) {
        card.classList.add("hidden");
        card.classList.remove("flex");
    }
}

function applyInspectorEdit(mode) {
    if (activeInspectorIdx === null || !currentResultData) return;
    const item = currentResultData.modified_details[activeInspectorIdx];
    const oldActiveInClean = item.replacement !== undefined && item.replacement !== "" ? item.replacement : item.original;

    let newTextVal = "";
    if (mode === "original") {
        newTextVal = item.original;
        item.status = "manual";
    } else {
        newTextVal = document.getElementById("inspCustomInput").value.trim();
        item.status = newTextVal ? "replaced" : "ablated";
    }

    if (oldActiveInClean && cleanTextOutput.includes(oldActiveInClean)) {
        cleanTextOutput = cleanTextOutput.replace(oldActiveInClean, newTextVal);
    } else if (item.original && cleanTextOutput.includes(item.original)) {
        cleanTextOutput = cleanTextOutput.replace(item.original, newTextVal);
    }

    item.replacement = newTextVal;
    currentResultData.clean_text = cleanTextOutput;

    const markEl = document.querySelector(`.diff-mark[data-diff-idx="${activeInspectorIdx}"]`);
    if (markEl) {
        markEl.textContent = newTextVal || item.original;
        if (item.status === "replaced") {
            markEl.className = "diff-mark active-mark bg-emerald-950/85 text-emerald-300 px-1.5 py-0.5 rounded border border-emerald-600/70 font-medium cursor-pointer";
        } else if (item.status === "manual") {
            markEl.className = "diff-mark active-mark bg-amber-950/85 text-amber-300 px-1.5 py-0.5 rounded border border-amber-500/80 font-medium cursor-pointer";
        } else {
            markEl.className = "diff-mark active-mark bg-rose-950/70 text-rose-300 px-1.5 py-0.5 rounded border border-rose-700/60 line-through decoration-rose-400 mx-0.5 cursor-pointer";
        }
    }

    document.getElementById("cleanTextContainer").value = cleanTextOutput;
    document.getElementById("splitCleanContainer").textContent = cleanTextOutput;
    const manualCount = currentResultData.modified_details.filter(m => m.status === "manual").length;
    document.getElementById("resManualCount").textContent = manualCount;
    showToast("Segment updated in Clean Copy.", "success");
}

async function rerollInspectorSentence() {
    if (activeInspectorIdx === null || !currentResultData) return;
    const item = currentResultData.modified_details[activeInspectorIdx];
    const btn = document.getElementById("inspRerollBtn");
    const row = document.getElementById("inspCandidatesRow");
    const ollamaModel = document.getElementById("ollamaModelSelect")?.value || DEFAULT_MODEL;
    const concurrency = parseInt(document.getElementById("concurrencySelect")?.value || "6", 10);

    if (btn) btn.disabled = true;
    showToast("Generating & testing fresh Ollama rewrites...", "info");

    try {
        const resp = await fetch("/api/reroll-sentence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_sentence: item.source_sentence || item.original,
                pruned_sentence: item.pruned_fallback || "",
                removed_tokens: item.removed_tokens || [item.original],
                current_clean_text: cleanTextOutput,
                current_replacement: item.replacement || item.original,
                model: ollamaModel,
                concurrency: concurrency
            })
        });
        const data = await resp.json();
        if (data.best) {
            document.getElementById("inspCustomInput").value = data.best;
            applyInspectorEdit("custom");
            showToast(data.status === "ok" ? "Applied 0%-AI verified rewrite!" : "Applied new rewrite candidate.", "success");
        }
        if (data.all_candidates && data.all_candidates.length > 0 && row) {
            row.innerHTML = "";
            data.all_candidates.forEach(cand => {
                const chip = document.createElement("button");
                chip.type = "button";
                chip.className = "text-[11px] bg-obsidian-base hover:bg-indigo-950 border border-obsidian-border px-2 py-1 rounded text-zinc-300 cursor-pointer text-left";
                chip.textContent = cand;
                chip.onclick = () => {
                    document.getElementById("inspCustomInput").value = cand;
                    applyInspectorEdit("custom");
                };
                row.appendChild(chip);
            });
            row.classList.remove("hidden");
            row.classList.add("flex");
        }
    } catch (e) {
        showToast("Re-roll error: " + e.message, "error");
    } finally {
        if (btn) btn.disabled = false;
    }
}

function switchTab(tab) {
    const reviewBtn = document.getElementById("tabReviewBtn");
    const cleanBtn = document.getElementById("tabCleanBtn");
    const splitBtn = document.getElementById("tabSplitBtn");

    const tabReview = document.getElementById("tabReview");
    const tabClean = document.getElementById("tabClean");
    const tabSplit = document.getElementById("tabSplit");

    const activeCls = "px-3 py-1.5 rounded-md text-xs font-medium bg-zinc-100 text-black shadow-sm transition-all flex items-center gap-1.5 cursor-pointer";
    const inactiveCls = "px-3 py-1.5 rounded-md text-xs font-medium text-zinc-400 hover:text-white transition-all flex items-center gap-1.5 cursor-pointer";

    reviewBtn.className = tab === "review" ? activeCls : inactiveCls;
    cleanBtn.className = tab === "clean" ? activeCls : inactiveCls;
    splitBtn.className = tab === "split" ? activeCls : inactiveCls;

    tabReview.classList.toggle("hidden", tab !== "review");
    tabClean.classList.toggle("hidden", tab !== "clean");
    tabClean.classList.toggle("flex", tab === "clean");
    tabSplit.classList.toggle("hidden", tab !== "split");
    tabSplit.classList.toggle("grid", tab === "split");
}

function copyCleanText() {
    const textToCopy = document.getElementById("cleanTextContainer")?.value || cleanTextOutput;
    navigator.clipboard.writeText(textToCopy).then(() => {
        showToast("Clean 0% AI text copied to clipboard!", "success");
    });
}

async function exportCleanFile(format) {
    const text = document.getElementById("cleanTextContainer")?.value || cleanTextOutput;
    if (!text || !text.trim()) {
        showToast("No clean text available to export.", "warn");
        return;
    }

    if (format === "txt") {
        const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "zerofy_clean_output.txt";
        a.click();
        URL.revokeObjectURL(url);
        showToast("Downloaded zerofy_clean_output.txt", "success");
    } else if (format === "docx") {
        try {
            const resp = await fetch("/api/export-docx", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            });
            if (!resp.ok) throw new Error("Failed to generate .docx");
            const blob = await resp.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "zerofy_clean_output.docx";
            a.click();
            URL.revokeObjectURL(url);
            showToast("Downloaded zerofy_clean_output.docx", "success");
        } catch (e) {
            showToast("DOCX Export Error: " + e.message, "error");
        }
    }
}

window.runFullAutoPipeline = runFullAutoPipeline;
window.runAblationPass = runAblationPass;
window.runSynonymPass = runSynonymPass;
window.runQuickScanInput = runQuickScanInput;
window.verifyCleanTextScore = verifyCleanTextScore;
window.cancelActiveJob = cancelActiveJob;
window.resetWorkspace = resetWorkspace;
window.handleFileUpload = handleFileUpload;
window.switchTab = switchTab;
window.copyCleanText = copyCleanText;
window.exportCleanFile = exportCleanFile;
window.openDiffInspector = openDiffInspector;
window.closeDiffInspector = closeDiffInspector;
window.applyInspectorEdit = applyInspectorEdit;
window.rerollInspectorSentence = rerollInspectorSentence;