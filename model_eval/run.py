"""
run.py — load questions, query each model, and save per-model results.

Usage
-----
    # Run all 6 models against every question
    python run.py

    # Run a specific subset of models
    python run.py --models qianwen deepseek

    # Dry-run: print questions without calling any API
    python run.py --dry-run

    # Resume: skip questions already answered (reads existing result files)
    python run.py --resume

Output
------
    results/<model_key>_results.jsonl
    Each line is a JSON object:
    {
        "question_id": 0,
        "cluster_id": 0,
        "cancer_type": "lung_cancer",
        "question_type": "treatment_goals",
        "question": "...",
        "model": "qianwen",
        "response": "...",
        "error": null,           # or an error message string
        "elapsed_s": 1.23
    }
"""

import argparse
import json
import time
import sys
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

import providers
from models import MODELS
from search_tools import run_search, format_search_results

load_dotenv()

QUESTIONS_FILE = Path(__file__).parent / "questions.jsonl"
RESULTS_DIR = Path(__file__).parent / "results"

# System prompt applied to every question
SYSTEM_PROMPT = (
    '请回答当前问题'
)


def load_questions() -> list[dict]:
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_existing_results(model_key: str) -> set[int]:
    """Return the set of question_ids already answered for this model."""
    path = RESULTS_DIR / f"{model_key}_results.jsonl"
    if not path.exists():
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                done.add(rec["question_id"])
            except (json.JSONDecodeError, KeyError):
                pass
    return done


def query_model(model_key: str, cfg: dict, question: dict) -> dict:
    """Call the model and return a result record."""
    question_text = question["question"]

    # Retrieve search results if search_engine is configured. Accepts one
    # engine ("tavily") or several (["tavily", "doubao"]).
    search_engine = cfg.get("search_engine")
    results, engines_used, search_errors = run_search(
        search_engine, question_text, max_results=3
    )
    for msg in search_errors:
        tqdm.write(
            f"  [WARNING] Search failed for qid={question['question_id']}: {msg[:120]}"
        )
    search_context = format_search_results(results)

    # Inject search context into user message if available
    user_content = question_text
    if search_context:
        user_content = f"{search_context}\n问题: {question_text}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    start = time.monotonic()
    error = None
    response = None

    # Per-model generation overrides from models.py (e.g. doubao disables its
    # reasoning pass and needs a larger max_tokens). Falls back to defaults.
    call_kwargs = {
        "temperature": cfg.get("temperature", 0.3),
        "max_tokens": cfg.get("max_tokens", 1024),
    }
    if "extra_body" in cfg:
        call_kwargs["extra_body"] = cfg["extra_body"]

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
        "question_id":   question["question_id"],
        "cluster_id":    question["cluster_id"],
        "cancer_type":   question["cancer_type"],
        "question_type": question["question_type"],
        "question":      question["question"],
        "model":         model_key,
        "response":      response,
        "error":         error,
        "elapsed_s":     elapsed,
        "search_engine": search_engine,
        "search_used":   bool(search_context),
        "engines_used":  engines_used,
        "search_hits":   len(results),
    }


def run_model(model_key: str, questions: list[dict], resume: bool, dry_run: bool):
    cfg = MODELS[model_key]
    out_path = RESULTS_DIR / f"{model_key}_results.jsonl"
    RESULTS_DIR.mkdir(exist_ok=True)

    done_ids = load_existing_results(model_key) if resume else set()
    todo = [q for q in questions if q["question_id"] not in done_ids]

    if not todo:
        print(f"[{cfg['display_name']}] all {len(questions)} questions already done, skipping.")
        return

    skipped = len(questions) - len(todo)
    if skipped:
        print(f"[{cfg['display_name']}] resuming — {skipped} done, {len(todo)} remaining.")

    mode = "a" if resume and out_path.exists() else "w"
    errors = 0

    with open(out_path, mode, encoding="utf-8") as f:
        for q in tqdm(todo, desc=cfg["display_name"], unit="q"):
            if dry_run:
                print(f"  [dry-run] qid={q['question_id']}: {q['question'][:60]}…")
                continue
            record = query_model(model_key, cfg, q)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            if record["error"]:
                errors += 1
                tqdm.write(
                    f"  ERROR qid={q['question_id']}: {record['error'][:120]}"
                )

    if not dry_run:
        total = len(todo)
        print(
            f"[{cfg['display_name']}] done — {total - errors}/{total} succeeded, "
            f"{errors} errors → {out_path}"
        )


def main():
    parser = argparse.ArgumentParser(description="Query Chinese LLMs with oncology questions.")
    parser.add_argument(
        "--models", nargs="+", choices=list(MODELS.keys()),
        default=list(MODELS.keys()),
        help="Which models to run (default: all 6).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Skip questions already present in the output file.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print questions without making any API calls.",
    )
    args = parser.parse_args()

    questions = load_questions()
    print(f"Loaded {len(questions)} questions from {QUESTIONS_FILE}")
    print(f"Models to run: {args.models}\n")

    for model_key in args.models:
        run_model(model_key, questions, resume=args.resume, dry_run=args.dry_run)

    print("\nAll done.")


if __name__ == "__main__":
    main()
