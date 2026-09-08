from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import html
import json
import os
import random
import statistics
import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .core import (
    DEFAULT_BENCHMARK, DEFAULT_KB, DEFAULT_PLATFORM_CONFIG, DIMENSIONS,
    KBRetriever, answer_user_prompt, append_jsonl, call_chat, iter_jsonl,
    json_dump, json_load, judge_user_prompt, load_benchmark, parse_json_object,
    optimized_answer_system_prompt, read_prompt, sha256_file, sha256_tree, stable_hash, validate_inputs,
    validate_judge_payload,
)


RUNS_DIR = Path(__file__).parent / "runs"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Six-platform paired KB A/B evaluation")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("validate", "answers", "judge", "report"):
        x = sub.add_parser(name)
        x.add_argument("--kb", type=Path, default=DEFAULT_KB)
        x.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
        if name != "validate":
            x.add_argument("--run-id", required=True)
        if name == "answers":
            x.add_argument("--platform-config", type=Path, default=DEFAULT_PLATFORM_CONFIG)
            x.add_argument("--platforms", nargs="+")
            x.add_argument("--limit", type=int)
            x.add_argument("--question-ids", nargs="+")
            x.add_argument("--top-k", type=int, default=8)
            x.add_argument("--workers", type=int, default=6)
            x.add_argument("--resume", action="store_true")
        if name == "judge":
            x.add_argument("--workers", type=int, default=4)
            x.add_argument("--resume", action="store_true")
            x.add_argument("--judge-base-url", default=os.environ.get("JUDGE_BASE_URL"))
            x.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "gpt-5.6-sol"))
            x.add_argument("--judge-api-key-env", default="JUDGE_API_KEY")
    return p


def run_dir(run_id: str) -> Path:
    if not all(c.isalnum() or c in "-_" for c in run_id):
        raise ValueError("run-id may contain only letters, digits, hyphen, and underscore")
    return RUNS_DIR / run_id


def select_questions(benchmark: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    questions = benchmark["questions"]
    if args.question_ids:
        wanted = set(args.question_ids)
        questions = [x for x in questions if x["question_id"] in wanted]
        missing = wanted - {x["question_id"] for x in questions}
        if missing:
            raise ValueError(f"unknown question IDs: {sorted(missing)}")
    if args.limit:
        questions = questions[: args.limit]
    return questions


def command_validate(args: argparse.Namespace) -> None:
    result = validate_inputs(args.kb, args.benchmark)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        raise SystemExit(1)


def command_answers(args: argparse.Namespace) -> None:
    validation = validate_inputs(args.kb, args.benchmark)
    if not validation["ok"]:
        raise ValueError(f'input validation failed: {validation["issues"]}')
    benchmark = load_benchmark(args.benchmark)
    retriever = KBRetriever(args.kb)
    configs = json_load(args.platform_config)
    selected_platforms = args.platforms or list(configs)
    unknown = set(selected_platforms) - set(configs)
    if unknown:
        raise ValueError(f"unknown platforms: {sorted(unknown)}")
    questions = select_questions(benchmark, args)
    output_dir = run_dir(args.run_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    answer_path = output_dir / "answers.jsonl"
    done = set()
    if args.resume:
        done = {(x["platform"], x["questionId"], x["arm"]) for x in iter_jsonl(answer_path) if x.get("status") == "COMPLETED"}
    elif answer_path.exists():
        raise FileExistsError(f"{answer_path} exists; use --resume or a new run-id")

    baseline_system = read_prompt("baseline_system_zh.md")
    optimized_system = optimized_answer_system_prompt(benchmark)
    manifest = {
        "schemaVersion": "1.0", "runId": args.run_id,
        "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "studyDesign": "paired-baseline-vs-prompt-rubric-reviewed-kb-redline-bundle",
        "platforms": {key: {k: v for k, v in configs[key].items() if k != "api_key"} for key in selected_platforms},
        "questionIds": [x["question_id"] for x in questions],
        "arms": ["baseline", "optimized"], "topK": args.top_k,
        "baselineSystemPromptSha256": stable_hash(baseline_system),
        "optimizedSystemPromptSha256": stable_hash(optimized_system),
        "platformConfigSha256": stable_hash(json.dumps(
            {key: configs[key] for key in selected_platforms}, sort_keys=True, ensure_ascii=False
        )),
        "benchmarkSha256": sha256_file(args.benchmark), "kbSha256": sha256_tree(args.kb),
        "benchmarkPath": str(args.benchmark.resolve()), "kbPath": str(args.kb.resolve()),
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists() and args.resume:
        old = json_load(manifest_path)
        for key in ("benchmarkSha256", "kbSha256", "baselineSystemPromptSha256", "optimizedSystemPromptSha256", "questionIds", "topK"):
            if old.get(key) != manifest.get(key):
                raise ValueError(f"resume manifest mismatch: {key}")
        manifest = old
    else:
        json_dump(manifest_path, manifest)

    tasks = []
    for question in questions:
        retrieval_query = " ".join(
            [question["cancer_type"], question["question_type"], question["question"]]
        )
        hits = retriever.search(retrieval_query, top_k=args.top_k)
        for platform in selected_platforms:
            # Stable crossover: half of pairs call KB first, half baseline first.
            arms = ["optimized", "baseline"] if int(stable_hash(platform + question["question_id"])[0], 16) % 2 else ["baseline", "optimized"]
            for order, arm in enumerate(arms):
                if (platform, question["question_id"], arm) not in done:
                    tasks.append((platform, configs[platform], question, hits, arm, order))

    def execute(task: tuple[Any, ...]) -> dict[str, Any]:
        platform, config, question, hits, arm, order = task
        used_hits = hits if arm == "optimized" else None
        system_prompt = optimized_system if arm == "optimized" else baseline_system
        user_prompt = answer_user_prompt(question, used_hits)
        base = {
            "platform": platform, "platformDisplayName": config.get("display_name", platform),
            "model": config["model"], "questionId": question["question_id"],
            "question": question["question"], "cancerType": question["cancer_type"],
            "questionType": question["question_type"], "arm": arm, "pairOrder": order,
            "retrieval": hits if arm == "optimized" else [],
            "systemPromptSha256": stable_hash(system_prompt), "userPromptSha256": stable_hash(user_prompt),
            "startedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        try:
            answer, response_id, elapsed = call_chat(config, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ])
            if not answer.strip():
                raise ValueError("empty answer")
            base.update({"status": "COMPLETED", "answer": answer, "providerResponseId": response_id, "elapsedSeconds": elapsed, "error": None})
        except Exception as error:
            base.update({"status": "FAILED", "answer": None, "providerResponseId": None, "elapsedSeconds": None, "error": f"{type(error).__name__}: {error}"})
        append_jsonl(answer_path, base)
        return base

    print(f"Answer calls scheduled: {len(tasks)}", flush=True)
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute, task) for task in tasks]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            failures += result["status"] != "COMPLETED"
            if index % 10 == 0 or index == len(futures):
                print(f"answers {index}/{len(futures)} failures={failures}", flush=True)
    print(f"Written {answer_path}")


def command_judge(args: argparse.Namespace) -> None:
    output_dir = run_dir(args.run_id)
    answers_path = output_dir / "answers.jsonl"
    if not answers_path.exists():
        raise FileNotFoundError(answers_path)
    if not args.judge_base_url:
        raise ValueError("set JUDGE_BASE_URL or pass --judge-base-url")
    if not os.environ.get(args.judge_api_key_env, "").strip():
        raise ValueError(f"set {args.judge_api_key_env}")
    benchmark = load_benchmark(args.benchmark)
    qmap = {x["question_id"]: x for x in benchmark["questions"]}
    retriever = KBRetriever(args.kb)
    judge_system = read_prompt("judge_system_zh.md")
    score_path = output_dir / "scores.jsonl"
    if score_path.exists() and not args.resume:
        raise FileExistsError(f"{score_path} exists; use --resume or a new run-id")
    done = set()
    if args.resume:
        done = {(x["platform"], x["questionId"], x["arm"]) for x in iter_jsonl(score_path) if x.get("status") == "COMPLETED"}
    answers = [x for x in iter_jsonl(answers_path) if x.get("status") == "COMPLETED" and (x["platform"], x["questionId"], x["arm"]) not in done]
    judge_config = {
        "base_url": args.judge_base_url, "api_key_env": args.judge_api_key_env,
        "model": args.judge_model, "temperature": 0, "max_tokens": 3500,
    }

    def execute(answer_row: dict[str, Any]) -> dict[str, Any]:
        question = qmap[answer_row["questionId"]]
        supplemental = retriever.search(
            " ".join([
                question["cancer_type"], question["question_type"],
                question["question"], answer_row["answer"],
            ]),
            top_k=12,
            max_chars=14000,
        )
        evidence_by_id = {x["node_id"]: x for x in answer_row.get("retrieval", [])}
        for item in supplemental:
            evidence_by_id.setdefault(item["node_id"], item)
        evidence = list(evidence_by_id.values())[:16]
        prompt = judge_user_prompt(benchmark, question, answer_row["answer"], evidence)
        base = {
            "platform": answer_row["platform"], "model": answer_row["model"],
            "questionId": answer_row["questionId"], "arm": answer_row["arm"],
            "judgeModel": args.judge_model, "judgeSystemPromptSha256": stable_hash(judge_system),
        }
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                raw, response_id, elapsed = call_chat(judge_config, [
                    {"role": "system", "content": judge_system},
                    {"role": "user", "content": prompt},
                ], json_mode=True)
                scoring = validate_judge_payload(parse_json_object(raw), question)
                base.update({"status": "COMPLETED", "scoring": scoring, "providerResponseId": response_id, "elapsedSeconds": elapsed, "rawJudgeOutput": raw, "error": None})
                append_jsonl(score_path, base)
                return base
            except Exception as error:
                last_error = error
        base.update({"status": "FAILED", "scoring": None, "providerResponseId": None, "elapsedSeconds": None, "rawJudgeOutput": None, "error": f"{type(last_error).__name__}: {last_error}"})
        append_jsonl(score_path, base)
        return base

    print(f"Judge calls scheduled: {len(answers)}", flush=True)
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(execute, row) for row in answers]
        for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            failures += result["status"] != "COMPLETED"
            if index % 10 == 0 or index == len(futures):
                print(f"scores {index}/{len(futures)} failures={failures}", flush=True)
    print(f"Written {score_path}")


def mean(values: list[float]) -> float | None:
    return round(statistics.mean(values), 1) if values else None


def command_report(args: argparse.Namespace) -> None:
    output_dir = run_dir(args.run_id)
    manifest = json_load(output_dir / "manifest.json")
    benchmark = load_benchmark(args.benchmark)
    qmap = {x["question_id"]: x for x in benchmark["questions"]}
    answers = list(iter_jsonl(output_dir / "answers.jsonl"))
    scores = list(iter_jsonl(output_dir / "scores.jsonl"))
    # Last record wins, supporting resumed calls without destructive rewrites.
    answer_map = {(x["platform"], x["questionId"], x["arm"]): x for x in answers}
    score_map = {(x["platform"], x["questionId"], x["arm"]): x for x in scores}
    platforms = list(manifest["platforms"])
    pairs = []
    for platform in platforms:
        for qid in manifest["questionIds"]:
            before_a = answer_map.get((platform, qid, "baseline"))
            after_a = answer_map.get((platform, qid, "optimized"))
            before_s = score_map.get((platform, qid, "baseline"))
            after_s = score_map.get((platform, qid, "optimized"))
            complete = all(x and x.get("status") == "COMPLETED" for x in (before_a, after_a, before_s, after_s))
            pair: dict[str, Any] = {
                "platform": platform, "questionId": qid,
                "question": qmap[qid]["question"], "cancerType": qmap[qid]["cancer_type"],
                "questionType": qmap[qid]["question_type"],
                "questionRubric": qmap[qid]["rubrics"], "completePair": complete,
                "baseline": {"answer": before_a, "score": before_s},
                "optimized": {"answer": after_a, "score": after_s},
            }
            if complete:
                bs, ks = before_s["scoring"], after_s["scoring"]
                delta = round(ks["totalScore"] - bs["totalScore"], 1)
                pair["difference"] = {
                    "totalScoreDelta": delta,
                    "outcome": "improved" if delta > 0 else "regressed" if delta < 0 else "tied",
                    "passChanged": f'{bs["passed"]}->{ks["passed"]}',
                    "dimensionDeltas": {code: ks["dimensions"][code]["score"] - bs["dimensions"][code]["score"] for code in DIMENSIONS},
                    "redLineCountDelta": len(ks["redLines"]) - len(bs["redLines"]),
                    "answerLengthDelta": len(after_a["answer"]) - len(before_a["answer"]),
                    "latencyDeltaSeconds": round(after_a["elapsedSeconds"] - before_a["elapsedSeconds"], 3),
                }
            else:
                pair["difference"] = None
            pairs.append(pair)
    paired_path = output_dir / "paired_results.jsonl"
    paired_path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in pairs), encoding="utf-8")

    summary = []
    for platform in platforms:
        complete = [x for x in pairs if x["platform"] == platform and x["completePair"]]
        baseline_scores = [x["baseline"]["score"]["scoring"]["totalScore"] for x in complete]
        kb_scores = [x["optimized"]["score"]["scoring"]["totalScore"] for x in complete]
        deltas = [x["difference"]["totalScoreDelta"] for x in complete]
        outcome = Counter(x["difference"]["outcome"] for x in complete)
        before_pass = sum(x["baseline"]["score"]["scoring"]["passed"] for x in complete)
        after_pass = sum(x["optimized"]["score"]["scoring"]["passed"] for x in complete)
        summary.append({
            "platform": platform, "model": manifest["platforms"][platform]["model"],
            "expectedPairs": len(manifest["questionIds"]), "completePairs": len(complete),
            "baselineMean": mean(baseline_scores), "optimizedMean": mean(kb_scores), "meanDelta": mean(deltas),
            "improved": outcome["improved"], "tied": outcome["tied"], "regressed": outcome["regressed"],
            "baselinePassRate": round(before_pass / len(complete) * 100, 1) if complete else None,
            "optimizedPassRate": round(after_pass / len(complete) * 100, 1) if complete else None,
            "passRateDeltaPctPoints": round((after_pass - before_pass) / len(complete) * 100, 1) if complete else None,
            **{f"{code}MeanDelta": mean([x["difference"]["dimensionDeltas"][code] for x in complete]) for code in DIMENSIONS},
        })
    with (output_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary[0].keys()) if summary else ["platform"])
        writer.writeheader(); writer.writerows(summary)
    result = {
        "schemaVersion": "1.0", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "manifest": manifest, "rubric": {k: benchmark[k] for k in ("title", "version", "dimensions", "red_lines", "scoring_rules")},
        "summaryByPlatform": summary, "pairedResults": pairs,
    }
    json_dump(output_dir / "result.json", result)
    write_html(output_dir / "report.html", result)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Written {output_dir / 'result.json'}")


def write_html(path: Path, result: dict[str, Any]) -> None:
    esc = lambda x: html.escape(str(x if x is not None else "—"))
    rows = "".join(
        "<tr>" + "".join(f"<td>{esc(row.get(k))}</td>" for k in (
            "platform", "model", "completePairs", "baselineMean", "optimizedMean", "meanDelta",
            "improved", "tied", "regressed", "baselinePassRate", "optimizedPassRate", "passRateDeltaPctPoints",
        )) + "</tr>" for row in result["summaryByPlatform"]
    )
    details = []
    for pair in result["pairedResults"]:
        if not pair["completePair"]:
            continue
        before = pair["baseline"]; after = pair["optimized"]; diff = pair["difference"]
        rubrics = {x["dimension"]: x["rubric"] for x in pair["questionRubric"]}
        dim_rows = "".join(
            f"<tr><td>{code}</td><td>{before['score']['scoring']['dimensions'][code]['score']}</td>"
            f"<td>{after['score']['scoring']['dimensions'][code]['score']}</td><td>{diff['dimensionDeltas'][code]:+.1f}</td>"
            f"<td>{esc(rubrics[code])}</td><td>{esc(after['score']['scoring']['dimensions'][code]['reason'])}</td></tr>" for code in DIMENSIONS
        )
        details.append(f"""<details><summary>{esc(pair['platform'])} · {esc(pair['questionId'])} · Δ {diff['totalScoreDelta']:+.1f} · {esc(pair['question'])}</summary>
        <div class="grid"><section><h4>Before / baseline ({before['score']['scoring']['totalScore']})</h4><pre>{esc(before['answer']['answer'])}</pre></section>
        <section><h4>After / Prompt + Rubric + KB + Red lines ({after['score']['scoring']['totalScore']})</h4><pre>{esc(after['answer']['answer'])}</pre></section></div>
        <h4>Rubric checks and score differences</h4><table><thead><tr><th>维度</th><th>Before</th><th>After</th><th>Δ</th><th>本题详细 Rubric</th><th>优化臂裁判理由</th></tr></thead><tbody>{dim_rows}</tbody></table>
        <p><b>Question check:</b> {esc(json.dumps(after['score']['scoring']['questionCheck'], ensure_ascii=False))}</p>
        <p><b>Retrieved KB nodes:</b> {esc(', '.join(x['node_id'] for x in after['answer']['retrieval']))}</p></details>""")
    path.write_text(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>KB A/B Evaluation</title>
    <style>body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:28px;color:#172033}}h1{{color:#0f766e}}table{{border-collapse:collapse;width:100%;margin:12px 0}}th,td{{border-bottom:1px solid #d9e2e7;padding:8px;text-align:left;vertical-align:top}}th{{background:#e7f5f2}}details{{margin:14px 0;border:1px solid #d9e2e7;border-radius:8px;padding:12px}}summary{{font-weight:650;cursor:pointer}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}pre{{white-space:pre-wrap;background:#f7f9fa;padding:12px;border-radius:6px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style>
    <body><h1>骨转移 Prompt + Rubric + Reviewed KB + Red lines 六平台配对评测</h1><p>Run: {esc(result['manifest']['runId'])}｜Rubric: {esc(result['rubric']['version'])}</p>
    <h2>平台汇总</h2><table><thead><tr><th>平台</th><th>模型</th><th>完整配对</th><th>Baseline均分</th><th>Optimized均分</th><th>均值Δ</th><th>提升</th><th>持平</th><th>回退</th><th>Baseline通过率%</th><th>Optimized通过率%</th><th>通过率Δpp</th></tr></thead><tbody>{rows}</tbody></table>
    <h2>逐题回答、评分、检查与差值</h2>{''.join(details)}</body></html>""", encoding="utf-8")


def main() -> None:
    args = parser().parse_args()
    try:
        {"validate": command_validate, "answers": command_answers, "judge": command_judge, "report": command_report}[args.command](args)
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}", file=sys.stderr)
        if os.environ.get("KB_AB_DEBUG") == "1":
            traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    main()
