#!/usr/bin/env python3
"""Score a GEO question/response export against the 246-question v6 rubric."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import os
import pathlib
import threading
import time
from collections import Counter, defaultdict
from typing import Any

from openai import OpenAI


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "final_testing_KB" / "默认题库评测（共_246_题）_搜索日志 (1).json"
DEFAULT_RUBRIC = ROOT / "model_response" / "question_list_all_new_rubric.jsonl"
DEFAULT_WORK_DIR = ROOT / "final_testing_KB" / ".scoring_cache_20260827"
DEFAULT_OUTPUT = ROOT / "final_testing_KB" / "默认题库评测（共_246_题）_问答及六维评分详情_20260827.json"
BASE_URL = os.environ.get("JUDGE_BASE_URL", "https://api.openai.com/v1")
JUDGE_MODEL = "gpt-5.6-sol"
SCORE_VALUES = {0, 0.5, 1}
DIMENSIONS = ["D1", "D2", "D3", "D4", "D5", "D6"]
RED_LINE_CODES = {"R1", "R2", "R3", "R4", "R5"}

_thread_local = threading.local()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=pathlib.Path, default=DEFAULT_SOURCE)
    parser.add_argument("--rubric", type=pathlib.Path, default=DEFAULT_RUBRIC)
    parser.add_argument("--work-dir", type=pathlib.Path, default=DEFAULT_WORK_DIR)
    parser.add_argument("--output", type=pathlib.Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--merge-only", action="store_true")
    return parser.parse_args()


def load_json(path: pathlib.Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def load_rubric(path: pathlib.Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                records.append(json.loads(line))
    if not records or records[0].get("record_type") != "general_rubric":
        raise ValueError("Rubric JSONL is missing the general rubric header")
    questions = {row["question"]: row for row in records[1:]}
    if len(questions) != 246:
        raise ValueError(f"Expected 246 question rubrics, found {len(questions)}")
    return records[0], questions


def api_key() -> str:
    value = os.environ.get("OPENAI_API_KEY", "").strip()
    if value:
        return value
    raise RuntimeError("Set OPENAI_API_KEY (and optionally JUDGE_BASE_URL) in the environment")


def client() -> OpenAI:
    instance = getattr(_thread_local, "client", None)
    if instance is None:
        instance = OpenAI(api_key=api_key(), base_url=BASE_URL, timeout=240, max_retries=2)
        _thread_local.client = instance
    return instance


def response_schema(model_names: list[str]) -> dict[str, Any]:
    dimension_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code", "score", "reason"],
        "properties": {
            "code": {"type": "string", "enum": DIMENSIONS},
            "score": {"type": "number", "enum": [0, 0.5, 1]},
            "reason": {"type": "string", "minLength": 1},
        },
    }
    score_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["model", "dimensions", "redLines", "summary", "mainFlaw", "fabrications"],
        "properties": {
            "model": {"type": "string", "enum": model_names},
            "dimensions": {
                "type": "array",
                "minItems": 6,
                "maxItems": 6,
                "items": dimension_item,
            },
            "redLines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["code", "evidence"],
                    "properties": {
                        "code": {"type": "string", "enum": sorted(RED_LINE_CODES)},
                        "evidence": {"type": "string", "minLength": 1},
                    },
                },
            },
            "summary": {"type": "string", "minLength": 1},
            "mainFlaw": {"type": "string"},
            "fabrications": {"type": ["string", "null"]},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["scores"],
        "properties": {
            "scores": {
                "type": "array",
                "minItems": len(model_names),
                "maxItems": len(model_names),
                "items": score_item,
            }
        },
    }


def system_prompt(general: dict[str, Any]) -> str:
    dimensions = "\n".join(
        f'{item["dimension"]} {item["name"]}: {item["definition"]}\n'
        f'1分: {item["score_1"]}\n0.5分: {item["score_0.5"]}\n0分: {item["score_0"]}'
        for item in general["dimensions"]
    )
    red_lines = "\n".join(
        f'{item["code"]} {item["name"]} ({item["applicability"]})\n'
        f'触发: {item["trigger_condition"]}\n不触发: {item["non_trigger_condition"]}'
        for item in general["red_lines"]
    )
    rules = general["scoring_rules"]
    return f"""你是严谨的医学问答质量裁判。只能评价给定回答，不得回答原问题。
每个回答必须独立评分，不得因为同题其他模型回答更好或更差而改变分数。
第一优先级是本题逐维Rubric；下面通用标准只用于Rubric未覆盖处和红线判断。
逐维分数只能取1、0.5、0。逐点核对Rubric锚点，理由须指出命中或漏掉的具体要点。
D5不要求回答写来源；无编造可得1。具体数字或归因无法确认时为0.5，确认错误或虚构为0。
R3和R5先判断题干门槛，不满足门槛绝不触发。红线必须给回答原文短引或准确概括作为证据。

六维通用标准:
{dimensions}

红线:
{red_lines}

计分规则:
总分: {rules["总分"]}
通过: {rules["通过"]}
D5与R4: {rules["D5与R4"]}
R3/R5: {rules["R3/R5"]}

输出要求: 每个模型都返回D1-D6且各出现一次。理由简洁具体。无明显缺陷时mainFlaw为空字符串；无编造或存疑内容时fabrications为null。"""


def user_prompt(question: dict[str, Any], rubric: dict[str, Any], completed: list[dict[str, Any]]) -> str:
    rubric_text = "\n".join(
        f'{item["dimension"]} {item["dimension_name"]} ({item["applicable"]}): {item["rubric"]}'
        for item in rubric["rubrics"]
    )
    answers = "\n\n".join(
        f'===== MODEL: {item["model"]} =====\n{item["modelAnswer"]["answer"]}'
        for item in completed
    )
    return f"""题号: {rubric["question_id"]}
癌种/场景: {rubric["cancer_type"]}
问题类型: {rubric["question_type"]}
原问题: {question["question"]}

本题逐维Rubric:
{rubric_text}

待评分回答:
{answers}"""


def extract_output(response: Any) -> tuple[str, str]:
    text = getattr(response, "output_text", "") or ""
    if not text:
        raise ValueError("Judge returned no output_text")
    return text, str(getattr(response, "id", "") or "")


def validate_score_payload(payload: dict[str, Any], model_names: list[str]) -> list[dict[str, Any]]:
    scores = payload.get("scores")
    if not isinstance(scores, list) or len(scores) != len(model_names):
        raise ValueError("Judge output has the wrong number of model scores")
    by_model = {item.get("model"): item for item in scores if isinstance(item, dict)}
    if set(by_model) != set(model_names):
        raise ValueError("Judge output model names do not match the completed responses")
    validated = []
    for model in model_names:
        item = by_model[model]
        dimensions = item.get("dimensions")
        if not isinstance(dimensions, list) or len(dimensions) != 6:
            raise ValueError(f"{model}: expected six dimensions")
        dimension_map = {dim.get("code"): dim for dim in dimensions if isinstance(dim, dict)}
        if set(dimension_map) != set(DIMENSIONS):
            raise ValueError(f"{model}: dimension codes are incomplete or duplicated")
        normalized_dimensions = {}
        for code in DIMENSIONS:
            dim = dimension_map[code]
            score = dim.get("score")
            reason = str(dim.get("reason", "")).strip()
            if score not in SCORE_VALUES or not reason:
                raise ValueError(f"{model}/{code}: invalid score or empty reason")
            normalized_dimensions[code] = {"score": score, "reason": reason}
        red_lines = item.get("redLines")
        if not isinstance(red_lines, list):
            raise ValueError(f"{model}: redLines must be an array")
        normalized_red_lines = []
        for red_line in red_lines:
            code = red_line.get("code")
            evidence = str(red_line.get("evidence", "")).strip()
            if code not in RED_LINE_CODES or not evidence:
                raise ValueError(f"{model}: invalid red-line item")
            normalized_red_lines.append({"code": code, "evidence": evidence})
        total = round(sum(normalized_dimensions[code]["score"] for code in DIMENSIONS) / 6 * 100, 1)
        passed = (
            not normalized_red_lines
            and normalized_dimensions["D2"]["score"] == 1
            and normalized_dimensions["D4"]["score"] == 1
            and total >= 80
        )
        summary = str(item.get("summary", "")).strip()
        if not summary:
            raise ValueError(f"{model}: empty summary")
        fabrication_value = item.get("fabrications")
        if fabrication_value is not None:
            fabrication_value = str(fabrication_value).strip() or None
        validated.append({
            "model": model,
            "status": "COMPLETED",
            "judgeModel": JUDGE_MODEL,
            "dimensions": normalized_dimensions,
            "totalScore": total,
            "passed": passed,
            "redLines": normalized_red_lines,
            "summary": summary,
            "mainFlaw": str(item.get("mainFlaw", "")).strip(),
            "fabrications": fabrication_value,
        })
    return validated


def score_question(
    question: dict[str, Any],
    rubric: dict[str, Any],
    general: dict[str, Any],
    output_path: pathlib.Path,
) -> dict[str, Any]:
    completed = [
        result for result in question["modelResults"]
        if result.get("modelAnswer", {}).get("status") == "COMPLETED"
        and str(result.get("modelAnswer", {}).get("answer") or "").strip()
    ]
    model_names = [item["model"] for item in completed]
    if not model_names:
        raise ValueError(f'{rubric["question_id"]}: no completed answers')
    last_error = None
    for attempt in range(1, 4):
        try:
            response = client().responses.create(
                model=JUDGE_MODEL,
                instructions=system_prompt(general),
                input=user_prompt(question, rubric, completed),
                max_output_tokens=12000,
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "question_scores",
                        "strict": True,
                        "schema": response_schema(model_names),
                    }
                },
            )
            raw_text, response_id = extract_output(response)
            scores = validate_score_payload(json.loads(raw_text), model_names)
            result = {
                "questionId": rubric["question_id"],
                "sequence": question["sequence"],
                "providerResponseId": response_id,
                "scores": scores,
            }
            write_json(output_path, result)
            return result
        except Exception as error:  # Retries cover transient API and malformed output failures.
            last_error = error
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError(f'{rubric["question_id"]}: scoring failed after 3 attempts: {last_error}')


def merge_output(
    source: dict[str, Any],
    general: dict[str, Any],
    rubric_by_question: dict[str, dict[str, Any]],
    work_dir: pathlib.Path,
    output: pathlib.Path,
) -> dict[str, Any]:
    combined = copy.deepcopy(source)
    score_totals: dict[str, list[float]] = defaultdict(list)
    pass_counts: Counter[str] = Counter()
    red_line_counts: Counter[str] = Counter()
    completed_counts: Counter[str] = Counter()
    unscored_counts: Counter[str] = Counter()

    for question in combined["questions"]:
        rubric = rubric_by_question[question["question"]]
        score_file = work_dir / f'{rubric["question_id"]}.json'
        scored = load_json(score_file)
        score_by_model = {item["model"]: item for item in scored["scores"]}
        question["questionId"] = rubric["question_id"]
        question["cancerType"] = rubric["cancer_type"]
        question["questionType"] = rubric["question_type"]
        question["scoringRubric"] = rubric["rubrics"]
        for model_result in question["modelResults"]:
            model = model_result["model"]
            if model in score_by_model:
                score = score_by_model[model]
                model_result["scoring"] = score
                model_result["scoring"]["providerResponseId"] = scored["providerResponseId"]
                completed_counts[model] += 1
                score_totals[model].append(score["totalScore"])
                if score["passed"]:
                    pass_counts[model] += 1
                red_line_counts[model] += len(score["redLines"])
            else:
                answer = model_result.get("modelAnswer", {})
                model_result["scoring"] = {
                    "model": model,
                    "status": "NOT_SCORED",
                    "judgeModel": None,
                    "dimensions": None,
                    "totalScore": None,
                    "passed": None,
                    "redLines": [],
                    "summary": "模型调用未完成且无回答文本，无法进行医学质量评分。",
                    "mainFlaw": "无可评分回答",
                    "fabrications": None,
                    "sourceStatus": answer.get("status"),
                    "sourceErrorMessage": answer.get("errorMessage"),
                }
                unscored_counts[model] += 1

    model_names = sorted(set(completed_counts) | set(unscored_counts))
    combined["schemaVersion"] = "2.0-scored"
    combined["scoring"] = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "judgeModel": JUDGE_MODEL,
        "rubricTitle": general["title"],
        "rubricVersion": general["version"],
        "dimensions": general["dimensions"],
        "redLines": general["red_lines"],
        "scoringRules": general["scoring_rules"],
        "summaryByModel": [
            {
                "model": model,
                "completedResponsesScored": completed_counts[model],
                "failedResponsesNotScored": unscored_counts[model],
                "averageScore": round(sum(score_totals[model]) / len(score_totals[model]), 1)
                if score_totals[model] else None,
                "passed": pass_counts[model],
                "passRateAmongScored": round(pass_counts[model] / completed_counts[model] * 100, 1)
                if completed_counts[model] else None,
                "redLineTriggers": red_line_counts[model],
            }
            for model in model_names
        ],
        "totalCompletedResponsesScored": sum(completed_counts.values()),
        "totalFailedResponsesNotScored": sum(unscored_counts.values()),
    }
    write_json(output, combined)
    return combined


def main() -> None:
    args = parse_args()
    source = load_json(args.source)
    general, rubric_by_question = load_rubric(args.rubric)
    questions = source.get("questions")
    if not isinstance(questions, list) or len(questions) != 246:
        raise ValueError(f"Expected 246 source questions, found {len(questions) if isinstance(questions, list) else 'invalid'}")
    missing = [item["sequence"] for item in questions if item["question"] not in rubric_by_question]
    if missing:
        raise ValueError(f"Questions without exact rubric matches: {missing}")
    args.work_dir.mkdir(parents=True, exist_ok=True)

    selected = questions[: args.limit] if args.limit else questions
    todo = []
    for question in selected:
        rubric = rubric_by_question[question["question"]]
        path = args.work_dir / f'{rubric["question_id"]}.json'
        if args.resume and path.exists():
            continue
        todo.append((question, rubric, path))

    if not args.merge_only and todo:
        print(f"Scoring {len(todo)} questions with {args.workers} workers", flush=True)
        failures = []
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(score_question, question, rubric, general, path): rubric["question_id"]
                for question, rubric, path in todo
            }
            for future in concurrent.futures.as_completed(futures):
                qid = futures[future]
                try:
                    future.result()
                except Exception as error:
                    failures.append(str(error))
                    print(f"ERROR {qid}: {error}", flush=True)
                completed += 1
                if completed % 10 == 0 or completed == len(todo):
                    print(f"Progress {completed}/{len(todo)}; failures={len(failures)}", flush=True)
        if failures:
            raise RuntimeError(f"{len(failures)} question(s) failed; rerun with --resume")

    if args.limit:
        print("Limit mode completed; final merge skipped", flush=True)
        return
    expected = {f'Q{index:03d}.json' for index in range(1, 247)}
    actual = {path.name for path in args.work_dir.glob("Q*.json")}
    missing_score_files = sorted(expected - actual)
    if missing_score_files:
        raise RuntimeError(f"Cannot merge; missing {len(missing_score_files)} score files")
    combined = merge_output(source, general, rubric_by_question, args.work_dir, args.output)
    print(json.dumps(combined["scoring"], ensure_ascii=False, indent=2), flush=True)
    print(f"Written: {args.output}", flush=True)


if __name__ == "__main__":
    main()
