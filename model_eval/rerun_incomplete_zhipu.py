"""
Rerun the 4 incomplete zhipu responses with higher max_tokens limit.
"""
import json
import time
from pathlib import Path
from dotenv import load_dotenv
import providers

load_dotenv()

# Incomplete questions that need to be rerun
INCOMPLETE_QIDS = ["Q108", "Q146", "Q150", "Q246"]

# Load questions
QUESTIONS_FILE = Path(__file__).parent / "question_list_all_new.jsonl"
questions = {}
with open(QUESTIONS_FILE, encoding="utf-8") as f:
    for line in f:
        if line.strip():
            q = json.loads(line)
            if q["question_id"] in INCOMPLETE_QIDS:
                questions[q["question_id"]] = q

# Load existing results
RESULTS_FILE = Path(__file__).parent / "results_doubal_only_new" / "zhipu_results.jsonl"
existing_results = []
with open(RESULTS_FILE, encoding="utf-8") as f:
    for line in f:
        existing_results.append(json.loads(line))

print(f"Found {len(questions)} questions to rerun: {list(questions.keys())}")
print(f"Loaded {len(existing_results)} existing results")

# Configuration for zhipu with higher max_tokens
ZHIPU_CONFIG = {
    "provider": "zhipu",
    "model_id": "glm-4-plus",
    "temperature": 0.3,
    "max_tokens": 4096,  # Increased from 1024 to 4096
}

SYSTEM_PROMPT = "请回答当前问题"

def rerun_question(question_row: dict) -> dict:
    """Rerun a single question with search context."""
    question_id = question_row["question_id"]
    question_text = question_row["question"]

    print(f"\n[{question_id}] Running: {question_text[:50]}...")

    # Import search tools
    from search_tools import run_search, format_search_results

    # Get search results
    results, engines_used, search_errors = run_search(
        "doubao", question_text, max_results=3
    )
    if search_errors:
        print(f"  Search warnings: {search_errors[0][:80]}")

    search_context = format_search_results(results)
    user_content = question_text
    if search_context:
        user_content = f"{search_context}\n问题: {question_text}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    start = time.monotonic()
    response, error = None, None
    try:
        response = providers.chat(
            provider=ZHIPU_CONFIG["provider"],
            model_id=ZHIPU_CONFIG["model_id"],
            messages=messages,
            temperature=ZHIPU_CONFIG["temperature"],
            max_tokens=ZHIPU_CONFIG["max_tokens"],
        )
        print(f"  ✓ Got response: {len(response)} chars")
    except Exception as exc:
        error = str(exc)
        print(f"  ✗ Error: {error[:80]}")

    elapsed = round(time.monotonic() - start, 3)

    return {
        "question_id": question_id,
        "cancer_type": question_row["cancer_type"],
        "question_type": question_row["question_type"],
        "question": question_text,
        "model": "zhipu",
        "response": response,
        "error": error,
        "elapsed_s": elapsed,
        "search_engine": "doubao",
        "search_used": bool(search_context),
        "engines_used": engines_used,
        "search_hits": len(results),
    }

# Run the incomplete questions
new_results = {}
for qid in INCOMPLETE_QIDS:
    if qid in questions:
        result = rerun_question(questions[qid])
        new_results[qid] = result
        time.sleep(1)  # Brief pause between calls

# Update the results file
print(f"\n\nUpdating results file...")
updated_results = []
for record in existing_results:
    qid = record["question_id"]
    if qid in new_results:
        # Replace with new result
        updated_results.append(new_results[qid])
        print(f"  Updated {qid}: {len(record['response'])} → {len(new_results[qid]['response'])} chars")
    else:
        # Keep existing
        updated_results.append(record)

# Write back to file
with open(RESULTS_FILE, "w", encoding="utf-8") as f:
    for record in updated_results:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

print(f"\n✓ Updated {RESULTS_FILE}")
print("\nSummary:")
for qid in INCOMPLETE_QIDS:
    if qid in new_results:
        old_len = next(r["response"] for r in existing_results if r["question_id"] == qid)
        new_len = new_results[qid]["response"]
        print(f"  {qid}: {len(old_len) if old_len else 0} → {len(new_len) if new_len else 0} chars")
