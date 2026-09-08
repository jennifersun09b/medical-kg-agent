#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_eval.py — Turn a model-evaluation export into a defect profile.

Reads an evaluation export (questions x models, each scored on rubric dimensions
and red lines) and emits a deterministic aggregation of where the models fail.
The result drives risk-weighted KB planning: what the KB must cover is not just
"what sources exist" but "where the models measurably break".

This script does MECHANICAL aggregation only. Semantic clustering of the free-text
flaw descriptions is the agent's job (skill workflow step 3) — the script supplies
counts, keyword frequencies, and verbatim samples to make that judgement concrete.

Usage:
    python3 analyze_eval.py eval-export.json -o defect-profile.json
    python3 analyze_eval.py eval-export.json -o out.json --field-map custom-map.json
    python3 analyze_eval.py eval-export.json -o out.json --no-report

Outputs:
    <output>.json    machine-readable defect profile (feeds validate_kb.py --defects)
    缺陷图谱.md       human-readable version, written next to the output JSON

Exit codes:
    0 = analysis completed
    1 = usage / IO error
    2 = export parsed but contained no scored responses

No external dependencies required (pure Python stdlib).
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Field map — where things live inside an evaluation export.
#
# The default targets the GEO export schema. Override any subset via
# --field-map custom.json to adapt to a different evaluation system.
#
# Path syntax: dot-separated keys. "[]" means "iterate this list".
# --------------------------------------------------------------------------
DEFAULT_FIELD_MAP = {
    "questions": "questions[]",
    "question_id": "questionId",
    "question_text": "question",
    "question_type": "questionType",
    "question_group": "cancerType",
    "model_results": "modelResults[]",
    "model_id": "model",
    "scoring": "scoring",
    "scoring_status": "status",
    "scoring_ok_value": "COMPLETED",
    "dimensions": "dimensions",
    "dimension_score": "score",
    "dimension_reason": "reason",
    "total_score": "totalScore",
    "passed": "passed",
    "red_lines": "redLines",
    "red_line_code": "code",
    "red_line_evidence": "evidence",
    "main_flaw": "mainFlaw",
    "fabrications": "fabrications",
    # Metadata (optional, best-effort)
    "meta_task_name": "task.taskName",
    "meta_judge_model": "scoring.judgeModel",
    "meta_rubric_title": "scoring.rubricTitle",
    "meta_rubric_version": "scoring.rubricVersion",
    "meta_dimension_defs": "scoring.dimensions",
    "meta_red_line_defs": "scoring.redLines",
}

# Scores treated as a defect. Rubrics use 1 / 0.5 / 0.
FAIL_SCORE = 0
PARTIAL_SCORE = 0.5

# Topic risk banding, applied to the mean total score of a topic.
P0_MAX_SCORE = 52.0
P1_MAX_SCORE = 60.0

# Priority thresholds, as a share of ALL scored responses (one denominator for
# every cluster type, so the bands mean the same thing everywhere).
#
# Red lines sit lower because they are 一票否决: a red line firing on 5% of
# responses is a worse problem than a dimension scoring 0 on 5% of them.
REDLINE_P0_SHARE = 0.05
REDLINE_P1_SHARE = 0.01
DIMENSION_P0_SHARE = 0.20
DIMENSION_P1_SHARE = 0.05

# How many verbatim samples to keep per cluster. Enough for the agent to judge
# the cluster's real shape without bloating the profile.
SAMPLES_PER_CLUSTER = 6

# How many worst-scoring questions to carry in the profile / show in the report.
HOTSPOTS_IN_PROFILE = 60
HOTSPOTS_IN_REPORT = 25

# Fraction of the model fleet that must actually have been scored on a question
# before "every model failed it" is a claim worth making. Exports routinely carry
# unscored responses (timeouts, API errors); demanding a full house would let an
# infrastructure gap mask a genuine knowledge hole.
MIN_HOTSPOT_COVERAGE = 0.6

# Minimum length for a keyword token pulled from free-text flaw descriptions.
MIN_KEYWORD_LEN = 2

# Chinese stopwords that survive naive segmentation and carry no signal.
STOPWORDS = {
    "回答", "答案", "原文", "题干", "本题", "存在", "缺少", "缺失", "没有", "未能",
    "以及", "并且", "同时", "但是", "however", "具体", "相关", "可能", "无法",
    "进行", "给出", "提到", "说明", "描述", "表述", "内容", "信息", "问题",
    "一个", "部分", "其他", "这一", "该", "等", "的", "了", "和", "与", "或",
    "确认", "支持", "情况", "方面", "要求", "建议", "明确", "提示",
}


def die(msg, code=1):
    sys.stderr.write("ERROR: %s\n" % msg)
    sys.exit(code)


# --------------------------------------------------------------------------
# Path resolution
# --------------------------------------------------------------------------
def resolve_path(data, path):
    """Resolve a dot-separated path. A trailing '[]' returns the list itself.

    Returns None when any segment is missing, so callers can treat a missing
    optional field as absent rather than crashing on a foreign export.
    """
    if not path:
        return None
    node = data
    for raw_segment in path.split("."):
        segment = raw_segment[:-2] if raw_segment.endswith("[]") else raw_segment
        if not isinstance(node, dict) or segment not in node:
            return None
        node = node[segment]
    return node


def load_field_map(custom_path):
    """Merge a user-supplied field map over the built-in GEO defaults."""
    field_map = dict(DEFAULT_FIELD_MAP)
    if not custom_path:
        return field_map
    if not os.path.exists(custom_path):
        die("field map not found: %s" % custom_path)
    with open(custom_path, "r", encoding="utf-8") as handle:
        try:
            custom = json.load(handle)
        except ValueError as exc:
            die("field map is not valid JSON: %s" % exc)
    if not isinstance(custom, dict):
        die("field map must be a JSON object of {key: path}")
    unknown = set(custom) - set(DEFAULT_FIELD_MAP)
    if unknown:
        die("field map has unknown keys: %s" % ", ".join(sorted(unknown)))
    field_map.update(custom)
    return field_map


# --------------------------------------------------------------------------
# Keyword extraction from free-text flaw descriptions
# --------------------------------------------------------------------------
def extract_keywords(texts, top_n=25):
    """Frequency-count salient tokens across free-text flaw descriptions.

    Deliberately crude: CJK runs are windowed into 2- and 3-grams, latin/digit
    runs are taken whole. This surfaces recurring phrasing ("脊髓压迫", "延长生存")
    for the agent to cluster; it is not a substitute for reading the samples.
    """
    counts = Counter()
    for text in texts:
        if not text:
            continue
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", str(text)):
            lowered = token.lower()
            if lowered not in STOPWORDS:
                counts[lowered] += 1
        for run in re.findall(r"[一-鿿]{2,}", str(text)):
            for size in (2, 3):
                for start in range(len(run) - size + 1):
                    gram = run[start:start + size]
                    if len(gram) >= MIN_KEYWORD_LEN and gram not in STOPWORDS:
                        counts[gram] += 1
    # Drop n-grams fully contained in a more frequent longer n-gram; keeps
    # "脊髓压迫" and discards the "脊髓压" / "髓压迫" shadows it casts.
    ranked = [word for word, _ in counts.most_common(top_n * 8)]
    kept = []
    for word in ranked:
        covered = any(
            word != other and word in other and counts[other] >= counts[word]
            for other in kept
        )
        if not covered:
            kept.append(word)
        if len(kept) >= top_n:
            break
    return [{"keyword": word, "count": counts[word]} for word in kept]


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def iter_scored_responses(data, field_map):
    """Yield one normalized record per scored model response."""
    questions = resolve_path(data, field_map["questions"])
    if not isinstance(questions, list):
        die("could not find the questions list at path '%s' — "
            "pass --field-map to describe this export's layout"
            % field_map["questions"])

    ok_value = field_map["scoring_ok_value"]
    for question in questions:
        if not isinstance(question, dict):
            continue
        q_common = {
            "question_id": question.get(field_map["question_id"]),
            "question_text": question.get(field_map["question_text"]),
            "question_type": question.get(field_map["question_type"]) or "(未分类)",
            "question_group": question.get(field_map["question_group"]) or "(未分组)",
        }
        results = question.get(field_map["model_results"].rstrip("[]"))
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            scoring = result.get(field_map["scoring"])
            if not isinstance(scoring, dict):
                continue
            if ok_value and scoring.get(field_map["scoring_status"]) != ok_value:
                continue

            dimensions = {}
            raw_dimensions = scoring.get(field_map["dimensions"])
            if isinstance(raw_dimensions, dict):
                for code, payload in raw_dimensions.items():
                    if isinstance(payload, dict):
                        dimensions[code] = {
                            "score": payload.get(field_map["dimension_score"]),
                            "reason": payload.get(field_map["dimension_reason"]),
                        }
                    else:
                        dimensions[code] = {"score": payload, "reason": None}

            red_lines = []
            for entry in scoring.get(field_map["red_lines"]) or []:
                if isinstance(entry, dict):
                    red_lines.append({
                        "code": entry.get(field_map["red_line_code"]),
                        "evidence": entry.get(field_map["red_line_evidence"]),
                    })
                elif entry:
                    red_lines.append({"code": str(entry), "evidence": None})

            record = dict(q_common)
            record.update({
                "model": result.get(field_map["model_id"]) or scoring.get("model"),
                "dimensions": dimensions,
                "red_lines": red_lines,
                "total_score": scoring.get(field_map["total_score"]),
                "passed": scoring.get(field_map["passed"]),
                "main_flaw": scoring.get(field_map["main_flaw"]),
                "fabrications": scoring.get(field_map["fabrications"]),
            })
            yield record


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def build_profile(data, field_map, source_name):
    records = list(iter_scored_responses(data, field_map))
    if not records:
        die("export contained no scored responses — check --field-map "
            "(scoring_ok_value is currently '%s')" % field_map["scoring_ok_value"], 2)

    models = sorted({r["model"] for r in records if r["model"]})
    model_count = len(models)

    dimension_stats = defaultdict(lambda: Counter())
    dimension_fail_by_topic = defaultdict(lambda: Counter())
    redline_counts = Counter()
    redline_models = defaultdict(set)
    redline_samples = defaultdict(list)
    redline_questions = defaultdict(set)
    redline_topics = defaultdict(lambda: Counter())
    dimension_models = defaultdict(set)
    dimension_samples = defaultdict(list)
    dimension_questions = defaultdict(set)
    scores_by_topic = defaultdict(list)
    scores_by_group = defaultdict(list)
    scores_by_model = defaultdict(list)
    passed_by_model = Counter()
    main_flaws = []
    fabrications = []
    by_question = defaultdict(lambda: {
        "question": None, "type": None, "group": None,
        "scores": [], "models": set(), "passed": 0,
        "redlines": Counter(), "dimension_fails": Counter(), "flaws": [],
    })

    for record in records:
        topic = record["question_type"]
        model = record["model"]

        bucket = by_question[record["question_id"]]
        bucket["question"] = bucket["question"] or record["question_text"]
        bucket["type"] = bucket["type"] or topic
        bucket["group"] = bucket["group"] or record["question_group"]
        bucket["models"].add(model)
        if isinstance(record["total_score"], (int, float)):
            bucket["scores"].append(record["total_score"])
        if record["passed"]:
            bucket["passed"] += 1
        if record["main_flaw"]:
            bucket["flaws"].append(record["main_flaw"])

        for code, payload in record["dimensions"].items():
            score = payload.get("score")
            dimension_stats[code][_score_key(score)] += 1
            if score == FAIL_SCORE:
                dimension_fail_by_topic[code][topic] += 1
                dimension_models[code].add(model)
                dimension_questions[code].add(record["question_id"])
                bucket["dimension_fails"][code] += 1
                if len(dimension_samples[code]) < SAMPLES_PER_CLUSTER and payload.get("reason"):
                    dimension_samples[code].append({
                        "model": model,
                        "questionId": record["question_id"],
                        "text": _truncate(payload["reason"]),
                    })

        for red_line in record["red_lines"]:
            code = red_line.get("code") or "(未编码)"
            redline_counts[code] += 1
            redline_models[code].add(model)
            redline_questions[code].add(record["question_id"])
            redline_topics[code][topic] += 1
            bucket["redlines"][code] += 1
            if len(redline_samples[code]) < SAMPLES_PER_CLUSTER and red_line.get("evidence"):
                redline_samples[code].append({
                    "model": model,
                    "questionId": record["question_id"],
                    "text": _truncate(red_line["evidence"]),
                })

        if isinstance(record["total_score"], (int, float)):
            scores_by_topic[topic].append(record["total_score"])
            scores_by_group[record["question_group"]].append(record["total_score"])
            scores_by_model[model].append(record["total_score"])
        if record["passed"]:
            passed_by_model[model] += 1
        if record["main_flaw"]:
            main_flaws.append(record["main_flaw"])
        if record["fabrications"]:
            fabrications.append(record["fabrications"])

    clusters = []
    clusters.extend(_redline_clusters(
        redline_counts, redline_models, redline_samples, redline_questions,
        redline_topics, len(records), model_count,
        _definition_index(data, field_map, "meta_red_line_defs", "code")))
    clusters.extend(_dimension_clusters(
        dimension_stats, dimension_models, dimension_samples, dimension_questions,
        dimension_fail_by_topic, len(records), model_count,
        _definition_index(data, field_map, "meta_dimension_defs", "dimension")))
    clusters.sort(key=lambda c: ({"P0": 0, "P1": 1, "P2": 2}[c["priority"]], -c["hits"]))

    hotspots = _question_hotspots(by_question, model_count)

    profile = {
        "profileVersion": "1.0",
        "source": {
            "file": source_name,
            "taskName": resolve_path(data, field_map["meta_task_name"]),
            "judgeModel": resolve_path(data, field_map["meta_judge_model"]),
            "rubricTitle": resolve_path(data, field_map["meta_rubric_title"]),
            "rubricVersion": resolve_path(data, field_map["meta_rubric_version"]),
            "scoredResponses": len(records),
            "questions": len({r["question_id"] for r in records}),
            "models": models,
        },
        "model_stats": [
            {
                "model": model,
                "scored": len(scores_by_model[model]),
                "averageScore": _mean(scores_by_model[model]),
                "passed": passed_by_model[model],
                "passRate": round(passed_by_model[model] * 100.0 / len(scores_by_model[model]), 1)
                if scores_by_model[model] else 0.0,
            }
            for model in models
        ],
        "dimension_stats": {
            code: dict(sorted(counts.items(), key=lambda kv: kv[0], reverse=True))
            for code, counts in sorted(dimension_stats.items())
        },
        "redline_stats": dict(redline_counts.most_common()),
        "topic_risk": _topic_risk(scores_by_topic),
        "group_risk": _topic_risk(scores_by_group),
        "clusters": clusters,
        "question_hotspots": hotspots[:HOTSPOTS_IN_PROFILE],
        "hotspot_summary": {
            "totalQuestions": len(hotspots),
            "universalFailures": sum(1 for h in hotspots if h["universal"]),
            "carriedInProfile": min(len(hotspots), HOTSPOTS_IN_PROFILE),
        },
        "fabrication_targets": extract_keywords(fabrications),
        "flaw_keywords": extract_keywords(main_flaws),
        "samples": {
            "mainFlaw": [_truncate(t) for t in main_flaws[:SAMPLES_PER_CLUSTER * 2]],
            "fabrications": [_truncate(t) for t in fabrications[:SAMPLES_PER_CLUSTER * 2]],
        },
    }
    return profile


def _score_key(score):
    """Normalize a score into a stable string key for JSON output."""
    if score is None:
        return "N/A"
    if isinstance(score, float) and score.is_integer():
        return str(int(score))
    return str(score)


def _truncate(text, limit=220):
    flat = re.sub(r"\s+", " ", str(text)).strip()
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _mean(values):
    return round(sum(values) / len(values), 1) if values else None


def _definition_index(data, field_map, map_key, id_field):
    """Index the export's own rubric/red-line definitions by their code."""
    definitions = resolve_path(data, field_map[map_key])
    index = {}
    if isinstance(definitions, list):
        for item in definitions:
            if isinstance(item, dict) and item.get(id_field):
                index[item[id_field]] = item
    return index


def _priority(universal, share, kind):
    """Band a cluster into P0/P1/P2.

    `universal` (every model fails it) forces P0 regardless of volume: a defect
    no model escapes means the knowledge itself is missing or ambiguous in the
    public corpus — exactly what a KB exists to fix. A defect only one model has
    is a model-capability problem and ranks lower.

    `share` is always measured against ALL scored responses so the bands are
    comparable across cluster types; the thresholds then differ by severity.
    """
    if universal:
        return "P0"
    p0, p1 = (REDLINE_P0_SHARE, REDLINE_P1_SHARE) if kind == "redline" \
        else (DIMENSION_P0_SHARE, DIMENSION_P1_SHARE)
    if share >= p0:
        return "P0"
    if share >= p1:
        return "P1"
    return "P2"


def _redline_clusters(counts, models, samples, questions, topics,
                      total_records, model_count, definitions):
    clusters = []
    for code, hits in counts.most_common():
        affected = sorted(models[code])
        universal = model_count > 1 and len(affected) == model_count
        share = hits / float(total_records or 1)
        definition = definitions.get(code, {})
        clusters.append({
            "id": "DEF-%s" % code,
            "type": "redline",
            "code": code,
            "name": definition.get("name") or "红线 %s" % code,
            "definition": definition.get("trigger_condition"),
            "hits": hits,
            "share": round(share * 100, 1),
            "models_affected": affected,
            "universal": universal,
            "priority": _priority(universal, share, "redline"),
            "question_ids": sorted(q for q in questions[code] if q)[:40],
            "question_types": dict(topics[code].most_common(8)),
            "evidence_samples": samples[code],
        })
    return clusters


def _dimension_clusters(stats, models, samples, questions, fail_topics,
                        total_records, model_count, definitions):
    clusters = []
    for code in sorted(stats):
        fails = stats[code].get(_score_key(FAIL_SCORE), 0)
        if not fails:
            continue
        affected = sorted(models[code])
        universal = model_count > 1 and len(affected) == model_count
        share = fails / float(total_records or 1)
        definition = definitions.get(code, {})
        clusters.append({
            "id": "DEF-%s" % code,
            "type": "dimension",
            "code": code,
            "name": definition.get("name") or "维度 %s" % code,
            "definition": definition.get("definition"),
            "hits": fails,
            "share": round(share * 100, 1),
            "partialHits": stats[code].get(_score_key(PARTIAL_SCORE), 0),
            "models_affected": affected,
            "universal": universal,
            "priority": _priority(universal, share, "dimension"),
            "question_ids": sorted(q for q in questions[code] if q)[:40],
            "question_types": dict(fail_topics[code].most_common(8)),
            "evidence_samples": samples[code],
        })
    return clusters


def _question_hotspots(by_question, model_count):
    """Rank individual questions by how badly the whole fleet handled them.

    This is where the `universal` signal is actually sharp — but only at the
    right granularity. Two coarser definitions look tempting and both collapse
    on real data:

    - "all models fail dimension D" (fleet-wide): near-tautological. With a
      thousand responses, any dimension failing often enough involves every model.
    - "no model passed this question": on a strict rubric where the fleet pass
      rate is a few percent, this is true of ~95% of questions.

    What discriminates is the intersection: a *specific dimension* (or red line)
    that *every scored model* got wrong *on this question*. That is a concrete
    hole in the public corpus, and it names both the question and the kind of
    knowledge missing — exactly what the materials list needs.
    """
    hotspots = []
    quorum = max(2, int(model_count * MIN_HOTSPOT_COVERAGE + 0.999)) if model_count > 1 else 1
    for question_id, bucket in by_question.items():
        answered = len(bucket["models"])
        if not answered:
            continue
        has_quorum = answered >= quorum
        universal_dimensions = sorted(
            code for code, fails in bucket["dimension_fails"].items() if fails >= answered)
        universal_redlines = sorted(
            code for code, hits in bucket["redlines"].items() if hits >= answered)
        universal = has_quorum and bool(universal_dimensions or universal_redlines)
        hotspots.append({
            "questionId": question_id,
            "question": _truncate(bucket["question"], 160),
            "questionType": bucket["type"],
            "group": bucket["group"],
            "modelsAnswered": answered,
            "modelsPassed": bucket["passed"],
            "averageScore": _mean(bucket["scores"]),
            "universal": universal,
            "universalDimensions": universal_dimensions if has_quorum else [],
            "universalRedlines": universal_redlines if has_quorum else [],
            "redlines": dict(bucket["redlines"].most_common()),
            "dimensionFails": dict(bucket["dimension_fails"].most_common()),
            "flawSamples": [_truncate(f, 160) for f in bucket["flaws"][:3]],
        })
    hotspots.sort(key=lambda h: (
        not h["universal"],
        h["averageScore"] if h["averageScore"] is not None else 0,
        -sum(h["redlines"].values()),
    ))
    return hotspots


def _topic_risk(scores_by_topic):
    rows = []
    for topic, scores in scores_by_topic.items():
        mean = _mean(scores)
        if mean is None:
            continue
        priority = "P0" if mean < P0_MAX_SCORE else ("P1" if mean < P1_MAX_SCORE else "P2")
        rows.append({
            "topic": topic,
            "responses": len(scores),
            "averageScore": mean,
            "priority": priority,
        })
    rows.sort(key=lambda row: row["averageScore"])
    return rows


# --------------------------------------------------------------------------
# Human-readable report
# --------------------------------------------------------------------------
def render_report(profile):
    source = profile["source"]
    out = ["# 缺陷图谱\n"]
    out.append("> 由 `analyze_eval.py` 从评测导出自动生成。**这是排产依据，不是结论**——"
               "语义聚类与 KB 弥补方案由 agent 在 skill 工作流 step 3 完成。\n")

    out.append("\n## 数据来源\n")
    out.append("| 项 | 值 |\n|---|---|\n")
    for label, value in (
        ("导出文件", source["file"]),
        ("评测任务", source.get("taskName")),
        ("裁判模型", source.get("judgeModel")),
        ("评分标准", source.get("rubricTitle")),
        ("标准版本", source.get("rubricVersion")),
        ("已评分回答", source["scoredResponses"]),
        ("题目数", source["questions"]),
        ("模型数", len(source["models"])),
    ):
        if value not in (None, ""):
            out.append("| %s | %s |\n" % (label, value))

    if profile["model_stats"]:
        out.append("\n## 模型表现\n")
        out.append("| 模型 | 已评分 | 平均分 | 通过 | 通过率 |\n|---|---|---|---|---|\n")
        for row in sorted(profile["model_stats"], key=lambda r: r["averageScore"] or 0):
            out.append("| %s | %s | %s | %s | %s%% |\n" % (
                row["model"], row["scored"], row["averageScore"],
                row["passed"], row["passRate"]))

    if profile["dimension_stats"]:
        out.append("\n## 维度得分分布\n")
        out.append("| 维度 | 分档 → 条数 |\n|---|---|\n")
        for code, counts in profile["dimension_stats"].items():
            detail = "、".join("%s 分 %s 条" % (k, v) for k, v in counts.items())
            out.append("| %s | %s |\n" % (code, detail))

    if profile["redline_stats"]:
        out.append("\n## 红线触发\n")
        out.append("| 代码 | 触发次数 |\n|---|---|\n")
        for code, hits in profile["redline_stats"].items():
            out.append("| %s | %s |\n" % (code, hits))

    out.append("\n## 缺陷聚类（按优先级）\n")
    out.append("按**占全部已评回答**的比例分档，红线因是一票否决而用更低的阈值。\n\n")
    out.append("这一层的 `universal`（全模型命中）参考价值有限——回答量一大，"
               "任何高频维度几乎必然被所有模型命中。真正锐利的 `universal` 判据在"
               "下面的《题目热点》一节。\n\n")
    for cluster in profile["clusters"]:
        flag = " · **universal（全模型命中）**" if cluster["universal"] else ""
        out.append("### [%s] %s — %s（%s 次，%s%%）%s\n\n" % (
            cluster["priority"], cluster["id"], cluster["name"],
            cluster["hits"], cluster["share"], flag))
        if cluster.get("definition"):
            out.append("**判据**：%s\n\n" % _truncate(cluster["definition"], 300))
        out.append("**命中模型**：%s\n\n" % "、".join(cluster["models_affected"]))
        if cluster.get("question_types"):
            detail = "、".join("%s(%s)" % (k, v) for k, v in cluster["question_types"].items())
            out.append("**集中题型**：%s\n\n" % detail)
        if cluster["evidence_samples"]:
            out.append("**实例**：\n\n")
            for sample in cluster["evidence_samples"]:
                out.append("- `%s` / %s — %s\n" % (
                    sample["model"], sample["questionId"], sample["text"]))
            out.append("\n")

    out.append("\n## 题型风险排序\n")
    out.append("| 优先级 | 题型 | 回答数 | 平均分 |\n|---|---|---|---|\n")
    for row in profile["topic_risk"]:
        out.append("| %s | %s | %s | %s |\n" % (
            row["priority"], row["topic"], row["responses"], row["averageScore"]))

    hotspots = profile.get("question_hotspots") or []
    if hotspots:
        summary = profile.get("hotspot_summary", {})
        out.append("\n## 题目热点（最该补的知识洞）\n")
        out.append("> `全员失败维度` = **所有已评模型在这道题的这个维度上都拿 0 分**。"
                   "这是 `universal` 判据唯一锐利的粒度：维度层面全员命中几乎是必然的，"
                   "「没有模型通过」在严格 rubric 下对 95% 的题都成立——"
                   "只有「同一道题上、同一个维度、全员归零」才是公开语料里一个具体的洞，"
                   "而且它同时点名了**哪道题**和**缺哪类知识**。\n\n")
        out.append("全部 %s 题中，**%s 题存在全员失败维度/红线**；下表为最差 %s 题"
                   "（完整 %s 题见 JSON 的 `question_hotspots`）。\n\n" % (
                       summary.get("totalQuestions"), summary.get("universalFailures"),
                       min(len(hotspots), HOTSPOTS_IN_REPORT), summary.get("carriedInProfile")))
        out.append("| 平均分 | 已评模型 | 全员失败维度 | 全员红线 | 题型 | 题目 |\n"
                   "|---|---|---|---|---|---|\n")
        for row in hotspots[:HOTSPOTS_IN_REPORT]:
            out.append("| %s | %s | %s | %s | %s | %s |\n" % (
                row["averageScore"], row["modelsAnswered"],
                "、".join(row["universalDimensions"]) or "—",
                "、".join(row["universalRedlines"]) or "—",
                row["questionType"], row["question"].replace("|", "\\|")))

    if profile["group_risk"]:
        out.append("\n## 分组风险排序\n")
        out.append("| 优先级 | 分组 | 回答数 | 平均分 |\n|---|---|---|---|\n")
        for row in profile["group_risk"]:
            out.append("| %s | %s | %s | %s |\n" % (
                row["priority"], row["topic"], row["responses"], row["averageScore"]))

    if profile["fabrication_targets"]:
        out.append("\n## 被编造对象词频（D5 线索）\n")
        out.append("> 供 agent 定位「模型最爱编什么」，据此决定哪些数字必须在 KB 中锁定来源。\n\n")
        out.append("| 关键词 | 出现次数 |\n|---|---|\n")
        for item in profile["fabrication_targets"]:
            out.append("| %s | %s |\n" % (item["keyword"], item["count"]))

    if profile["flaw_keywords"]:
        out.append("\n## 主要缺陷词频\n")
        out.append("| 关键词 | 出现次数 |\n|---|---|\n")
        for item in profile["flaw_keywords"]:
            out.append("| %s | %s |\n" % (item["keyword"], item["count"]))

    out.append("\n## 下一步（agent 执行）\n")
    out.append("1. 阅读上面每个 P0 聚类的实例，做**语义归类**，写成具体的 KB 弥补项\n")
    out.append("2. 把「全员失败」的题目按题型归拢，排到《所需资料清单》最前——"
               "这些是公开语料的洞，权威来源必须覆盖\n")
    out.append("3. 为每个 P0 聚类规划反例卡（`antipatterns`）与对应实体字段\n")
    out.append("4. 建 KB 后用 `validate_kb.py --defects <本文件同名 .json>` 校验覆盖率\n")
    return "".join(out)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Aggregate a model-evaluation export into a defect profile.")
    parser.add_argument("input", help="evaluation export JSON")
    parser.add_argument("-o", "--output", help="defect profile JSON path "
                                               "(default: <input dir>/defect-profile.json)")
    parser.add_argument("--field-map", help="JSON file overriding export field paths")
    parser.add_argument("--no-report", action="store_true",
                        help="skip writing the human-readable 缺陷图谱.md")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        die("input not found: %s" % args.input)

    with open(args.input, "r", encoding="utf-8") as handle:
        try:
            data = json.load(handle)
        except ValueError as exc:
            die("input is not valid JSON: %s" % exc)

    field_map = load_field_map(args.field_map)
    profile = build_profile(data, field_map, os.path.basename(args.input))

    output_path = Path(args.output) if args.output \
        else Path(args.input).parent / "defect-profile.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)

    if not args.no_report:
        report_path = output_path.parent / "缺陷图谱.md"
        with open(report_path, "w", encoding="utf-8") as handle:
            handle.write(render_report(profile))

    source = profile["source"]
    p0 = [c for c in profile["clusters"] if c["priority"] == "P0"]
    print("已生成缺陷画像: %s" % output_path)
    if not args.no_report:
        print("人类可读版: %s" % (output_path.parent / "缺陷图谱.md"))
    print("  已评分回答 %s 条 | 题目 %s | 模型 %s"
          % (source["scoredResponses"], source["questions"], len(source["models"])))
    print("  缺陷聚类 %s 个，其中 P0 %s 个"
          % (len(profile["clusters"]), len(p0)))
    summary = profile.get("hotspot_summary", {})
    if summary:
        print("  题目热点: %s 题中 %s 题存在全员失败维度/红线（KB 必补）"
              % (summary.get("totalQuestions"), summary.get("universalFailures")))
    if profile["topic_risk"]:
        worst = profile["topic_risk"][0]
        print("  最高风险题型: %s（平均 %s 分）" % (worst["topic"], worst["averageScore"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
