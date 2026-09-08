"""
Two-stage patient-question extraction for social-media / consultation text.

Stage 1  clean_text()          pure regex/unicode normalization (no LLM)
Stage 2  extract_questions()   Claude identifies the actual patient questions

The two stages are connected by `run_extraction()`, which:
  - cleans every row with stage 1,
  - packs rows into batches sized by character budget (NOT a fixed row count),
  - sends each batch to Claude in one call,
  - writes each finished batch to a JSONL checkpoint so a crash/interrupt resumes,
  - returns a tidy DataFrame you merge back onto your original frame by `row_id`.

Backends (auto-detected, override with backend=):
  "sdk"  anthropic Python SDK.  Needs `pip install anthropic` + ANTHROPIC_API_KEY.
         Fast and cheap. Use this for the full 4,250-row run.
  "cli"  subprocess to the `claude` binary already on your PATH. No API key needed,
         but every call re-sends the ~30K-token Claude Code system prompt, so it is
         far slower/pricier per call. Batching is what makes it survivable.

Usage from the notebook:

    from question_extraction import clean_text, run_extraction

    df["news_content_clean"] = df["news_content"].map(clean_text)

    q = run_extraction(
        df,
        id_col="source_row_number",       # or df.index
        text_cols=["news_title", "news_content"],
        out_path="questions.jsonl",
    )
    df = df.merge(q, left_on="source_row_number", right_on="row_id", how="left")
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Sequence

import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# Stage 1 — regex cleaning
# ──────────────────────────────────────────────────────────────────────────────

# Emoji + pictographs + variation selectors + skin-tone modifiers + ZWJ sequences.
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"   # pictographs, emoticons, transport, symbols, extended-A
    "\U00002600-\U000027bf"   # misc symbols + dingbats
    "\U00002190-\U000021ff"   # arrows
    "\U00002b00-\U00002bff"   # misc symbols and arrows
    "\U0001f1e6-\U0001f1ff"   # regional indicators (flags)
    "\U0000fe00-\U0000fe0f"   # variation selectors
    "\U0001f3fb-\U0001f3ff"   # skin tone modifiers
    "\U000020e3"              # combining enclosing keycap
    "\U0000200d"              # zero-width joiner (emoji sequences)
    "\U00002640\U00002642"    # gender signs
    "]+"
)

# Platform emoticon placeholders: [哭惹R] [皱眉R] [微笑] [doge] etc.
# Deliberately capped at 12 chars so it can't eat a real bracketed clause.
_STICKER = re.compile(r"[\[【]\s*[^\[\]【】\n]{1,12}\s*[\]】]")

# Weibo/Xiaohongshu topic tags: #骨转移[话题]#  #放疗#  #肝癌晚期
_HASHTAG = re.compile(r"#[^#\s]{1,40}?(?:\[话题\])?#|#[^\s#]{1,40}")

_AT_MENTION = re.compile(r"[@＠][\w一-龥\-_]{1,30}")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b\S+\.(?:com|cn|net|org)/\S*", re.I)

# Invisible / control characters (keep \n and \t; they're stripped later anyway).
_INVISIBLE = re.compile(r"[​-\u200F\u202A-\u202E⁠-⁯﻿\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Decorative runs: ——— ~~~ === *** ··· ___ ▬▬▬ ★★★ and full/half-width dashes.
_DECOR = re.compile(r"[-—–―_=~*·・•▪▫◆◇■□●○★☆※§¶✦✧♥♡→←↑↓▬▭▁-▏]{2,}")
_LONE_DASH = re.compile(r"(?<![\w一-龥])[-—–―~]+(?![\w一-龥])")

# Repeated punctuation: ！！！！ → ！ , ？？？ → ？ , ...... → …
_REPEAT_PUNCT = re.compile(r"([!！?？。，,、;；:：…~～])\1{1,}")
_ELLIPSIS = re.compile(r"\.{3,}|。{3,}")

_WS = re.compile(r"[ \t　\xa0]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# NFKC folds full-width CJK punctuation to ASCII (，→, ？→? （→( …→...), which
# westernizes Chinese text for no benefit. Shield those characters through the
# normalization by round-tripping them via the private-use area, so NFKC still
# does the work we DO want (full-width letters/digits, compatibility forms).
_PROTECTED_PUNCT = "，！？；：（）…～"
_TO_PUA = {ord(c): chr(0xE000 + i) for i, c in enumerate(_PROTECTED_PUNCT)}
_FROM_PUA = {0xE000 + i: c for i, c in enumerate(_PROTECTED_PUNCT)}


def _normalize_preserving_cjk_punct(s: str) -> str:
    return unicodedata.normalize("NFKC", s.translate(_TO_PUA)).translate(_FROM_PUA)

# Boilerplate that carries no patient signal. Extend freely.
_BOILERPLATE = [
    r"图片资料[，,]?\s*仅主诊医生和患者本人可见",
    r"隐私内容[，,]?\s*仅主诊医生和患者本人可见",
    r"内容涉及隐私[，,]?\s*仅医生和患者本人可见",
    r"该内容仅医生和患者本人可见",
    r"(?:图片|语音|视频|文件|音频|医学影像|录音)因隐私问题无法显示",
    r"语音文件已过期[，,]?\s*无法显示该消息",
    r"春雨已将此部分内容屏蔽[^\n]*",
    r"如您希望获得医生定制的方案建议[，,]?\s*请点击下方立即咨询",
    r"^\s*通知[:：][^\n]*",
    r"^\s*您好[，,]?\s*很高兴为(?:你|您)提供健康咨询服务[^\n]*",
    r"点击(?:链接|下方|查看详情)",
    r"(?:展开|收起)全文",
]
_BOILERPLATE_RE = re.compile("|".join(_BOILERPLATE), re.M)


def clean_text(text: Any) -> str:
    """Stage 1: strip unformatted noise. Deterministic, no LLM, safe to re-run.

    Removes emoji, sticker placeholders, hashtags, @mentions, URLs, decorative
    dash/tilde runs, invisible characters, platform boilerplate; collapses
    repeated punctuation and whitespace. Preserves sentence content and line
    structure so stage 2 still sees where one thought ends and the next begins.
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return ""
    s = str(text)
    if not s.strip():
        return ""

    s = _normalize_preserving_cjk_punct(s)
    s = _INVISIBLE.sub("", s)
    s = _URL.sub(" ", s)
    s = _HASHTAG.sub(" ", s)
    s = _AT_MENTION.sub(" ", s)
    s = _EMOJI.sub(" ", s)
    s = _STICKER.sub(" ", s)
    s = _BOILERPLATE_RE.sub(" ", s)
    s = _DECOR.sub(" ", s)
    s = _LONE_DASH.sub(" ", s)
    s = _ELLIPSIS.sub("…", s)
    s = _REPEAT_PUNCT.sub(r"\1", s)

    lines = [_WS.sub(" ", ln).strip() for ln in s.split("\n")]
    s = "\n".join(ln for ln in lines if ln)
    s = _BLANK_LINES.sub("\n\n", s)
    return s.strip()


# ──────────────────────────────────────────────────────────────────────────────
# Stage 2 — Claude
# ──────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
你是医学文本标注助手，任务是从肿瘤（尤其是骨转移）相关的社交媒体帖子和在线问诊记录中，\
识别并抽取【患者或其家属真正提出的问题】。

判定规则：
1. 只抽取"提问"——即作者在寻求信息、建议、经验或确认的表达。
2. 问题不一定带问号。中文口语里大量提问没有问号，例如"想问下放疗会不会伤皮肤"、\
   "有经验的战友分享一下怎么应对"、"不知道该不该换靶向药"。这些都算问题。
3. 以下不算问题，必须排除：
   - 纯情绪表达（"好难受""希望爸爸好起来""心疼"）
   - 纯病情陈述/流水账（"今天做了CT""指标没变化"）
   - 医生的回复、科普内容、广告、募捐
   - 反问句和修辞性问句（不是真的在求答案）
4. 把每个问题改写成【可独立理解的完整问句】：补上必要的主语和背景（癌种、部位、\
   治疗手段），去掉口语赘词，保留原意，不要添加原文没有的信息。一句话，25字以内为宜。
5. 一条记录可能包含 0 个、1 个或多个问题。不要合并不同的问题，也不要拆散同一个问题。

对每条记录输出：
- row_id：原样回传，不要修改
- has_question：true / false
- questions：改写后的问句数组；has_question 为 false 时返回 []
- intent：每个问题的意图分类，与 questions 等长，取值仅限
  ["治疗方案","副作用/不良反应","用药","检查/诊断","疾病进展/预后","护理/生活",\
"费用/医保","就医流程","其他"]

只输出 JSON，不要输出任何解释文字、前言或 markdown 代码块。
输出格式：{"results": [{"row_id": ..., "has_question": ..., "questions": [...], "intent": [...]}, ...]}
必须为输入里的每一条记录输出一个对象，顺序与输入一致。"""


@dataclass
class Batch:
    rows: list[dict[str, Any]]

    @property
    def ids(self) -> list[Any]:
        return [r["row_id"] for r in self.rows]

    def to_prompt(self) -> str:
        payload = json.dumps(self.rows, ensure_ascii=False, indent=None)
        return f"请处理以下 {len(self.rows)} 条记录：\n\n{payload}"


def make_batches(
    records: Sequence[dict[str, Any]],
    max_rows: int = 20,
    max_chars: int = 12_000,
) -> list[Batch]:
    """Pack records into batches bounded by BOTH row count and character budget.

    A fixed row count is wrong here: news_content ranges from a few characters to
    118K. Budgeting by characters keeps every request roughly the same size, and
    an oversized single record gets its own batch instead of blowing up a group.
    """
    batches: list[Batch] = []
    cur: list[dict[str, Any]] = []
    cur_chars = 0
    for rec in records:
        n = len(rec.get("text", ""))
        if cur and (len(cur) >= max_rows or cur_chars + n > max_chars):
            batches.append(Batch(cur))
            cur, cur_chars = [], 0
        cur.append(rec)
        cur_chars += n
    if cur:
        batches.append(Batch(cur))
    return batches


# --- JSON extraction ----------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_model_json(raw: str) -> list[dict[str, Any]]:
    """Pull the results array out of a model response, tolerating fences/preamble."""
    text = raw.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError(f"no JSON object in response: {raw[:300]!r}")
        obj = json.loads(text[start : end + 1])
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("results"), list):
        return obj["results"]
    raise ValueError(f"unexpected JSON shape: {str(obj)[:300]!r}")


# --- Backends -----------------------------------------------------------------

class Backend:
    name = "base"

    def complete(self, system: str, user: str) -> str:  # pragma: no cover
        raise NotImplementedError


class SDKBackend(Backend):
    """anthropic Python SDK. Requires `pip install anthropic` and ANTHROPIC_API_KEY.

    This is the path to use for the full run: no agent-harness prompt overhead,
    real concurrency, and prompt caching on the (static) system prompt.
    """

    name = "sdk"

    def __init__(
        self,
        model: str = "claude-opus-5",
        max_tokens: int = 8000,
        effort: str = "low",
    ):
        import anthropic  # imported lazily so the CLI path needs no dependency

        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens
        self.effort = effort

    def complete(self, system: str, user: str) -> str:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # System prompt is byte-identical across every batch → cache it and
            # pay ~0.1x for it after the first call.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            # Mechanical extraction; low effort keeps cost and latency down.
            # Raise to "medium"/"high" if you see questions being missed.
            output_config={"effort": self.effort},
            messages=[{"role": "user", "content": user}],
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {resp.stop_details}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("hit max_tokens — lower max_rows/max_chars or raise max_tokens")
        return "".join(b.text for b in resp.content if b.type == "text")


def _resolve_claude(binary: str = "claude") -> str:
    """Absolute path to the `claude` executable.

    A Jupyter kernel launched from a GUI does not inherit the login shell's PATH,
    so `claude` works in your terminal but subprocess raises
    `[Errno 2] No such file or directory: 'claude'` from a notebook. Check PATH
    first, then probe the usual install locations.
    """
    import shutil
    if os.path.sep in binary:
        return binary
    found = shutil.which(binary)
    if found:
        return found
    for cand in (os.path.expanduser("~/.local/bin/claude"),
                 os.path.expanduser("~/.claude/local/claude"),
                 "/opt/homebrew/bin/claude",
                 "/usr/local/bin/claude"):
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError(
        "cannot find the `claude` executable. Run `which claude` in your terminal "
        "and pass the absolute path: CLIBackend(binary='/abs/path/claude')"
    )


class CLIBackend(Backend):
    """Subprocess to the `claude` binary already authenticated on your machine.

    No API key required. Costs more per call because Claude Code's own system
    prompt (~30K tokens) is re-sent every invocation — batching amortizes it.
    Keep concurrency low (2-4); each call spawns a full agent process.
    """

    name = "cli"

    def __init__(self, binary: str = "claude", timeout: int = 600, extra_args: Sequence[str] = ()):
        # Resolve at construction so a missing binary fails once and clearly,
        # instead of failing every batch three times inside the retry loop.
        self.binary = _resolve_claude(binary)
        self.timeout = timeout
        self.extra_args = list(extra_args)

    def complete(self, system: str, user: str) -> str:
        cmd = [
            self.binary,
            "-p",
            "--output-format", "json",
            "--append-system-prompt", system,
            *self.extra_args,
        ]
        proc = subprocess.run(
            cmd,
            input=user,
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
        env = json.loads(proc.stdout)
        if env.get("is_error"):
            raise RuntimeError(f"claude error: {str(env)[:500]}")
        return env["result"]


def auto_backend(**kwargs) -> Backend:
    """Prefer the SDK when it's usable, otherwise fall back to the CLI."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return SDKBackend(**{k: v for k, v in kwargs.items() if k in {"model", "max_tokens", "effort"}})
        except ImportError:
            print("ANTHROPIC_API_KEY set but `anthropic` not installed; "
                  "run `pip install anthropic` for the fast path. Falling back to CLI.",
                  file=sys.stderr)
    return CLIBackend(**{k: v for k, v in kwargs.items() if k in {"binary", "timeout", "extra_args"}})


# ──────────────────────────────────────────────────────────────────────────────
# The connector: stage 1 → batching → stage 2 → checkpointed results
# ──────────────────────────────────────────────────────────────────────────────

_write_lock = threading.Lock()

_ALLOWED_INTENTS = {
    "治疗方案", "副作用/不良反应", "用药", "检查/诊断",
    "疾病进展/预后", "护理/生活", "费用/医保", "就医流程", "其他",
}


def _normalize_result(rec: dict[str, Any], row_id: Any) -> dict[str, Any]:
    qs = rec.get("questions") or []
    if not isinstance(qs, list):
        qs = [str(qs)]
    qs = [str(q).strip() for q in qs if str(q).strip()]

    intents = rec.get("intent") or []
    if not isinstance(intents, list):
        intents = [str(intents)]
    intents = [i if i in _ALLOWED_INTENTS else "其他" for i in map(str, intents)]
    # Keep intent aligned with questions even if the model returned a short list.
    intents = (intents + ["其他"] * len(qs))[: len(qs)]

    return {
        "row_id": row_id,
        "has_question": bool(qs) if rec.get("has_question") is None else bool(rec["has_question"]) and bool(qs),
        "questions": qs,
        "intent": intents,
        "n_questions": len(qs),
    }


def _load_done(path: str) -> set:
    """row_ids already finished. Rows carrying an `error` do NOT count as done,
    so re-running automatically retries failed batches."""
    done, failed = set(), set()
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    rid = rec["row_id"]
                except (json.JSONDecodeError, KeyError):
                    continue
                (failed if rec.get("error") else done).add(rid)
    retry = failed - done
    if retry:
        print(f"  retrying {len(retry)} row(s) that failed on a previous run")
    return done


def _append(path: str, rows: Iterable[dict[str, Any]]) -> None:
    if not path:
        return
    with _write_lock, open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _process_batch(batch: Batch, backend: Backend, system: str, retries: int) -> list[dict[str, Any]]:
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            raw = backend.complete(system, batch.to_prompt())
            parsed = parse_model_json(raw)
            by_id = {r.get("row_id"): r for r in parsed if isinstance(r, dict)}
            out = []
            for rid in batch.ids:
                # Models occasionally stringify integer ids — accept either form.
                rec = by_id.get(rid, by_id.get(str(rid), {}))
                out.append(_normalize_result(rec, rid))
            return out
        except Exception as e:  # noqa: BLE001 — retry any transport/parse failure
            last_err = e
    # Don't lose the whole batch: emit error rows so the run is auditable and
    # you can re-drive just the failures by deleting them from the checkpoint.
    return [
        {"row_id": rid, "has_question": None, "questions": [], "intent": [],
         "n_questions": 0, "error": str(last_err)[:300]}
        for rid in batch.ids
    ]


def run_extraction(
    df: pd.DataFrame,
    id_col: str | None = None,
    text_cols: Sequence[str] = ("news_title", "news_content"),
    out_path: str = "questions.jsonl",
    backend: Backend | str | None = None,
    max_rows: int = 20,
    max_chars: int = 12_000,
    max_record_chars: int = 4_000,
    concurrency: int | None = None,
    retries: int = 2,
    system_prompt: str = SYSTEM_PROMPT,
    limit: int | None = None,
    cleaner: Callable[[Any], str] = clean_text,
    resume: bool = True,
    progress: bool = True,
) -> pd.DataFrame:
    """Run stage 1 + stage 2 over `df` and return one row per input row.

    Args:
        id_col: column holding a stable unique id. None → use df.index.
        text_cols: columns concatenated (in order) into the text sent to Claude.
        out_path: JSONL checkpoint. Re-running skips row_ids already present.
        backend: Backend instance, "sdk", "cli", or None to auto-detect.
        max_record_chars: per-record truncation; a 118K-char post adds noise, not signal.
        limit: process only the first N un-done rows — use this to smoke-test.

    Returns:
        DataFrame with columns row_id, has_question, questions, intent, n_questions
        (plus `error` if any batch failed). Merge it back with:
            df.merge(out, left_on=id_col, right_on="row_id", how="left")
    """
    if isinstance(backend, str):
        backend = SDKBackend() if backend == "sdk" else CLIBackend()
    elif backend is None:
        backend = auto_backend()

    if concurrency is None:
        concurrency = 8 if backend.name == "sdk" else 3

    ids = df[id_col].tolist() if id_col else df.index.tolist()
    if len(set(ids)) != len(ids):
        raise ValueError(f"{id_col or 'index'} is not unique — pick a unique id column")

    done = _load_done(out_path) if resume else set()
    if not resume and out_path and os.path.exists(out_path):
        os.remove(out_path)

    records: list[dict[str, Any]] = []
    skipped_empty = 0
    for rid, (_, row) in zip(ids, df.iterrows()):
        if rid in done:
            continue
        parts = [cleaner(row.get(c)) for c in text_cols if c in df.columns]
        text = "\n".join(p for p in parts if p).strip()
        if not text:
            skipped_empty += 1
            _append(out_path, [{"row_id": rid, "has_question": False, "questions": [],
                                "intent": [], "n_questions": 0}])
            continue
        if len(text) > max_record_chars:
            text = text[:max_record_chars] + "…"
        records.append({"row_id": rid, "text": text})

    if limit is not None:
        records = records[:limit]

    batches = make_batches(records, max_rows=max_rows, max_chars=max_chars)
    if progress:
        print(f"backend={backend.name} | {len(done)} cached, {skipped_empty} empty, "
              f"{len(records)} to process in {len(batches)} batches "
              f"(concurrency={concurrency})", file=sys.stderr)

    completed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_process_batch, b, backend, system_prompt, retries): b for b in batches}
        for fut in as_completed(futures):
            rows = fut.result()
            _append(out_path, rows)
            completed += 1
            if progress:
                n_err = sum(1 for r in rows if "error" in r)
                print(f"  [{completed}/{len(batches)}] {len(rows)} rows"
                      + (f"  ⚠ {n_err} errors" if n_err else ""), file=sys.stderr)

    return read_results(out_path) if out_path else pd.DataFrame()


def read_results(path: str) -> pd.DataFrame:
    """Load the checkpoint back as a DataFrame (last write per row_id wins)."""
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.drop_duplicates(subset="row_id", keep="last").reset_index(drop=True)
    return out


def explode_questions(results: pd.DataFrame) -> pd.DataFrame:
    """One row per extracted question — the shape you want for the question bank."""
    df = results[results["n_questions"] > 0].copy()
    if df.empty:
        return pd.DataFrame(columns=["row_id", "question", "intent"])
    df["pair"] = df.apply(lambda r: list(zip(r["questions"], r["intent"])), axis=1)
    df = df.explode("pair")
    df["question"] = df["pair"].map(lambda p: p[0])
    df["intent"] = df["pair"].map(lambda p: p[1])
    return df[["row_id", "question", "intent"]].reset_index(drop=True)
