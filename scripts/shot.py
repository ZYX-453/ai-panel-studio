# -*- coding: utf-8 -*-
"""按 DOM 条件截图（CDP）。

为什么不用 `msedge --headless --screenshot`：它只截一次、立刻返回。
本项目的首屏要等 `/api/discussions` 回来才长出列表，演播厅要等 SSE 补发
把事件喂进 applyEvent 才长出正文。命令行截图截到的一定是「骨架 + 空白」，
拿去当证据等于什么都没证明。

做法：开一个带 --remote-debugging-port 的 Edge，连上 WebSocket，
用 Runtime.evaluate 轮询 DOM 直到目标元素出现，再 Page.captureScreenshot。

四个已知坑（都在这份脚本里堵住了）：
  1. 复用用户 profile → 上次会话的缓存/同步会把页面搞成旧版本
     → 每次用全新的临时 profile，并加 --disable-sync
  2. Edge 退出在 Windows 上是滞后的，固定端口会连到上一个实例
     → 每次向系统要一个空闲端口
  3. 页面有内部滚动容器，整页 scrollHeight 量不出真实内容高度
     → 默认按视口截图（本应用三区各自滚动，视口就是所见）
  4. 截图成功但内容是旧的（上一次导航的残帧）
     → 改 URL 后先等 readyState=complete，再等选择器，且断言 URL 已变

用法：
    python scripts/shot.py --url http://127.0.0.1:5173/#/d_01 \
        --wait ".say" --out shots/studio.png --width 1440 --height 900
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket

EDGE_CANDIDATES = [
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
]


def find_browser() -> str:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    raise SystemExit("没找到 Edge/Chrome 可执行文件")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CDP:
    """一层薄封装。

    注意 `events`：CDP 的事件（console 输出、异常、网络）和命令响应共用一条
    WebSocket，命令响应带 id、事件不带。这里把不带 id 的攒起来，
    调用方才能事后拿到"页面到底报没报错"，而不是只能在当时同步处理。
    """

    def __init__(self, ws_url: str) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=30)
        self.seq = 0
        self.events: list[dict[str, Any]] = []

    def send(self, method: str, **params: Any) -> dict[str, Any]:
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.seq:
                return msg
            if msg.get("method"):
                self.events.append(msg)

    def pump(self) -> None:
        """把已经到达但还没读的事件收进 self.events，不发命令。

        用一个极短超时：没有事件就立刻返回，不会把检查脚本挂住。
        """
        self.ws.settimeout(0.2)
        try:
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("method"):
                    self.events.append(msg)
        except Exception:
            pass
        finally:
            self.ws.settimeout(30)

    def eval(self, expression: str) -> Any:
        r = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("result", {}).get("value")

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


def pick_page(port: int) -> str:
    """从 /json/list 里挑一个 page 目标。挑不到就报错，绝不拿别的目标凑数。"""
    for _ in range(50):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as r:
                targets = json.load(r)
            pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.2)
    raise SystemExit("没有可用的 page 目标")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--wait", default="body", help="等这个选择器出现再截图")
    ap.add_argument("--wait-text", default="", help="再等页面上出现这段文字")
    ap.add_argument("--width", type=int, default=1440)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--settle", type=float, default=0.4, help="目标出现后再等几秒（CSS 过渡）")
    ap.add_argument("--eval-before", default="", help="截图前执行的一段 JS，可用来滚动、展开")
    ap.add_argument(
        "--min-nodes",
        default="",
        help="等某选择器的节点数达标再截，写法 '.say=24'（防止截到数据还没填完的中间态）",
    )
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="panel-shot-"))
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
            f"--window-size={args.width},{args.height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cdp: CDP | None = None
    try:
        cdp = CDP(pick_page(port))
        cdp.send("Page.enable")
        cdp.send(
            "Emulation.setDeviceMetricsOverride",
            width=args.width,
            height=args.height,
            deviceScaleFactor=1,
            mobile=False,
        )
        cdp.send("Page.navigate", url=args.url)

        deadline = time.time() + args.timeout
        ready = False
        while time.time() < deadline:
            state = cdp.eval("document.readyState")
            if state == "complete":
                found = cdp.eval(
                    "(() => { const el = document.querySelector(%s); return !!(el && el.offsetHeight > 0) })()"
                    % json.dumps(args.wait)
                )
                if found:
                    ready = True
                    break
            time.sleep(0.2)
        if not ready:
            raise SystemExit(f"等待超时：{args.wait} 没有出现（{args.url}）")

        if args.wait_text:
            text = json.dumps(args.wait_text, ensure_ascii=False)
            while time.time() < deadline:
                if cdp.eval(f"document.body.innerText.includes({text})"):
                    break
                time.sleep(0.2)
            else:
                raise SystemExit(f"等待超时：页面上没有出现「{args.wait_text}」")

        # 等节点数量到位。演播厅先渲染骨架、再由 SSE 一帧帧填发言，
        # 只等"某个元素出现"会截到回放中途 —— 图上 6 条、库里 24 条。
        if args.min_nodes:
            selector, _, want = args.min_nodes.rpartition("=")
            want_n = int(want)
            while time.time() < deadline:
                got = cdp.eval(
                    "document.querySelectorAll(%s).length" % json.dumps(selector)
                )
                if isinstance(got, int) and got >= want_n:
                    break
                time.sleep(0.2)
            else:
                raise SystemExit(
                    f"等待超时：{selector} 只到 {got} 个，期望 ≥ {want_n}"
                )

        # 断言真的落在目标页上，防止截到上一次导航的残帧
        href = cdp.eval("location.href")
        if args.url.split("#")[-1] and args.url.split("#")[-1] not in str(href):
            raise SystemExit(f"页面停在 {href}，不是目标 {args.url}")

        # 截图前的可选操作：比如把发言区滚回顶部，让截图从第一句开始，
        # 而不是停在"跟随最新一句"自动滚到的底部
        if args.eval_before:
            cdp.eval(args.eval_before)
            time.sleep(0.3)

        time.sleep(args.settle)
        shot = cdp.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        data = shot["result"]["data"]
        out.write_bytes(base64.b64decode(data))
        print(f"OK {out} ({out.stat().st_size // 1024} KB) ← {href}")
        return 0
    finally:
        if cdp:
            cdp.close()
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
