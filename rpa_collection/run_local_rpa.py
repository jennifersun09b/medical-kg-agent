"""
run_local_rpa.py — 本地 RPA：用你自己的 Chrome 直接问 Kimi / 腾讯元宝网页版

不依赖 GEO 后端，也不依赖 RPA 平台。整个流程都在你这台机器上：
    本脚本 -> Playwright 驱动本地 Chrome -> kimi.com / yuanbao.tencent.com

三步：
    1) 探测页面结构，确认选择器能用（不问任何问题，不消耗额度）
         python run_local_rpa.py --probe

    2) 首次登录（开浏览器，你手动扫码/登录，登录态存进本地 profile 下次复用）
         python run_local_rpa.py --login

    3) 跑题
         python run_local_rpa.py --sample 50
         python run_local_rpa.py --resume        # 补跑失败的

设计说明：
  - 答案抽取用「发送前后页面文本 diff + 稳定性轮询」，不依赖某个易变的 CSS class。
    聊天站点前端改版很频繁，写死 class 名的脚本活不过几周。
  - 有已知选择器就先用已知的，取不到就自动退回 diff 方案。
  - 逐题落盘，中断不丢数据。
  - 默认有头模式（headless 容易被风控拦），跑的时候别去动那个浏览器窗口。
"""
import argparse
import difflib
import json
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
DEFAULT_QUESTIONS = HERE.parent / "model_response" / "question_list_all_new.jsonl"
RESULTS_DIR = HERE / "results_local"
PROFILE_DIR = HERE / "browser_profile"   # 登录态存这里，别提交进 Git
SHOTS_DIR = HERE / "shots"               # 失败时截图，方便排错

SITES = {
    "kimi": {
        "name": "Kimi",
        "url": "https://www.kimi.com/",
        # 已知选择器仅作优先尝试，取不到会自动退回通用方案
        "answer_hints": ["[class*='segment-assistant']", ".markdown", "[class*='assistant']"],
        "send_key": "Enter",
        "logged_out_hint": ["扫码登录", "手机号登录", "登录后可"],
    },
    "yuanbao": {
        "name": "腾讯元宝(混元)",
        "url": "https://yuanbao.tencent.com/chat/naQivTmsDa",
        "answer_hints": ["[class*='agent-chat__bubble--ai']", "[class*='hyc-content']", ".markdown"],
        "send_key": "Enter",
        "logged_out_hint": ["扫码登录", "微信登录", "QQ登录", "登录后"],
    },
}


# ---------------------------------------------------------------------------
# 题库
# ---------------------------------------------------------------------------
def question_text(q: dict) -> str:
    for k in ("question", "question_text", "query", "content", "text"):
        v = q.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    raise KeyError(f"找不到问题文本，该条字段为: {sorted(q.keys())}")


def load_questions(path: Path, n: int, seed: int) -> list:
    rows = [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]
    random.seed(seed)
    picked = random.sample(rows, min(n, len(rows)))
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "_sample_manifest.json").write_text(json.dumps({
        "source": str(path), "total_in_pool": len(rows), "sample_size": len(picked),
        "seed": seed, "sampled_at": datetime.now(timezone.utc).isoformat(),
        "question_ids": [q.get("question_id") for q in picked],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return picked


def out_path(site_key: str) -> Path:
    return RESULTS_DIR / f"{site_key}_results.jsonl"


def done_ids(site_key: str) -> set:
    p = out_path(site_key)
    if not p.exists():
        return set()
    ids = set()
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("error") and row.get("response"):
            ids.add(row.get("question_id"))
    return ids


def write_row(site_key: str, row: dict):
    RESULTS_DIR.mkdir(exist_ok=True)
    with open(out_path(site_key), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 页面操作
# ---------------------------------------------------------------------------
def find_input(page):
    """
    找输入框：取页面上可见的 textarea / contenteditable 里面积最大的那个。
    比写死 CSS class 稳得多 —— 聊天站的输入框永远是页面上最大的那个可编辑区。
    """
    best, best_area = None, 0
    for el in page.query_selector_all("textarea, [contenteditable='true'], [contenteditable='plaintext-only']"):
        try:
            if not el.is_visible():
                continue
            bb = el.bounding_box()
        except Exception:
            continue
        if not bb or bb["width"] < 120 or bb["height"] < 20:
            continue
        area = bb["width"] * bb["height"]
        if area > best_area:
            best, best_area = el, area
    return best


def page_text(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def added_text(before: str, after: str) -> str:
    """取 after 相对 before 新增的行。"""
    b, a = before.splitlines(), after.splitlines()
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, b, a).get_opcodes():
        if tag in ("insert", "replace"):
            out.extend(a[j1:j2])
    return "\n".join(out).strip()


def read_answer_now(page, site, before_text: str, question: str) -> str:
    """当前时刻能读到的答案。优先已知选择器，取不到退回文本 diff。"""
    best = ""
    for sel in site["answer_hints"]:
        try:
            els = page.query_selector_all(sel)
        except Exception:
            continue
        if els:
            try:
                t = (els[-1].inner_text() or "").strip()
            except Exception:
                t = ""
            if len(t) > len(best):
                best = t

    diff = added_text(before_text, page_text(page))
    # 去掉页面回显的问题本身
    if diff.startswith(question):
        diff = diff[len(question):].strip()
    diff = re.sub(r"^(复制|重新生成|分享|点赞|踩)\s*$", "", diff, flags=re.M).strip()

    return best if len(best) >= len(diff) else diff


def wait_for_answer(page, site, before_text, question,
                    idle_rounds=5, poll_s=1.5, max_s=240):
    """
    流式输出没有可靠的「结束」事件，只能靠稳定性判断：
    连续 idle_rounds 次读到的答案长度不再变化，就认为写完了。
    """
    t0 = time.monotonic()
    last_len, stable, answer = -1, 0, ""
    while time.monotonic() - t0 < max_s:
        time.sleep(poll_s)
        cur = read_answer_now(page, site, before_text, question)
        if len(cur) == last_len and len(cur) > 0:
            stable += 1
            if stable >= idle_rounds:
                return cur, False
        else:
            stable = 0
        last_len = len(cur)
        answer = cur
    return answer, True   # True = 超时截断


def check_login(page, site):
    """返回 (是否已登录, 说明)。"""
    inp = find_input(page)
    body = page_text(page)
    hit = [h for h in site["logged_out_hint"] if h in body]
    if inp is None:
        return False, f"找不到输入框" + (f"；页面出现{hit}" if hit else "")
    if hit:
        return False, f"找到了输入框，但页面同时出现{hit}，可能未登录"
    return True, "已登录"


def ask_one(page, site, question, max_s):
    page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    inp = find_input(page)
    if inp is None:
        raise RuntimeError("找不到输入框（可能未登录或页面结构变了，跑 --probe 看看）")

    before = page_text(page)
    inp.click()
    page.wait_for_timeout(300)
    inp.type(question, delay=12)
    page.wait_for_timeout(400)
    page.keyboard.press(site["send_key"])

    t0 = time.monotonic()
    answer, truncated = wait_for_answer(page, site, before, question, max_s=max_s)
    elapsed = round(time.monotonic() - t0, 1)

    if not answer:
        raise RuntimeError("等到超时也没读到答案内容")
    return answer, elapsed, truncated


# ---------------------------------------------------------------------------
def open_browser(p, headless: bool):
    PROFILE_DIR.mkdir(exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        channel="chrome",                     # 用你已装的真 Chrome，风控识别率更低
        viewport={"width": 1440, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def cmd_probe(args):
    """只探测页面结构，不问任何问题。"""
    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for key in args.models:
            site = SITES[key]
            print(f"\n=== {site['name']} {site['url']} ===")
            try:
                page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(4000)
                ok, why = check_login(page, site)
                print(f"  登录状态: {'✓ ' if ok else '✗ '}{why}")
                inp = find_input(page)
                if inp:
                    bb = inp.bounding_box()
                    print(f"  输入框: <{inp.evaluate('e => e.tagName.toLowerCase()')}> "
                          f"{int(bb['width'])}x{int(bb['height'])}")
                for sel in site["answer_hints"]:
                    n = len(page.query_selector_all(sel))
                    print(f"  选择器 {sel:<45} 命中 {n} 个")
                SHOTS_DIR.mkdir(exist_ok=True)
                shot = SHOTS_DIR / f"probe_{key}.png"
                page.screenshot(path=str(shot))
                print(f"  截图: {shot}")
            except Exception as e:
                print(f"  探测失败: {e}")
        ctx.close()
    print("\n看一下截图和上面的登录状态。都 ✓ 就可以跑 --sample 了；"
          "如果 ✗，先跑 --login。")


def cmd_login(args):
    with sync_playwright() as p:
        ctx = open_browser(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for key in args.models:
            site = SITES[key]
            page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
            print(f"\n>>> 浏览器已打开 {site['name']}。请在窗口里完成登录。")
            input("    登录好之后回到这里按回车继续...")
            ok, why = check_login(page, site)
            print(f"    {'✓' if ok else '✗'} {why}")
        ctx.close()
    print(f"\n登录态已存进 {PROFILE_DIR}，之后跑题不用再登。")


def cmd_run(args):
    questions = load_questions(args.questions_file, args.sample, args.seed)
    done = {k: (done_ids(k) if args.resume else set()) for k in args.models}
    total = sum(len([q for q in questions if q.get("question_id") not in done[k]])
                for k in args.models)
    print(f"题目 {len(questions)} 道 (seed={args.seed}) | 站点 {len(args.models)} 个 | 待采 {total} 次")
    print("提示：跑的时候别动那个浏览器窗口。\n")

    stats = {k: {"ok": 0, "fail": 0, "skip": 0, "trunc": 0} for k in args.models}
    t_start = time.monotonic()

    with sync_playwright() as p:
        ctx = open_browser(p, headless=args.headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for key in args.models:
            site = SITES[key]
            # 开跑前先确认登录，避免白跑 50 题
            page.goto(site["url"], wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)
            ok, why = check_login(page, site)
            if not ok:
                print(f"[{site['name']}] 未登录（{why}），跳过。先跑 --login。")
                continue

            todo = [q for q in questions if q.get("question_id") not in done[key]]
            stats[key]["skip"] = len(questions) - len(todo)
            print(f"\n[{site['name']}] 待采 {len(todo)} 题")

            for i, q in enumerate(todo, 1):
                qid = q.get("question_id")
                qtext = ""
                try:
                    qtext = question_text(q)
                    answer, elapsed, truncated = ask_one(page, site, qtext, args.max_wait)
                    row = {
                        "question_id": qid,
                        "cancer_type": q.get("cancer_type"),
                        "question_type": q.get("question_type"),
                        "question": qtext,
                        "model": key,
                        "model_name": site["name"],
                        "channel": "local_rpa",
                        "response": answer,
                        "truncated": truncated,
                        "error": None,
                        "elapsed_s": elapsed,
                        "asked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    stats[key]["ok"] += 1
                    if truncated:
                        stats[key]["trunc"] += 1
                    flag = " [超时截断]" if truncated else ""
                    print(f"  [{i}/{len(todo)}] {qid} ok {elapsed}s len={len(answer)}{flag}")
                except Exception as exc:
                    row = {
                        "question_id": qid,
                        "cancer_type": q.get("cancer_type"),
                        "question_type": q.get("question_type"),
                        "question": qtext or q.get("question"),
                        "model": key,
                        "model_name": site["name"],
                        "channel": "local_rpa",
                        "response": None,
                        "error": str(exc),
                        "asked_at": datetime.now(timezone.utc).isoformat(),
                    }
                    stats[key]["fail"] += 1
                    print(f"  [{i}/{len(todo)}] {qid} FAILED: {exc}")
                    try:
                        SHOTS_DIR.mkdir(exist_ok=True)
                        page.screenshot(path=str(SHOTS_DIR / f"fail_{key}_{qid}.png"))
                    except Exception:
                        pass

                write_row(key, row)
                if i < len(todo):
                    time.sleep(args.interval)

        ctx.close()

    mins = round((time.monotonic() - t_start) / 60, 1)
    print(f"\n{'='*52}\n耗时 {mins} 分钟")
    for k in args.models:
        s = stats[k]
        print(f"  {SITES[k]['name']:<14} 成功 {s['ok']:>3}  失败 {s['fail']:>3}  "
              f"跳过 {s['skip']:>3}  截断 {s['trunc']:>3}  → {out_path(k).name}")
    if any(stats[k]["fail"] for k in args.models):
        print("\n有失败题目，看 shots/ 里的截图排错，然后 --resume 补跑。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", choices=list(SITES), default=list(SITES))
    ap.add_argument("--questions-file", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--sample", type=int, default=50)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--probe", action="store_true", help="只探测页面结构，不问问题")
    ap.add_argument("--login", action="store_true", help="开浏览器手动登录一次")
    ap.add_argument("--headless", action="store_true", help="无头模式（容易被风控，不建议）")
    ap.add_argument("--interval", type=float, default=5.0, help="题间间隔秒")
    ap.add_argument("--max-wait", type=int, default=240, help="单题最长等待秒")
    args = ap.parse_args()

    if args.probe:
        cmd_probe(args)
    elif args.login:
        cmd_login(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
