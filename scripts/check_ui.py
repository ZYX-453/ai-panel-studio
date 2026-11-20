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


def check_page(cdp: CDP, url: str, wait_sel: str, label: str, *, expect_no_scroll: bool) -> list[str]:
    problems: list[str] = []
    cdp.events.clear()
    goto(cdp, url, wait_sel)
    m = metrics(cdp)

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
    print("全部通过：无横向溢出、无整页滚动、无原始数据泄漏、控制台无报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
