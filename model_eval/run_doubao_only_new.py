"""
run_doubao_only_new.py — run question_list_all_new.jsonl against all 6 models
with doubao search engine only.

Usage
-----
    python run_doubao_only_new.py

Output
------
    results_doubal_only_new/<model_key>_results.jsonl

    Each line:
    {
        "question_id": "Q001",
        "cancer_type": "...",
        "question_type": "...",
        "question": "...",
        "model": "qianwen",
        "response": "...",
        "error": null,
        "elapsed_s": 1.23,
        "search_engine": "doubao",
        "search_used": true,
        "engines_used": ["doubao"],
        "search_hits": 3
    }
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


def load_done(model_key: str) -> set[str]:
    """question_ids successfully answered (error == null) for this model."""
    path = RESULTS_DIR / f"{model_key}_results.jsonl"
    if not path.exists():
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                # Only count as done if the call succeeded
                if rec.get("error") is None:
                    done.add(rec["question_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def query_one(model_key: str, cfg: dict, question_row: dict) -> dict:
    question_text = question_row["question"]
    question_id = question_row["question_id"]

    # Retrieve search results using doubao search engine
    search_engine = cfg.get("search_engine", "doubao")
    results, engines_used, search_errors = run_search(
        search_engine, question_text, max_results=3
    )
    for msg in search_errors:
        log(f"[{model_key}] Search failed for {question_id}: {msg[:120]}")
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
        "question_id":   question_id,
        "cancer_type":   question_row["cancer_type"],
        "question_type": question_row["question_type"],
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

    # Load already completed questions
    done = load_done(model_key) if resume else set()
    todo = [q for q in questions if q["question_id"] not in done]

    if len(todo) < len(questions):
        log(f"[{name}] resuming — {len(questions) - len(todo)} done, {len(todo)} remaining.")

    path = RESULTS_DIR / f"{model_key}_results.jsonl"
    mode = "a" if resume and path.exists() else "w"

    if not todo:
        log(f"[{name}] everything already done, skipping.")
        return

    total = len(todo)
    errors = 0
    completed = 0
    start = time.monotonic()

    with open(path, mode, encoding="utf-8") as fh:
        with ThreadPoolExecutor(max_workers=WORKERS_PER_MODEL) as pool:
            futures = {
                pool.submit(query_one, model_key, cfg, q): q
                for q in todo
            }
            for fut in as_completed(futures):
                record = fut.result()
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                completed += 1
                if record["error"]:
                    errors += 1
                    log(f"[{name}] ERROR {record['question_id']}: {record['error'][:120]}")

                # Progress updates
                if completed % 10 == 0 or completed == total:
                    rate = completed / (time.monotonic() - start)
                    eta = (total - completed) / rate if rate else 0
                    percent = (completed / total) * 100
                    log(f"[{name}] Progress: {completed}/{total} ({percent:.1f}%) | "
                        f"Errors: {errors} | ETA: ~{eta/60:.0f} min")

    mins = (time.monotonic() - start) / 60
    log(f"[{name}] ✓ DONE — {total - errors}/{total} succeeded, {errors} errors, {mins:.1f} min")


def main():
    parser = argparse.ArgumentParser(
        description="Run questions against 6 models with doubao search only."
    )
    parser.add_argument("--models", nargs="+", choices=list(MODELS.keys()),
                        default=list(MODELS.keys()))
    parser.add_argument("--limit", type=int, default=None,
                        help="Only run the first N questions (smoke test).")
    parser.add_argument("--resume", action="store_true",
                        help="Skip question_ids already in output files.")
    args = parser.parse_args()

    questions = load_questions(args.limit)
    RESULTS_DIR.mkdir(exist_ok=True)
    n_calls = len(questions) * len(args.models)

    log("=" * 80)
    log(f"Starting model evaluation with doubao search engine")
    log(f"Loaded {len(questions)} questions from {QUESTIONS_FILE.name}")
    log(f"Total API calls: {n_calls} ({len(questions)} questions × {len(args.models)} models)")
    log(f"Models: {', '.join(args.models)}")
    log(f"Workers per model: {WORKERS_PER_MODEL}")
    log(f"Output directory: {RESULTS_DIR}/")
    log("=" * 80)
    log("")

    overall = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(args.models)) as pool:
        futs = [
            pool.submit(run_model, mk, questions, args.resume)
            for mk in args.models
        ]
        for fut in futs:
            fut.result()  # propagate unexpected exceptions

    total_mins = (time.monotonic() - overall) / 60
    log("")
    log("=" * 80)
    log(f"✓ ALL MODELS COMPLETE in {total_mins:.1f} minutes")
    log(f"Results saved to: {RESULTS_DIR}/")
    log("=" * 80)


if __name__ == "__main__":
    main()
