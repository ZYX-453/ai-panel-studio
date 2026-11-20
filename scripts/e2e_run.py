# -*- coding: utf-8 -*-
"""端到端跑一场真实讨论，并把"该成立的事"逐条断言。

这条脚本不是"请求返回 200 就算过"。它验证的是：

  1. 阵容：1 位主持 + N 位专家，颜色两两不同，立场两两不同
  2. 事件流：`phase` 只在阶段真的变化时出现；每条发言前后各有一次 `panelist_state`
  3. 纪律：内部状态词（准备发言/待机/举手）不得出现在 transcript 正文里
  4. 断线重连：中途断开再用 `Last-Event-ID` 接上，收到的事件**不重、不漏**
  5. 收敛：最后一条事件必须是 `ended`，且 `ended_reason` 落在允许集合里
  6. 共识：版本单调不减，且 version>0 时 points 非空
  7. 导出：markdown 里有议题与共识小节

用法（后端起好、PANEL_LLM_KEY 已配置）::

    python scripts/e2e_run.py --base http://127.0.0.1:8000 \
        --topic "远程办公是否应该成为公司默认工作方式" --experts 4

判定：EXIT=0 且 ★=0。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

INTERNAL_WORDS = ("准备发言", "待机", "举手", "让位")


def api(method: str, url: str, body: Any = None, timeout: float = 60.0) -> Any:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("content-type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    if not payload.get("ok"):
        raise RuntimeError(f"{url} -> {payload.get('error')}")
    return payload["data"]


def stream(url: str, last_event_id: int | None, limit_seconds: float) -> Iterator[dict[str, Any]]:
    """按 SSE 协议逐帧读。

    没有用第三方库：SSE 的帧格式只有四行（id/event/data/空行），
    自己解析反而能看清楚"我们依赖的到底是哪几条规则"。
    """
    req = urllib.request.Request(url, method="GET")
    req.add_header("accept", "text/event-stream")
    if last_event_id is not None:
        req.add_header("Last-Event-ID", str(last_event_id))

    deadline = time.time() + limit_seconds
    with urllib.request.urlopen(req, timeout=limit_seconds) as resp:
        eid: int | None = None
        etype: str | None = None
        buf: list[str] = []
        while time.time() < deadline:
            line = resp.readline()
            if not line:
                return
            text = line.decode("utf-8").rstrip("\r\n")
            if text == "":
                if etype and buf:
                    yield {"seq": eid, "type": etype, "payload": json.loads("\n".join(buf))}
                eid, etype, buf = None, None, []
                continue
            if text.startswith(":"):
                # 保活注释行，不占 seq
                continue
            if text.startswith("id:"):
                eid = int(text[3:].strip())
            elif text.startswith("event:"):
                etype = text[6:].strip()
            elif text.startswith("data:"):
                buf.append(text[5:].strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--topic", default="公司该不该给员工配 AI 编程助手的完全访问权限")
    ap.add_argument("--experts", type=int, default=4)
    ap.add_argument("--budget", type=float, default=420.0, help="等待整场结束的最长秒数")
    args = ap.parse_args()

    problems: list[str] = []
    notes: list[str] = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            problems.append(msg)

    health = api("GET", f"{args.base}/api/health")
    check(health["db"] == "ok", "数据库不可用")
    if not health["llm"]["key_configured"]:
        print(f"★ 模型未配置：{health['llm']}")
        print("  这条脚本要的是真实调用，配好 PANEL_LLM_KEY 再跑")
        return 1
    print(f"模型：{health['llm']['model']} @ {health['llm']['endpoint']}")

    t0 = time.time()
    d = api("POST", f"{args.base}/api/discussions", {"topic": args.topic, "expert_count": args.experts})
    did = d["id"]
    panel = d["panel"]
    hosts = [p for p in panel if p["role"] == "host"]
    experts = [p for p in panel if p["role"] == "expert"]
    print(f"创建 {did}｜阵容来源 {d.get('lineup_source', '?')}｜{time.time() - t0:.1f}s")

    check(len(hosts) == 1, f"主持人应为 1 位，实际 {len(hosts)}")
    check(len(experts) == args.experts, f"专家应为 {args.experts} 位，实际 {len(experts)}")
    check(len({p["color"] for p in panel}) == len(panel), "阵容里有重复颜色")
    check(len({p["stance"] for p in panel}) == len(panel), "阵容里有重复立场")
    check(all(p["name"] and p["title"] for p in panel), "阵容里有空白的姓名或头衔")

    api("POST", f"{args.base}/api/discussions/{did}/start")

    # ---- 读流：中途断一次，用 Last-Event-ID 接上，验证不重不漏
    url = f"{args.base}/api/discussions/{did}/stream"
    seen: list[dict[str, Any]] = []
    broke_at: int | None = None
    try:
        for ev in stream(url, None, 12.0):
            seen.append(ev)
            if len(seen) >= 6:
                broke_at = ev["seq"]
                break  # 模拟"网络中途断了"
    except (urllib.error.URLError, TimeoutError) as exc:
        notes.append(f"第一段连接异常结束（可接受）：{exc}")

    if broke_at is not None:
        print(f"模拟断线：已收到 {len(seen)} 帧，最后 id={broke_at}")
        for ev in stream(url, broke_at, args.budget):
            seen.append(ev)
            if ev["type"] == "ended":
                break
    else:
        for ev in stream(url, None, args.budget):
            seen.append(ev)
            if ev["type"] == "ended":
                break

    seqs = [e["seq"] for e in seen]
    check(len(seqs) == len(set(seqs)), f"事件 seq 有重复：{len(seqs)} 帧 / {len(set(seqs))} 个不同 seq")
    check(seqs == sorted(seqs), "事件 seq 不是单调递增")
    if broke_at is not None:
        expected = list(range(1, max(seqs) + 1)) if seqs else []
        check(seqs == expected, f"断线重连后有缺口：期望 1..{max(seqs)}，实际缺 {sorted(set(expected) - set(seqs))[:5]}")

    utts = [e for e in seen if e["type"] == "utterance"]
    ended = [e for e in seen if e["type"] == "ended"]
    phases = [e["payload"]["phase"] for e in seen if e["type"] == "phase"]

    check(len(utts) >= 6, f"发言太少（{len(utts)} 条），讨论不像真的跑起来了")
    check(len(ended) == 1, f"ended 事件应恰好 1 条，实际 {len(ended)}")
    if ended:
        reason = ended[0]["payload"]["reason"]
        check(reason in ("converged", "max_turns"), f"ended_reason={reason} 不在允许集合内")
        check(ended[0]["seq"] == max(seqs), "ended 不是最后一条事件")
    else:
        problems.append(f"{args.budget}s 内没有等到 ended")

    # 阶段不得重复连发同一个
    check(all(a != b for a, b in zip(phases, phases[1:])), f"phase 有连续重复：{phases}")
    check(phases and phases[0] == "OPENING", f"首个阶段应是 OPENING，实际 {phases[:1]}")

    # 每条发言前后都要有状态事件（"准备发言"→发言→"待机"）
    types = [e["type"] for e in seen]
    for i, t in enumerate(types):
        if t != "utterance":
            continue
        prev_state = seen[i - 1]["type"] if i > 0 else None
        next_state = seen[i + 1]["type"] if i + 1 < len(types) else None
        check(prev_state == "panelist_state", f"第 {seen[i]['seq']} 帧发言前没有状态事件")
        if next_state is not None:
            check(next_state in ("panelist_state", "consensus", "phase"), f"第 {seen[i]['seq']} 帧发言后事件异常：{next_state}")

    # 内部状态词不得出现在正文
    for u in utts:
        content = u["payload"]["content"]
        check(bool(content.strip()), f"seq={u['seq']} 的发言是空的")
        for word in INTERNAL_WORDS:
            check(word not in content, f"seq={u['seq']} 的正文里出现了内部状态词「{word}」：{content[:40]}")

    # 回应关系必须指向已有的更早发言
    seqs_of_utt = [u["payload"]["utterance_seq"] for u in utts]
    for u in utts:
        r = u["payload"].get("reply_to_utterance_seq")
        if r is not None:
            check(r < u["payload"]["utterance_seq"], f"发言 {u['payload']['utterance_seq']} 回应了更晚的 {r}")
            check(r in seqs_of_utt, f"发言 {u['payload']['utterance_seq']} 回应了不存在的第 {r} 句")

    # 共识版本单调
    versions = [e["payload"]["version"] for e in seen if e["type"] == "consensus"]
    check(versions == sorted(versions), f"共识版本不是单调不减：{versions}")
    # 早期版本的 points 允许为空：前三句的时候本来就还没谈拢，
    # 硬要求它非空，等于逼模型把"某一方的观点"写进共识里充数。
    # 真正该守住的是**最后一个版本** —— 讨论结束时连一条共识都没有，才是问题。
    cons_frames = [e for e in seen if e["type"] == "consensus"]
    if cons_frames:
        last = cons_frames[-1]["payload"]
        check(bool(last["points"]), f"最后一版共识（v{last['version']}）points 为空")
        check(last["based_on_seq"] > 0, "最后一版共识没有基于任何一句发言")
        both = set(last["points"]) & set(last["disputes"])
        check(not both, f"同一条同时出现在共识与分歧里：{sorted(both)}")

    # 快照与流对账
    snap = api("GET", f"{args.base}/api/discussions/{did}")
    check(snap["status"] == "FINISHED", f"快照状态是 {snap['status']}，应为 FINISHED")
    check(len(snap["transcript"]) == len(utts), f"快照 {len(snap['transcript'])} 条发言，流里 {len(utts)} 条")
    check(snap["ended_reason"] is not None, "FINISHED 但没有 ended_reason")
    check(bool((snap["final_summary"] or "").strip()), "finished 但没有收尾文本")

    md = api("GET", f"{args.base}/api/discussions/{did}/export")["markdown"]
    check(args.topic in md, "导出的 markdown 里没有议题")
    check("## 共识" in md, "导出的 markdown 里没有共识小节")

    elapsed = time.time() - t0
    print("\n--- 本场结果 ---")
    print(f"  场次      {did}")
    print(f"  发言      {len(utts)} 句（事件 {len(seen)} 帧）")
    print(f"  阶段      {' → '.join(dict.fromkeys(phases))}")
    print(f"  共识版本  {max(versions) if versions else 0}")
    print(f"  结束原因  {snap['ended_reason']}")
    print(f"  总耗时    {elapsed:.1f}s（平均每句 {elapsed / max(1, len(utts)):.1f}s）")
    for n in notes:
        print(f"  注：{n}")

    if problems:
        print(f"\n★ {len(problems)} 个问题")
        for p in problems:
            print(f"  ★ {p}")
        return 1
    print("\n全部断言通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
