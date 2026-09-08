"""
run_parallel.py — run question_list_all.jsonl (both general & detailed
versions) against all 6 models in parallel.

Usage
-----
    # Full run: all 6 models x 200 questions x 2 versions = 2,400 calls
    python run_parallel.py

    # Smoke test: first 3 questions only
    python run_parallel.py --limit 3

    # Resume: skip (row_id, version) pairs already in the output files
    python run_parallel.py --resume

    # Subset of models
    python run_parallel.py --models qianwen deepseek

Output
------
    results/<model_key>_general_results.jsonl
    results/<model_key>_detailed_results.jsonl

    Each line:
    {
        "row_id": 0, "final_qid": "Q001", "cluster_id": 0,
        "cancer_type": "lung_cancer", "question_type": "treatment_goals",
        "version": "general",          # or "detailed"
        "question": "...",
        "model": "qianwen",
        "response": "...",
        "error": null,
        "elapsed_s": 1.23
    }

Parallelism
-----------
    All 6 models run concurrently (one thread per model), and within each
    model up to WORKERS_PER_MODEL requests are in flight at once, drawn from
    the combined general+detailed task list. Total in-flight <= 6 x 4 = 24.
"""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

import providers
from models import MODELS
from search_tools import run_search, format_search_results

load_dotenv()

QUESTIONS_FILE = Path(__file__).parent / "question_list_all_new.jsonl"
RESULTS_DIR = Path(__file__).parent / "results_doubal_only_new"

VERSIONS = ("general", "detailed")
WORKERS_PER_MODEL = 4

SYSTEM_PROMPT = "请回答当前问题"

_print_lock = threading.Lock()


def log(msg: str):
    with _print_lock:
        print(msg, flush=True)


def load_questions(limit: int | None) -> list[dict]:
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return rows[:limit] if limit else rows


def load_done(model_key: str, version: str) -> set[int]:
    """row_ids successfully answered (error == null) for this (model, version)."""
    path = RESULTS_DIR / f"{model_key}_{version}_results.jsonl"
    if not path.exists():
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                # Only count as done if the call succeeded
                if rec.get("error") is None:
                    done.add(rec["row_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def query_one(model_key: str, cfg: dict, row: dict, version: str) -> dict:
    question_text = row[f"question_{version}"]

    # Retrieve search results if search_engine is configured. Accepts one
    # engine ("tavily") or several (["tavily", "doubao"]).
    search_engine = cfg.get("search_engine")
    results, engines_used, search_errors = run_search(
        search_engine, question_text, max_results=3
    )
    for msg in search_errors:
        log(f"[{model_key}] Search failed for row={row['row_id']} "
            f"({version}): {msg[:120]}")
    search_context = format_search_results(results)

    # Inject search context into user message if available
    user_content = question_text
    if search_context:
        user_content = f"{search_context}\n问题: {question_text}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    call_kwargs = {
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 1024),
    }
    if "extra_body" in cfg:
        call_kwargs["extra_body"] = cfg["extra_body"]

    start = time.monotonic()
    response, error = None, None
    try:
        response = providers.chat(
            provider=cfg["provider"],
            model_id=cfg["model_id"],
            messages=messages,
            **call_kwargs,
        )
    except Exception as exc:
        error = str(exc)
    elapsed = round(time.monotonic() - start, 3)

    return {
        "row_id":        row["row_id"],
        "final_qid":     row["final_qid"],
        "cluster_id":    row["cluster_id"],
        "cancer_type":   row["cancer_type"],
        "question_type": row["question_type"],
        "version":       version,
        "question":      question_text,
        "model":         model_key,
        "response":      response,
        "error":         error,
        "elapsed_s":     elapsed,
        "search_engine": search_engine,
        "search_used":   bool(search_context),
        "engines_used":  engines_used,
        "search_hits":   len(results),
    }


def run_model(model_key: str, questions: list[dict], resume: bool):
    cfg = MODELS[model_key]
    name = cfg["display_name"]

    # Build the combined task list across both versions.
    tasks = []
    files = {}
    for version in VERSIONS:
        done = load_done(model_key, version) if resume else set()
        todo = [r for r in questions if r["row_id"] not in done]
        if len(todo) < len(questions):
            log(f"[{name}] {version}: resuming — "
                f"{len(questions) - len(todo)} done, {len(todo)} remaining.")
        path = RESULTS_DIR / f"{model_key}_{version}_results.jsonl"
        mode = "a" if resume and path.exists() else "w"
        files[version] = open(path, mode, encoding="utf-8")
        tasks.extend((row, version) for row in todo)

    if not tasks:
        for fh in files.values():
            fh.close()
        log(f"[{name}] everything already done, skipping.")
        return

    total = len(tasks)
    errors = 0
    completed = 0
    start = time.monotonic()

    try:
        with ThreadPoolExecutor(max_workers=WORKERS_PER_MODEL) as pool:
            futures = {
                pool.submit(query_one, model_key, cfg, row, version): (row, version)
                for row, version in tasks
            }
            for fut in as_completed(futures):
                record = fut.result()
                fh = files[record["version"]]
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                completed += 1
                if record["error"]:
                    errors += 1
                    log(f"[{name}] ERROR row={record['row_id']} "
                        f"({record['version']}): {record['error'][:120]}")
                if completed % 25 == 0 or completed == total:
                    rate = completed / (time.monotonic() - start)
                    eta = (total - completed) / rate if rate else 0
                    log(f"[{name}] {completed}/{total} "
                        f"({errors} errors, ~{eta/60:.0f} min left)")
    finally:
        for fh in files.values():
            fh.close()

    mins = (time.monotonic() - start) / 60
    log(f"[{name}] DONE — {total - errors}/{total} succeeded, "
        f"{errors} errors, {mins:.1f} min")


def main():
    parser = argparse.ArgumentParser(
        description="Run general+detailed questions against 6 models in parallel."
    )
    parser.add_argument("--models", nargs="+", choices=list(MODELS.keys()),
                        default=list(MODELS.keys()))
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N questions (smoke test).")
    parser.add_argument("--resume", action="store_true",
                        help="Skip (row_id, version) pairs already in output files.")
    args = parser.parse_args()

    questions = load_questions(args.limit)
    RESULTS_DIR.mkdir(exist_ok=True)
    n_calls = len(questions) * len(VERSIONS) * len(args.models)
    log(f"Loaded {len(questions)} questions ({n_calls} total calls) "
        f"from {QUESTIONS_FILE.name}")
    log(f"Models: {args.models} | {WORKERS_PER_MODEL} workers per model\n")

    overall = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(args.models)) as pool:
        futs = [
            pool.submit(run_model, mk, questions, args.resume)
            for mk in args.models
        ]
        for fut in futs:
            fut.result()  # propagate unexpected exceptions

    log(f"\nAll done in {(time.monotonic() - overall)/60:.1f} min. "
        f"Results in {RESULTS_DIR}/")


if __name__ == "__main__":
    main()
