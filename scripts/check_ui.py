# -*- coding: utf-8 -*-
"""界面门禁：宽度、滚动、文本、控制台。

这条脚本回答的是「界面在别人的屏幕上会不会烂掉」，而不是「接口返回 200 没有」。
四条门禁，每一条都对应一次真实踩坑：

1. **横向溢出**：`documentElement.scrollWidth > innerWidth`
   窄屏时表格/长英文串会把整页撑出横向滚动条。用户要的是"不依赖整页滚动"。

2. **整页纵向滚动**：`scrollHeight > clientHeight`
   演播厅是三区各自滚动的。如果整页也能滚，说明某个区的高度没被约束住，
   用户滚动滚轮时动的是整页而不是那一个区 —— 观感上像卡住。

3. **原始 JSON / undefined / NaN 出现在正文**
   后端信封是 `{ok, data}`，前端一旦漏了 adapter 就会把 `{"ok":true,...}`
   直接铺在页面上；空值拼接会写出 `NaN`。这两种都是"接口通了但界面崩了"。

4. **控制台报错**：`console.error` / 未捕获异常
   演示时 F12 一片红是最难解释的。

5. **发言长度**：`<p class="say-text">` 里每一句都不得超过 65 字
   需求写的是"1-2 句、不超过 60 字"。引擎那一端是"代码量长度、超了让它重说"，
   这一条负责证明它真的拦住了 —— 约束写进 prompt 不等于成立，得有人量。
   阈值取 65 而不是 60：引擎故意留了 5 字余量（模型不数数），
   门禁跟代码同口径，否则就是凭空造出第二套标准。

用法（前后端都要先起来）::

    python scripts/check_ui.py --base http://127.0.0.1:5173 --did d_01

判定：EXIT=0 且 ★=0。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shot import CDP, find_browser, free_port, pick_page  # noqa: E402

WIDTHS = [(1440, 900), (1366, 768), (1280, 800), (1024, 768)]

BAD_TEXT = ["{\"ok\"", "\"data\":", "undefined", "NaN", "[object Object]", "null%"]

#: 单句发言的硬上限，必须与 `backend/app/engine.py` 的 `MAX_SPEECH_CHARS` 一致。
#: prompt 里写的是 60，代码故意留了 5 字余量（模型不数数，卡死 60 会让几乎每句都触发
#: 重说）。门禁只能跟代码同口径 —— 卡 60 就是凭空造出第二套标准，红得没有意义。
MAX_SPEECH_CHARS = 65

#: 软上限：超过就在报告里点名，但不判失败。用来暴露"贴着上限写"的趋势。
SOFT_SPEECH_CHARS = 60


def launch(width: int, height: int) -> tuple[subprocess.Popen, str, Path]:
    profile = Path(tempfile.mkdtemp(prefix="panel-gate-"))
    port = free_port()
    proc = subprocess.Popen(
        [
            find_browser(),
            "--headless=new",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--disable-sync",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--force-device-scale-factor=1",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc, pick_page(port), profile


def goto(cdp: CDP, url: str, wait_sel: str, timeout: float = 25.0) -> None:
    cdp.send("Page.navigate", url=url)
    deadline = time.time() + timeout
    while time.time() < deadline:
        cdp.pump()
        if cdp.eval("document.readyState") == "complete":
            ok = cdp.eval(
                "(() => { const e = document.querySelector(%s); return !!(e && e.offsetHeight > 0) })()"
                % __import__("json").dumps(wait_sel)
            )
            if ok:
                time.sleep(0.5)
                cdp.pump()
                return
        time.sleep(0.15)
    raise RuntimeError(f"等不到 {wait_sel}：{url}")


def metrics(cdp: CDP) -> dict[str, Any]:
    return cdp.eval(
        "({ sw: document.documentElement.scrollWidth, iw: window.innerWidth,"
        " sh: document.documentElement.scrollHeight, ih: window.innerHeight,"
        " text: document.body.innerText })"
    )


def console_errors(cdp: CDP) -> list[str]:
    out: list[str] = []
    for ev in cdp.events:
        method = ev.get("method")
        params = ev.get("params", {})
        if method == "Runtime.exceptionThrown":
            d = params.get("exceptionDetails", {})
            out.append(f"未捕获异常：{d.get('text')} {d.get('exception', {}).get('description', '')}".strip())
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            args = " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))
            out.append(f"console.error：{args[:200]}")
    return out


def fill_gap(cdp: CDP) -> float | None:
    """演播厅三栏底部离视口底部还有多少像素。

    为什么单独量这个：`scrollHeight == clientHeight` 只能证明"不滚动"，
    证明不了"撑满"。实测的缺陷是这样的 —— `.studio` 用 `grid-template-rows:
    auto auto 1fr` 排三行，而桌面端 `.studio-tabs` 是 `display:none`，
    它不参与布局，于是 `.studio-body` 落到第 2 行（auto）而非第 3 行（1fr）。
    24 句时内容自己就把高度顶满了，看不出问题；降级收尾那种 0 句的场次，
    三栏只长到 520px，下面 380px 整片留白。**门禁当时全绿。**
    """
    box = cdp.eval(
        "(()=>{const b=document.querySelector('.studio-body');"
        "if(!b) return null;const r=b.getBoundingClientRect();"
        "return {bottom:r.bottom, ih:window.innerHeight, h:r.height};})()"
    )
    if not box:
        return None
    return float(box["ih"]) - float(box["bottom"])


def check_fill(cdp: CDP, label: str) -> str | None:
    """清空三栏内容后再量：布局不许跟着内容一起塌下去。

    清空动作只是探针，跑完立刻恢复（同一个页面后续还要继续检查页签）。
    """
    restore = "document.querySelectorAll('.pane-scroll,.tr,.final,.studio-notice').forEach(e=>{e.style.display=e.dataset.disp||''})"
    cdp.eval(
        "document.querySelectorAll('.pane-scroll,.tr,.final,.studio-notice')"
        ".forEach(e=>{e.dataset.disp=e.style.display||'';e.style.display='none'})"
    )
    try:
        gap = fill_gap(cdp)
    finally:
        cdp.eval(restore)

    if gap is None:
        return f"[{label}] 找不到 .studio-body，无法判断三栏是否撑满"
    if gap > 2:
        return f"[{label}] 内容清空后三栏没撑满：底部空出 {gap:.0f}px（布局跟着内容塌了）"
    return None


def check_speech(cdp: CDP, label: str) -> list[str]:
    """演播厅里每一句发言都不能超长。

    这条门禁守的是"只说 1-2 句、不超过 60 字"在**界面上**真的成立。
    引擎那一端是"代码量长度、超了让它重说"，门禁这一端负责证明它真的拦住了 ——
    这跟布局那条是同一个教训：约束写进 prompt 不等于成立，得有人量。

    之所以要放在界面上量而不是直接查库：用户看到的是 `<p class="say-text">`，
    如果渲染层做了截断（或者反过来拼接了前缀），库里的长度就不代表屏幕上的长度。
    """
    texts = cdp.eval(
        "(()=>[...document.querySelectorAll('.say-text')]"
        "  .map(e => (e.textContent || '').trim()))()"
    )
    if not texts:
        # 0 句的时候长度门禁恒真，等于没测。宁可报红也不要给假绿。
        return [f"[{label}] 演播厅里 0 句发言，长度门禁无从判断"]

    problems: list[str] = []
    too_long = [t for t in texts if len(t) > MAX_SPEECH_CHARS]
    if too_long:
        worst = max(too_long, key=len)
        problems.append(
            f"[{label}] {len(too_long)}/{len(texts)} 句超过 {MAX_SPEECH_CHARS} 字，"
            f"最长 {len(worst)} 字：「{worst[:40]}…」"
        )
    near = [t for t in texts if SOFT_SPEECH_CHARS < len(t) <= MAX_SPEECH_CHARS]
    if near:
        print(
            f"  · [{label}] {len(near)} 句落在 {SOFT_SPEECH_CHARS+1}-{MAX_SPEECH_CHARS} 字"
            f"（未越界，但已顶到上限）：最长 {max(len(t) for t in near)} 字"
        )
    return problems


def check_page(
    cdp: CDP,
    url: str,
    wait_sel: str,
    label: str,
    *,
    expect_no_scroll: bool,
    require_fill: bool = False,
    check_speech_len: bool = False,
) -> list[str]:
    problems: list[str] = []
    cdp.events.clear()
    goto(cdp, url, wait_sel)
    m = metrics(cdp)

    # 长度检查必须在 check_fill 之前：后者会清空三栏内容来造"内容少"的条件，
    # 清完就一句发言都不剩了。
    if check_speech_len:
        problems += check_speech(cdp, label)
        m = metrics(cdp)

    if require_fill:
        # 只量"当前这一场"是不够的：d_01 有 11 句，内容自己就把高度顶满了，
        # 布局即使按内容长也看不出问题 —— 我第一版门禁就是这么写的，
        # 把 CSS 改回 grid 缺陷版仍然全绿，等于没测。
        # 真正该守的是「内容少时也必须撑满」，所以先清空三栏内容再造这个条件。
        problem = check_fill(cdp, label)
        if problem:
            problems.append(problem)

    if m["sw"] > m["iw"] + 1:
        problems.append(f"[{label}] 横向溢出：scrollWidth={m['sw']} > innerWidth={m['iw']}")

    if expect_no_scroll and m["sh"] > m["ih"] + 1:
        problems.append(f"[{label}] 整页可纵向滚动：scrollHeight={m['sh']} > innerHeight={m['ih']}")

    for bad in BAD_TEXT:
        if bad in m["text"]:
            problems.append(f"[{label}] 正文里出现了原始数据/占位文本：{bad!r}")

    for err in console_errors(cdp):
        problems.append(f"[{label}] {err}")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5173")
    ap.add_argument("--did", default="d_01", help="用于演播厅检查的讨论 id")
    ap.add_argument("--only", default="", help="只跑某个宽度，如 1280")
    args = ap.parse_args()

    problems: list[str] = []
    checked = 0

    for width, height in WIDTHS:
        if args.only and str(width) != args.only:
            continue
        proc, ws, profile = launch(width, height)
        cdp: CDP | None = None
        try:
            cdp = CDP(ws)
            cdp.send("Page.enable")
            cdp.send("Runtime.enable")
            cdp.send("Log.enable")
            cdp.send(
                "Emulation.setDeviceMetricsOverride",
                width=width,
                height=height,
                deviceScaleFactor=1,
                mobile=False,
            )

            # 首页：允许整页滚动（列表可以很长），但不能横向溢出
            problems += check_page(cdp, f"{args.base}/", ".drow", f"{width} 首页", expect_no_scroll=False)
            # 演播厅：整页不许滚，三区各自滚
            problems += check_page(
                cdp,
                f"{args.base}/#/d/{args.did}",
                ".say",
                f"{width} 演播厅",
                expect_no_scroll=True,
                require_fill=True,
                check_speech_len=True,
            )

            # 窄屏页签：三区必须能切换，且同一时刻只显示一个
            if width < 1100:
                tabs = cdp.eval("document.querySelectorAll('.studio-tab').length")
                if tabs != 3:
                    problems.append(f"[{width} 窄屏] 页签数量是 {tabs}，应为 3")
                cdp.eval("document.querySelectorAll('.studio-tab')[2].click()")
                time.sleep(0.3)
                pane = cdp.eval("document.querySelector('.studio').dataset.pane")
                if pane != "consensus":
                    problems.append(f"[{width} 窄屏] 点「共识」页签后 data-pane={pane}，应为 consensus")
                m = metrics(cdp)
                if m["sw"] > m["iw"] + 1:
                    problems.append(f"[{width} 窄屏] 切到共识后横向溢出：{m['sw']} > {m['iw']}")
            checked += 1
        except Exception as exc:  # 门禁脚本自己出错也要报红，不能静默跳过
            problems.append(f"[{width}px] 检查过程异常：{exc}")
        finally:
            if cdp:
                cdp.close()
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            shutil.rmtree(profile, ignore_errors=True)

    print(f"检查宽度：{checked} 组")
    if problems:
        print(f"★ {len(problems)} 个问题")
        for p in problems:
            print(f"  ★ {p}")
        return 1
    print("全部通过：三栏撑满、无横向溢出、无整页滚动、无原始数据泄漏、发言不超长、控制台无报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
