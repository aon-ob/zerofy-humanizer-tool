import asyncio
from typing import AsyncGenerator
from core.browser_pool import cluster_manager, analyze_text_fast
from core.ollama_client import get_ollama_rewrites
from core.text_utils import (
    MAX_CHUNK_CHARACTERS,
    build_prioritized_ablation_candidates,
    extract_enclosing_sentence,
    generate_highlighted_html,
    is_segment_highlighted,
    replace_segment_in_text,
    segment_in_text,
    split_text_into_chunks,
)


async def run_parallel_candidate_search(
    pages: list,
    candidates: list[dict],
    target_segment: str,
    baseline_score: float,
    require_zero_score: bool = False,
    cancel_check=None,
) -> dict | None:
    # Evaluates candidates across worker pages and aborts remaining tasks once a match is found
    if not candidates:
        return None

    task_queue: asyncio.Queue = asyncio.Queue()
    for c in candidates:
        task_queue.put_nowait(c)

    solution_found = asyncio.Event()
    best_result: dict | None = None
    best_lock = asyncio.Lock()

    async def worker_loop(page):
        nonlocal best_result
        while not task_queue.empty() and not solution_found.is_set():
            if cancel_check and await cancel_check():
                solution_found.set()
                break
            try:
                spec = task_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                new_score, new_highlights = await analyze_text_fast(page, spec["candidate_text"])
            except asyncio.CancelledError:
                break

            if solution_found.is_set():
                break

            check_phrase = spec.get("modified_segment") or spec.get("candidate_phrase") or ""

            if require_zero_score:
                # Stage 2: Strict bidirectional check so no sub-clause of the rewrite is highlighted
                still_flagged = (
                    is_segment_highlighted(check_phrase, new_highlights)
                    or is_segment_highlighted(target_segment, new_highlights)
                )
                passed = not still_flagged and (
                    new_score == 0.0 or (new_score <= baseline_score and len(new_highlights) == 0)
                )
            else:
                # Stage 1: Accept if the full modified segment is no longer highlighted (or split into smaller sub-segments)
                still_flagged = any(
                    (check_phrase and check_phrase in h) or (target_segment in h)
                    for h in new_highlights
                )
                passed = not still_flagged or new_score == 0.0

            async with best_lock:
                if passed and not solution_found.is_set():
                    best_result = {
                        "spec": spec,
                        "score": new_score,
                        "highlights": new_highlights,
                        "passed": True,
                    }
                    solution_found.set()
                    break
                if not still_flagged and (best_result is None or new_score < best_result["score"]):
                    best_result = {
                        "spec": spec,
                        "score": new_score,
                        "highlights": new_highlights,
                        "passed": False,
                    }

    worker_tasks = [asyncio.create_task(worker_loop(p)) for p in pages]
    await asyncio.gather(*worker_tasks, return_exceptions=True)

    if best_result and (best_result.get("passed") or not require_zero_score):
        return best_result
    return best_result if (best_result and best_result["score"] < baseline_score) else None


def _record_sentence_edit(
    sentence_records: list[dict],
    source_sentence: str,
    target_segment: str,
    pruned_segment: str,
    removed_token: str,
    chunk_index: int,
):
    # Groups multiple token removals belonging to the same sentence into one coherent record
    for rec in sentence_records:
        if rec["chunk_index"] == chunk_index and (
            rec["source_sentence"] == source_sentence
            or rec["current_sentence"] == target_segment
            or segment_in_text(rec["current_sentence"], target_segment)
        ):
            rec["removed_tokens"].append(removed_token)
            if rec["current_sentence"] == target_segment:
                rec["current_sentence"] = pruned_segment
            else:
                rec["current_sentence"] = replace_segment_in_text(
                    rec["current_sentence"], target_segment, pruned_segment
                )
            return

    initial_pruned = (
        pruned_segment
        if source_sentence == target_segment
        else replace_segment_in_text(source_sentence, target_segment, pruned_segment)
    )
    sentence_records.append({
        "source_sentence": source_sentence,
        "current_sentence": initial_pruned,
        "removed_tokens": [removed_token],
        "chunk_index": chunk_index,
    })


async def stream_ablation_pipeline(
    document_text: str,
    concurrency: int,
    cancel_check=None,
) -> AsyncGenerator[dict, None]:
    # Stage 1: Smart AI Token Pruning with heuristic ranking and infinite-loop protection
    modified_phrases_log = []
    sentence_records = []
    clean_chunks = []
    chunk_initial_scores = []
    chunk_weights = []

    chunks = split_text_into_chunks(document_text, MAX_CHUNK_CHARACTERS)
    total_chunks = len(chunks)

    try:
        if total_chunks > 1:
            yield {
                "type": "status",
                "msg": f"Text length: {len(document_text)} chars. Split into {total_chunks} chunk(s).",
            }

        status_messages = []
        pages = await cluster_manager.ensure_workers(
            concurrency, status_cb=lambda m: status_messages.append(m)
        )
        for m in status_messages:
            yield {"type": "status", "msg": m}

        yield {
            "type": "status",
            "msg": f"Running Stage 1 Smart Pruning across {len(pages)} warm Playwright worker(s)...",
        }

        for chunk_idx, chunk_text in enumerate(chunks, 1):
            if cancel_check and await cancel_check():
                yield {"type": "error", "msg": "Job cancelled by user."}
                return

            yield {
                "type": "chunk_start",
                "chunk_index": chunk_idx,
                "total_chunks": total_chunks,
                "chunk_length": len(chunk_text),
            }

            current_text = chunk_text
            score, highlights = await analyze_text_fast(pages[0], current_text)

            chunk_initial_scores.append(score)
            chunk_weights.append(len(chunk_text))

            yield {
                "type": "init",
                "initial_score": score,
                "total_highlights": len(highlights),
                "chunk_index": chunk_idx,
                "total_chunks": total_chunks,
            }

            step = 1
            initial_highlight_count = max(1, len(highlights))
            max_steps_per_chunk = 80

            while score > 0 and highlights and step <= max_steps_per_chunk:
                if cancel_check and await cancel_check():
                    yield {"type": "error", "msg": "Job cancelled by user."}
                    return

                target_segment = highlights[0]
                enclosing_orig_sentence = extract_enclosing_sentence(chunk_text, target_segment)
                words = target_segment.split()
                segment_resolved = False

                progress_pct = min(
                    95,
                    int(((initial_highlight_count - len(highlights)) / initial_highlight_count) * 100),
                )

                yield {
                    "type": "step_start",
                    "step": step,
                    "current_score": score,
                    "segment": target_segment,
                    "chunk_index": chunk_idx,
                    "total_chunks": total_chunks,
                    "progress": progress_pct,
                }

                for k in range(1, len(words) + 1):
                    candidates = build_prioritized_ablation_candidates(current_text, target_segment, k)
                    if not candidates:
                        break

                    win = await run_parallel_candidate_search(
                        pages=pages,
                        candidates=candidates,
                        target_segment=target_segment,
                        baseline_score=score,
                        require_zero_score=False,
                        cancel_check=cancel_check,
                    )

                    if win and win.get("passed"):
                        spec = win["spec"]
                        modified_phrases_log.append({
                            "original": spec["removed_str"],
                            "replacement": "",
                            "status": "ablated",
                            "source_sentence": enclosing_orig_sentence,
                            "pruned_sentence": spec["modified_segment"],
                            "chunk_index": chunk_idx,
                        })
                        _record_sentence_edit(
                            sentence_records,
                            source_sentence=enclosing_orig_sentence,
                            target_segment=target_segment,
                            pruned_segment=spec["modified_segment"],
                            removed_token=spec["removed_str"],
                            chunk_index=chunk_idx,
                        )
                        current_text = spec["candidate_text"]
                        score = win["score"]
                        highlights = win["highlights"]
                        segment_resolved = True

                        yield {
                            "type": "word_removed",
                            "removed": spec["removed_str"],
                            "new_score": score,
                            "remaining_segments": len(highlights),
                            "chunk_index": chunk_idx,
                            "total_chunks": total_chunks,
                        }
                        break

                step += 1

                if not segment_resolved:
                    new_text = replace_segment_in_text(
                        current_text, target_segment, "", enclosing_sentence=enclosing_orig_sentence
                    )
                    if new_text != current_text:
                        modified_phrases_log.append({
                            "original": target_segment,
                            "replacement": "",
                            "status": "ablated",
                            "source_sentence": enclosing_orig_sentence,
                            "pruned_sentence": "",
                            "chunk_index": chunk_idx,
                        })
                        _record_sentence_edit(
                            sentence_records,
                            source_sentence=enclosing_orig_sentence,
                            target_segment=target_segment,
                            pruned_segment="",
                            removed_token=target_segment,
                            chunk_index=chunk_idx,
                        )
                        current_text = new_text
                        score, highlights = await analyze_text_fast(pages[0], current_text)

                        yield {
                            "type": "segment_removed",
                            "removed": target_segment,
                            "new_score": score,
                            "remaining_segments": len(highlights),
                            "chunk_index": chunk_idx,
                            "total_chunks": total_chunks,
                        }

                    # Failsafe: if target_segment could not be matched or is still at highlights[0], pop it to prevent infinite loops
                    if highlights and highlights[0] == target_segment:
                        highlights.pop(0)

            clean_chunks.append(current_text)

        final_clean_text = "\n\n".join(clean_chunks)
        total_weight = sum(chunk_weights) or 1
        overall_initial_score = round(
            sum(s * w for s, w in zip(chunk_initial_scores, chunk_weights)) / total_weight, 1
        )

        highlighted_html = generate_highlighted_html(document_text, modified_phrases_log)

        yield {
            "type": "complete",
            "stage": "ablation",
            "initial_score": overall_initial_score,
            "final_score": 0.0,
            "removed_words": [m["original"] for m in modified_phrases_log],
            "modified_details": modified_phrases_log,
            "sentence_records": sentence_records,
            "original_text": document_text,
            "highlighted_html": highlighted_html,
            "clean_text": final_clean_text,
            "total_chunks": total_chunks,
        }

    except Exception as e:
        yield {"type": "error", "msg": f"Stage 1 Engine Error: {str(e)}"}


async def stream_synonym_pipeline(
    original_text: str,
    clean_text: str,
    ablated_items: list[dict],
    sentence_records: list[dict] | None,
    initial_score: float,
    concurrency: int,
    model_name: str,
    fallback_policy: str = "keep_zero",
    cancel_check=None,
) -> AsyncGenerator[dict, None]:
    # Stage 2: Zero-Baseline Contextual Ollama Humanizer
    try:
        pages = await cluster_manager.ensure_workers(concurrency)

        if not sentence_records:
            sentence_records = []
            for item in ablated_items:
                raw_tok = item.get("original", "").strip()
                src_sent = item.get("source_sentence") or extract_enclosing_sentence(original_text, raw_tok)
                pruned_sent = item.get("pruned_sentence") or replace_segment_in_text(src_sent, raw_tok, "")
                _record_sentence_edit(
                    sentence_records,
                    source_sentence=src_sent,
                    target_segment=src_sent,
                    pruned_segment=pruned_sent,
                    removed_token=raw_tok,
                    chunk_index=item.get("chunk_index", 1),
                )

        total_targets = len(sentence_records)
        yield {
            "type": "status",
            "msg": f"Starting Stage 2 Zero-Baseline Humanizer on {total_targets} sentence(s) using '{model_name}'...",
        }

        working_text = clean_text if clean_text and clean_text.strip() else original_text
        baseline_score, baseline_highlights = await analyze_text_fast(pages[0], working_text)

        final_modified_log = []

        for idx, rec in enumerate(sentence_records, 1):
            if cancel_check and await cancel_check():
                yield {"type": "error", "msg": "Stage 2 cancelled by user."}
                return

            src_sentence = rec["source_sentence"]
            pruned_sentence = rec["current_sentence"]
            removed_tokens = rec["removed_tokens"]

            anchor_in_working = pruned_sentence if (pruned_sentence and segment_in_text(working_text, pruned_sentence)) else src_sentence
            progress_pct = int(((idx - 1) / max(1, total_targets)) * 85)

            yield {
                "type": "step_start",
                "step": idx,
                "current_score": baseline_score,
                "segment": src_sentence,
                "chunk_index": 1,
                "total_chunks": 1,
                "progress": progress_pct,
            }

            accepted_rewrite = None
            failed_candidates = []

            for round_num in (1, 2):
                if cancel_check and await cancel_check():
                    return

                round_label = "Pass 1" if round_num == 1 else "Pass 2 (Self-Correction Retry)"
                yield {
                    "type": "status",
                    "msg": f"[{idx}/{total_targets}] {round_label}: Generating 6 contextual rewrites via Ollama ({model_name})...",
                }

                raw_candidates = await get_ollama_rewrites(
                    target_sentence=src_sentence,
                    pruned_sentence=pruned_sentence,
                    flagged_tokens=removed_tokens,
                    full_text=original_text,
                    model_name=model_name,
                    retry_round=round_num,
                    failed_candidates=failed_candidates,
                )

                if not raw_candidates:
                    continue

                yield {
                    "type": "status",
                    "msg": f"Testing {len(raw_candidates)} candidate rewrite(s) against 0% AI baseline in ZeroGPT...",
                }

                candidate_specs = []
                for cand in raw_candidates:
                    if anchor_in_working and segment_in_text(working_text, anchor_in_working):
                        cand_text = replace_segment_in_text(working_text, anchor_in_working, cand)
                    else:
                        cand_text = replace_segment_in_text(working_text, src_sentence, cand)

                    candidate_specs.append({
                        "candidate_phrase": cand,
                        "modified_segment": cand,
                        "candidate_text": cand_text,
                    })

                win = await run_parallel_candidate_search(
                    pages=pages,
                    candidates=candidate_specs,
                    target_segment=src_sentence,
                    baseline_score=baseline_score,
                    require_zero_score=True,
                    cancel_check=cancel_check,
                )

                if win and win.get("passed"):
                    accepted_rewrite = win["spec"]["candidate_phrase"]
                    working_text = win["spec"]["candidate_text"]
                    baseline_score = win["score"]
                    baseline_highlights = win["highlights"]
                    break
                else:
                    failed_candidates.extend(raw_candidates)

            if accepted_rewrite:
                final_modified_log.append({
                    "original": src_sentence,
                    "replacement": accepted_rewrite,
                    "pruned_fallback": pruned_sentence,
                    "removed_tokens": removed_tokens,
                    "status": "replaced",
                    "source_sentence": src_sentence,
                })
                yield {
                    "type": "word_removed",
                    "removed": f'"{src_sentence[:32]}..." -> "{accepted_rewrite[:32]}..."',
                    "new_score": baseline_score,
                    "remaining_segments": len(baseline_highlights),
                    "chunk_index": 1,
                    "total_chunks": 1,
                }
            else:
                if fallback_policy == "keep_zero" and pruned_sentence:
                    final_modified_log.append({
                        "original": src_sentence,
                        "replacement": pruned_sentence,
                        "pruned_fallback": pruned_sentence,
                        "removed_tokens": removed_tokens,
                        "status": "manual",
                        "source_sentence": src_sentence,
                    })
                    yield {
                        "type": "status",
                        "msg": f"All 12 LLM rewrites triggered detection for '{src_sentence[:30]}...'. Kept 0%-AI pruned version and flagged in Amber.",
                    }
                else:
                    if anchor_in_working and segment_in_text(working_text, anchor_in_working):
                        working_text = replace_segment_in_text(working_text, anchor_in_working, src_sentence)
                    final_modified_log.append({
                        "original": src_sentence,
                        "replacement": src_sentence,
                        "pruned_fallback": pruned_sentence,
                        "removed_tokens": removed_tokens,
                        "status": "manual",
                        "source_sentence": src_sentence,
                    })
                    yield {
                        "type": "status",
                        "msg": f"No 0%-AI rewrite found for '{src_sentence[:30]}...'. Restored original and flagged in Amber for manual review.",
                    }

        yield {
            "type": "status",
            "msg": "Running Final Global Convergence Check on assembled document...",
        }
        final_score, final_highlights = await analyze_text_fast(pages[0], working_text, use_cache=False)

        conv_loops = 0
        while final_score > 0 and final_highlights and conv_loops < 3:
            conv_loops += 1
            flagged_seg = final_highlights[0]
            flagged_sent = extract_enclosing_sentence(working_text, flagged_seg)
            yield {
                "type": "status",
                "msg": f"[Convergence Pass {conv_loops}] Resolving secondary highlight: '{flagged_sent[:40]}...'",
            }
            cands = await get_ollama_rewrites(
                target_sentence=flagged_sent,
                pruned_sentence=replace_segment_in_text(flagged_sent, flagged_seg, ""),
                flagged_tokens=[flagged_seg],
                full_text=working_text,
                model_name=model_name,
                retry_round=2,
            )
            if not cands:
                break
            specs = [
                {
                    "candidate_phrase": c,
                    "modified_segment": c,
                    "candidate_text": replace_segment_in_text(working_text, flagged_sent, c),
                }
                for c in cands
            ]
            win = await run_parallel_candidate_search(
                pages=pages,
                candidates=specs,
                target_segment=flagged_sent,
                baseline_score=final_score,
                require_zero_score=True,
                cancel_check=cancel_check,
            )
            if win and win.get("passed"):
                working_text = win["spec"]["candidate_text"]
                final_score = win["score"]
                final_highlights = win["highlights"]
            else:
                break

        highlighted_html = generate_highlighted_html(original_text, final_modified_log)

        yield {
            "type": "complete",
            "stage": "synonym",
            "initial_score": initial_score,
            "final_score": final_score,
            "removed_words": [m["original"] for m in final_modified_log],
            "modified_details": final_modified_log,
            "sentence_records": sentence_records,
            "original_text": original_text,
            "highlighted_html": highlighted_html,
            "clean_text": working_text,
            "total_chunks": 1,
        }

    except Exception as e:
        yield {"type": "error", "msg": f"Stage 2 Engine Error: {str(e)}"}