"""
chat_ui_task.py — RPA 平台任务脚本：驱动 LLM 网页版聊天界面采集回答。

契约（见 docs/worker/worker-env-variables.md、skills/rpa-script-wrapper/SKILL.md）：
  入参   : stdin JSON  +  env API_CUSTOM_INPUT
  登录态 : env RPA_STORAGE_STATE（平台从 OSS 拉下来的 storage_state.json）
  有头   : env CDP_ENDPOINT（WORKER_HEADED=1 时，attach worker 浏览器）
  出参   : stdout 最后一行 {"summary": {...}, "rows": [...]}
  日志   : 全部走 stderr（污染 stdout 会让 worker 解析不到结果）
  退出码 : 运行时异常 exit 1；登录态失效 summary.logged_in=false + exit 0

一次 run 处理一批问题（batch），复用同一个浏览器上下文与会话，
避免每题一次冷启动。批量大小由 custom_input.questions 决定。
"""
import json
import os
import sys
import time
import traceback

# ── 每个平台的站点配置 ────────────────────────────────────────────────
# 选择器需要你在真实页面上用 devtools 核对后填准确。这里给的是起点，
# 各家前端改版频繁，selector 是这套方案最主要的维护成本。
SITES = {
    "qianwen": {
        "url": "https://www.tongyi.com/qianwen/",
        "input": "textarea, div[contenteditable='true']",
        "send_key": "Enter",
        "answer": "[class*='answerItem'], [class*='bubble'][class*='ai']",
        "logged_out_hint": ["登录", "立即登录"],
    },
    "doubao": {
        "url": "https://www.doubao.com/chat/",
        "input": "textarea, div[contenteditable='true']",
        "send_key": "Enter",
        "answer": "[data-testid='message_text_content'], [class*='message-content']",
        "logged_out_hint": ["登录", "手机号登录"],
    },
    "zhipu": {
        "url": "https://chatglm.cn/main/alltoolsdetail",
        "input": "textarea, div[contenteditable='true']",
        "send_key": "Enter",
        "answer": "[class*='answer-content'], [class*='markdown-body']",
        "logged_out_hint": ["登录", "手机号登录"],
    },
    "kimi": {
        "url": "https://kimi.moonshot.cn/",
        "input": "div[contenteditable='true'], textarea",
        "send_key": "Enter",
        "answer": "[class*='segment-assistant'], [class*='markdown']",
        "logged_out_hint": ["登录", "立即登录"],
    },
    "deepseek": {
        "url": "https://chat.deepseek.com/",
        "input": "textarea#chat-input, textarea",
        "send_key": "Enter",
        "answer": "[class*='ds-markdown']",
        "logged_out_hint": ["登录", "Log in"],
    },
    "hunyuan": {
        "url": "https://yuanbao.tencent.com/chat/",
        "input": "div[contenteditable='true'], textarea",
        "send_key": "Enter",
        "answer": "[class*='agent-chat__bubble--ai'], [class*='hyc-content']",
        "logged_out_hint": ["登录", "微信登录"],
    },
}


def log(msg):
    """所有日志走 stderr —— stdout 最后一行必须是结果 JSON。"""
    print(msg, file=sys.stderr, flush=True)


def read_params():
    """业务参数：stdin JSON 与 API_CUSTOM_INPUT 两路等价，合并取用。"""
    try:
        stdin_raw = sys.stdin.read()
    except Exception:
        stdin_raw = ""
    params = json.loads(stdin_raw) if stdin_raw.strip() else {}
    custom = params.get("custom_input") or {}
    env_custom = os.environ.get("API_CUSTOM_INPUT")
    if env_custom:
        try:
            custom = {**custom, **json.loads(env_custom)}
        except json.JSONDecodeError:
            pass
    return params, custom


def open_context(p, site):
    """优先 attach worker 的有头浏览器；否则用 storage_state 起一个新的。"""
    cdp = os.environ.get("CDP_ENDPOINT")
    storage = os.environ.get("RPA_STORAGE_STATE")

    if cdp:
        log(f"[browser] attach CDP {cdp}")
        browser = p.chromium.connect_over_cdp(cdp)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        return browser, ctx

    if not storage or not os.path.exists(storage):
        return None, None

    log(f"[browser] launch headless with storage_state={storage}")
    browser = p.chromium.launch(
        headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"]
    )
    ctx = browser.new_context(storage_state=storage, locale="zh-CN")
    return browser, ctx


def check_logged_in(page, site):
    """粗判登录态：页面出现登录提示且找不到输入框，视为未登录。"""
    try:
        page.wait_for_selector(site["input"], timeout=15000)
        return True
    except Exception:
        body = (page.inner_text("body") or "")[:2000]
        for hint in site["logged_out_hint"]:
            if hint in body:
                return False
        return False


def wait_for_answer(page, site, prev_count, idle_rounds=4, poll_s=1.5, max_s=180):
    """
    等待流式输出结束：轮询最后一条回答的文本，连续 idle_rounds 次不变即认为写完。
    LLM 网页是流式渲染，没有可靠的「完成」事件，稳定性检测是最实用的判定方式。
    """
    deadline = time.time() + max_s
    last_text, stable = "", 0

    while time.time() < deadline:
        time.sleep(poll_s)
        nodes = page.query_selector_all(site["answer"])
        if len(nodes) <= prev_count:
            continue
        text = (nodes[-1].inner_text() or "").strip()
        if text and text == last_text:
            stable += 1
            if stable >= idle_rounds:
                return text
        else:
            stable = 0
            last_text = text

    return last_text  # 超时也把已拿到的部分返回，标记 truncated


def ask_one(page, site, question):
    """发一题，等回答，返回 (答案文本, 耗时秒, 是否疑似截断)。"""
    start = time.monotonic()
    prev = len(page.query_selector_all(site["answer"]))

    box = page.wait_for_selector(site["input"], timeout=20000)
    box.click()
    box.fill("") if box.get_attribute("contenteditable") is None else None
    box.type(question, delay=15)
    page.keyboard.press(site["send_key"])
    log(f"[ask] 已发送，等待流式回答… (prev_count={prev})")

    answer = wait_for_answer(page, site, prev)
    elapsed = round(time.monotonic() - start, 2)
    return answer, elapsed, not answer


def run():
    params, custom = read_params()
    platform_key = (
        custom.get("platform")
        or os.environ.get("RPA_ACCOUNT_PLATFORM")
        or params.get("platform")
    )
    questions = custom.get("questions") or []

    if platform_key not in SITES:
        raise ValueError(f"未知平台 {platform_key}，可选 {list(SITES)}")
    if not questions:
        raise ValueError("custom_input.questions 为空")

    site = SITES[platform_key]
    log(f"[run] platform={platform_key} 共 {len(questions)} 题")

    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as p:
        browser, ctx = open_context(p, site)
        if ctx is None:
            return {
                "summary": {
                    "logged_in": False,
                    "reason": "缺少 RPA_STORAGE_STATE，账号未登录",
                    "platform": platform_key,
                },
                "rows": [],
            }

        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(site["url"], wait_until="domcontentloaded")

        if not check_logged_in(page, site):
            log("[login] 判定未登录")
            return {
                "summary": {
                    "logged_in": False,
                    "reason": f"{platform_key} 网页端登录态失效，需人工重登",
                    "platform": platform_key,
                },
                "rows": [],
            }

        log("[login] 登录态有效，开始逐题提问")
        for i, q in enumerate(questions, 1):
            qid = q.get("question_id")
            try:
                answer, elapsed, truncated = ask_one(page, site, q["question"])
                rows.append({
                    "question_id": qid,
                    "cancer_type": q.get("cancer_type"),
                    "question_type": q.get("question_type"),
                    "question": q["question"],
                    "model": platform_key,
                    "response": answer,
                    "error": "empty_or_timeout" if truncated else None,
                    "elapsed_s": elapsed,
                    "channel": "web_rpa",
                })
                log(f"[{i}/{len(questions)}] {qid} ok {elapsed}s len={len(answer)}")
            except Exception as exc:
                rows.append({
                    "question_id": qid,
                    "question": q.get("question"),
                    "model": platform_key,
                    "response": None,
                    "error": f"{type(exc).__name__}: {exc}",
                    "channel": "web_rpa",
                })
                log(f"[{i}/{len(questions)}] {qid} FAILED: {exc}")

            time.sleep(custom.get("interval_s", 5))  # 降低风控概率

        try:
            browser.close()
        except Exception:
            pass

    ok = sum(1 for r in rows if r.get("response"))
    return {
        "summary": {
            "logged_in": True,
            "platform": platform_key,
            "total": len(rows),
            "ok": ok,
            "failed": len(rows) - ok,
        },
        "rows": rows,
    }


def main():
    try:
        result = run()
    except Exception as exc:
        log("[fatal] " + traceback.format_exc())
        print(json.dumps(
            {"summary": {"ok": False, "reason": str(exc)}, "rows": []},
            ensure_ascii=False,
        ))
        sys.exit(1)

    # stdout 最后一行 = 结果 JSON
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
