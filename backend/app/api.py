"""HTTP 层。

这层只做四件事：参数校验、状态机守卫、组装响应、把引擎的事件转成 SSE。
**不含任何业务判断** —— 谁该发言、讨论是否结束，都在 engine 里，
放在这里会让"同一件事有两处实现"。

统一信封 {ok, data} / {ok, error}：前端只需写一次错误分支，
不会出现"某个端点忘了处理失败"。
"""

from __future__ import annotations

import json
import queue
import uuid
from typing import Any, Iterator

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import lineup as lineup_mod
from .engine import Engine, EngineConfig, EventBus
from .llm import LLMClient
from .store import Store

#: SSE 保活间隔。代理常在 30-60s 断空闲连接，15s 留足余量
PING_SECONDS = 15.0

store = Store()
store.init_schema()
client = LLMClient()
bus = EventBus()
engine = Engine(store, client, bus)


def configure(*, store: Store | None = None, client: LLMClient | None = None) -> None:
    """替换运行期依赖。

    存在两个原因：测试需要一个干净的临时库（不能污染 data/panel.db）；
    以及未来换库换供应商时不必改端点代码。
    """
    g = globals()
    if store is not None:
        g["store"] = store
    if client is not None:
        g["client"] = client
    g["engine"] = Engine(g["store"], g["client"], g["bus"])


app = FastAPI(title="AI Panel Studio", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- 响应


def ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def fail(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code, "message": message}}, status_code=status)


# --------------------------------------------------------------------- 端点


@app.get("/api/health")
def health() -> JSONResponse:
    db_ok = True
    try:
        store.conn.execute("SELECT 1").fetchone()
    except Exception:
        db_ok = False
    return ok(
        {
            "db": "ok" if db_ok else "error",
            # 只报端点与模型名，绝不回显 Key
            "llm": client.describe(),
        }
    )


@app.get("/api/discussions")
def list_discussions() -> JSONResponse:
    rows = store.list_discussions()
    return ok(
        [
            {
                "id": r["id"],
                "topic": r["topic"],
                "status": r["status"],
                "ended_reason": r["ended_reason"],
                "expert_count": r["expert_count"],
                "utterance_count": r["utterance_count"],
                "consensus_version": r["consensus_version"] or 0,
                "turn_count": r["turn_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    )


@app.post("/api/discussions")
async def create_discussion(req: Request) -> JSONResponse:
    body = await req.json()
    topic = str(body.get("topic") or "").strip()
    try:
        expert_count = int(body.get("expert_count", 4))
    except (TypeError, ValueError):
        return fail("INVALID_COUNT", "人数必须是整数", 400)

    if not (2 <= len(topic) <= 200):
        return fail("INVALID_TOPIC", "议题长度需在 2-200 字之间", 400)
    if not (2 <= expert_count <= 6):
        return fail("INVALID_COUNT", "专家人数需在 2-6 之间", 400)

    panel, source = lineup_mod.generate(
        client, topic=topic, expert_count=expert_count, seed=0
    )
    did = f"d_{uuid.uuid4().hex[:8]}"
    # 阵容自带的是"本场第几号座"，入库前必须绑到场次上（panelist.id 是全局主键）
    panel = lineup_mod.bind_to_discussion(panel, did)
    store.create_discussion(
        did=did,
        topic=topic,
        expert_count=expert_count,
        max_turns=24,
        panel=panel,
        config={"lineup_source": source},
    )
    snap = store.snapshot(did)
    snap["lineup_source"] = source
    snap["lineup_source_note"] = lineup_mod.describe_source(source)
    return ok(snap, status=201)


@app.post("/api/discussions/{did}/regenerate")
async def regenerate(did: str, req: Request) -> JSONResponse:
    d = store.get_discussion(did)
    if not d:
        return fail("DISCUSSION_NOT_FOUND", "讨论不存在", 404)
    if d["status"] != "AWAITING_CONFIRM":
        return fail("NOT_CONFIRMABLE", "只有等待确认的阵容可以重掷", 409)

    body = await req.json() if await req.body() else {}
    seed = int(body.get("seed") or (len(d["created_at"]) + uuid.uuid4().int % 97))
    panel, source = lineup_mod.generate(
        client, topic=d["topic"], expert_count=int(d["expert_count"]), seed=seed
    )
    # 同 create：不绑场次就会与别场的 p0/p1 撞主键
    panel = lineup_mod.bind_to_discussion(panel, did)
    store.replace_panel(did, panel)
    snap = store.snapshot(did)
    snap["lineup_source"] = source
    return ok(snap)


@app.post("/api/discussions/{did}/start")
def start(did: str) -> JSONResponse:
    if not store.get_discussion(did):
        return fail("DISCUSSION_NOT_FOUND", "讨论不存在", 404)
    if not engine.start(did):
        return fail("NOT_CONFIRMABLE", "该讨论不在等待确认状态", 409)
    # 202：讨论交给后台线程推进，不在这个请求里同步跑完
    return ok({"id": did, "status": "LIVE"}, status=202)


@app.get("/api/discussions/{did}")
def get_snapshot(did: str) -> JSONResponse:
    snap = store.snapshot(did)
    if not snap:
        return fail("DISCUSSION_NOT_FOUND", "讨论不存在", 404)
    return ok(snap)


@app.get("/api/discussions/{did}/export")
def export_markdown(did: str) -> JSONResponse:
    snap = store.snapshot(did)
    if not snap:
        return fail("DISCUSSION_NOT_FOUND", "讨论不存在", 404)
    names = {p["id"]: p["name"] for p in snap["panel"]}
    lines = [f"# {snap['topic']}", ""]
    for u in snap["transcript"]:
        lines.append(f"**{names.get(u['panelist_id'], u['panelist_id'])}**：{u['content']}")
        lines.append("")
    c = snap["consensus"]
    lines += ["---", "", f"## 共识（v{c['version']}）", ""]
    lines += [f"- {p}" for p in c["points"]] or ["（无）"]
    lines += ["", "## 分歧", ""]
    lines += [f"- {d}" for d in c["disputes"]] or ["（无）"]
    if snap.get("final_summary"):
        lines += ["", "## 收尾", "", snap["final_summary"]]
    return ok({"markdown": "\n".join(lines)})


# ----------------------------------------------------------------------- SSE


#: 业务事件类型 → SSE 线上 event 名。
#:
#: 目前只有一条：`error` 是 EventSource 的**保留事件名**，浏览器在连接断开时
#: 也会派发一个名为 `error` 的事件。业务里的 `error`（某一句生成失败）
#: 如果原样上线，两个语义就挤在同一个监听器里 —— 前端分不清"连接断了"和
#: "有一句没生成出来"，实测到的后果是断线判断被业务事件污染，
#: 空转重连的计数永远清不到阈值，页面挂着「正在重连」直到讨论结束。
#:
#: 数据库里的事件类型仍然是 `error`（单一真相源不动），只在传输层转义。
SSE_EVENT_ALIAS = {"error": "panel_error"}


def _sse_frame(event: dict[str, Any]) -> str:
    """SSE 帧。id 直接用 seq —— 浏览器断线重连会自动带 Last-Event-ID，
    于是「直播」和「补发/回放」走的是同一条渲染管线。"""
    payload = json.dumps(event["payload"], ensure_ascii=False)
    name = SSE_EVENT_ALIAS.get(event["type"], event["type"])
    return f"id: {event['seq']}\nevent: {name}\ndata: {payload}\n\n"


@app.get("/api/discussions/{did}/stream")
def stream(
    did: str,
    from_seq: int = 0,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    d = store.get_discussion(did)
    if not d:
        return JSONResponse(
            {"ok": False, "error": {"code": "DISCUSSION_NOT_FOUND", "message": "讨论不存在"}},
            status_code=404,
        )

    start = max(from_seq, int(last_event_id or 0))

    def gen() -> Iterator[str]:
        # 先订阅再补发：否则补发期间产生的新事件会掉在缝里
        q = bus.subscribe(did)
        try:
            # 告诉 EventSource 断线后 2 秒重连，不要用默认的 3 秒乱试探
            yield "retry: 2000\n\n"
            last = start
            for ev in store.get_events(did, start):
                yield _sse_frame(ev)
                last = ev["seq"]

            status = store.get_discussion(did)["status"]
            if status in ("FINISHED", "FAILED"):
                return

            while True:
                try:
                    ev = q.get(timeout=PING_SECONDS)
                except queue.Empty:
                    # 保活是传输层信号：以注释行发出，不占 seq、不入库
                    yield ": ping\n\n"
                    continue
                if ev["seq"] <= last:
                    continue
                yield _sse_frame(ev)
                last = ev["seq"]
                if ev["type"] == "ended":
                    return
        finally:
            bus.unsubscribe(did, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 禁用 Nginx 之类反向代理的缓冲，否则事件会攒着一起发
            "X-Accel-Buffering": "no",
        },
    )
