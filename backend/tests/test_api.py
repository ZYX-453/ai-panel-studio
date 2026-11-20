"""HTTP 契约测试。

只测"接口边界"，不测引擎推进 —— 后者在 test_engine 里用假模型跑。
这里关心的是：非法输入能不能被挡住、状态机守卫是否生效、
事件流在讨论已结束时会不会立刻补齐而不是挂住。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import api
from app.llm import ChatResult, LLMError, Usage
from app.store import Store


class AlwaysFailLLM:
    """没配 Key 的场景。用来验证接口不会因此 500，而是给出可判断的错误。"""

    configured = False

    def describe(self):
        return {"endpoint": "https://api.deepseek.com", "model": "deepseek-chat",
                "key_configured": False}

    def chat(self, *a, **kw):
        raise LLMError("未配置 PANEL_LLM_KEY", retryable=False)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    s = Store(str(tmp_path / "api.db"))
    s.init_schema()
    api.configure(store=s, client=AlwaysFailLLM())
    with TestClient(api.app) as c:
        yield c
    s.close()


def test_health_reports_llm_without_key(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["data"]["db"] == "ok"
    # 关键：响应体里绝不能出现 Key 的痕迹
    assert "key" not in json.dumps(body).lower().replace("key_configured", "")


def test_create_requires_valid_topic(client: TestClient) -> None:
    r = client.post("/api/discussions", json={"topic": "短", "expert_count": 4})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_TOPIC"


def test_create_rejects_out_of_range_count(client: TestClient) -> None:
    r = client.post("/api/discussions", json={"topic": "一个足够长的议题", "expert_count": 9})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_COUNT"


def test_create_falls_back_to_template_when_llm_unavailable(client: TestClient) -> None:
    """模型不可用不该让用户卡住：退到确定性人设池，并如实标注来源。"""
    r = client.post("/api/discussions", json={"topic": "远程办公是否应成为默认", "expert_count": 4})
    assert r.status_code == 201
    data = r.json()["data"]
    assert data["status"] == "AWAITING_CONFIRM"
    assert data["lineup_source"] == "template"
    assert len(data["panel"]) == 5

    stances = [p["stance"] for p in data["panel"] if p["role"] == "expert"]
    assert len(set(stances)) == len(stances), "回退阵容里也不许出现重复立场"

    colors = [p["color"] for p in data["panel"]]
    assert len(set(colors)) == len(colors)


def test_unknown_discussion_returns_404(client: TestClient) -> None:
    assert client.get("/api/discussions/d_nope").status_code == 404
    assert client.post("/api/discussions/d_nope/start").status_code == 404
    assert client.get("/api/discussions/d_nope/stream").status_code == 404


def test_start_then_second_start_conflicts(client: TestClient) -> None:
    did = client.post(
        "/api/discussions", json={"topic": "要不要引入代码评审制度", "expert_count": 3}
    ).json()["data"]["id"]

    first = client.post(f"/api/discussions/{did}/start")
    assert first.status_code == 202
    # 已经 LIVE，再点一次必须被挡住，否则会起第二个引擎线程
    second = client.post(f"/api/discussions/{did}/start")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "NOT_CONFIRMABLE"


def test_regenerate_only_allowed_before_start(client: TestClient) -> None:
    did = client.post(
        "/api/discussions", json={"topic": "要不要引入代码评审制度", "expert_count": 3}
    ).json()["data"]["id"]
    r = client.post(f"/api/discussions/{did}/regenerate", json={"seed": 3})
    assert r.status_code == 200
    assert len(r.json()["data"]["panel"]) == 4

    client.post(f"/api/discussions/{did}/start")
    r2 = client.post(f"/api/discussions/{did}/regenerate", json={"seed": 4})
    assert r2.status_code == 409


def test_stream_replays_history_for_finished_discussion(client: TestClient) -> None:
    """已结束的讨论：连上去应该把历史补齐然后立刻关闭，而不是挂住等新事件。"""
    did = client.post(
        "/api/discussions", json={"topic": "要不要引入代码评审制度", "expert_count": 3}
    ).json()["data"]["id"]
    client.post(f"/api/discussions/{did}/start")

    # 模型不可用 → 连续失败后降级结束，是确定的终态
    with client.stream("GET", f"/api/discussions/{did}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("x-accel-buffering") == "no"
        body = "".join(resp.iter_text())

    assert "event: ended" in body
    # 保活必须是注释行，不能伪装成业务事件
    assert "event: ping" not in body


def test_export_returns_markdown(client: TestClient) -> None:
    did = client.post(
        "/api/discussions", json={"topic": "要不要引入代码评审制度", "expert_count": 3}
    ).json()["data"]["id"]
    md = client.get(f"/api/discussions/{did}/export").json()["data"]["markdown"]
    assert "要不要引入代码评审制度" in md
    assert "## 共识" in md


def test_list_marks_status(client: TestClient) -> None:
    client.post("/api/discussions", json={"topic": "要不要引入代码评审制度", "expert_count": 3})
    rows = client.get("/api/discussions").json()["data"]
    assert rows and rows[0]["status"] in ("AWAITING_CONFIRM", "LIVE", "FINISHED")
    assert "expert_count" in rows[0]


def test_two_discussions_do_not_collide_on_panelist_id(client: TestClient) -> None:
    """同一个库里建第二场，不许撞 panelist 主键。

    真实踩到的：`build_from_pool` 给的 id 是 "p0"/"p1"（本场第几号座），
    而 `panelist.id` 是全局主键。第一场建完、第一场 24 轮跑完，再建第二场
    就 `UNIQUE constraint failed: panelist.id` → 接口 500。
    单场用例永远碰不到，必须连着建两场才能钉住。
    """
    first = client.post("/api/discussions", json={"topic": "要不要推行四天工作制", "expert_count": 3})
    assert first.status_code == 201, first.text
    second = client.post("/api/discussions", json={"topic": "该不该给新人配完整仓库权限", "expert_count": 3})
    assert second.status_code == 201, second.text

    a = {p["id"] for p in first.json()["data"]["panel"]}
    b = {p["id"] for p in second.json()["data"]["panel"]}
    assert len(a) == 4 and len(b) == 4
    assert a & b == set(), f"两场阵容共用了 id：{a & b}"


def test_regenerate_keeps_panel_ids_inside_discussion(client: TestClient) -> None:
    """重掷阵容同样要绑场次，且不能把别的场的座位 id 撞掉。"""
    other = client.post("/api/discussions", json={"topic": "先占住一套模板阵容", "expert_count": 4})
    assert other.status_code == 201

    did = client.post(
        "/api/discussions", json={"topic": "重掷之后阵容应当换人", "expert_count": 4}
    ).json()["data"]["id"]
    r = client.post(f"/api/discussions/{did}/regenerate", json={"seed": 3})
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["data"]["panel"]]
    assert all(i.startswith(f"{did}_") for i in ids), ids
    assert len(set(ids)) == len(ids)


# ------------------------------------------------------- SSE 事件名的转义


def test_sse_frame_renames_business_error_event() -> None:
    """业务 `error` 不能原样上线：`error` 是 EventSource 的保留事件名。

    浏览器的 `EventSource` 在连接断开时派发的事件也叫 `error`。业务里的
    `error`（某一句生成失败）如果同名上线，前端一个监听器同时收到两种东西，
    分不清"连接断了"和"有句话没生成出来"。实测后果：断线判断被业务事件污染，
    空转重连的计数永远清不到阈值，页面从打开起一直挂着「正在重连」。
    """
    from app.api import _sse_frame

    frame = _sse_frame({"seq": 7, "type": "error", "payload": {"message": "一句失败"}})
    assert "event: panel_error\n" in frame
    assert "event: error\n" not in frame


def test_sse_frame_leaves_other_types_alone() -> None:
    """只给 error 开例外，别顺手把别的类型也改名 —— 前端按类型分发。"""
    from app.api import _sse_frame

    for t in ("phase", "panelist_state", "utterance", "consensus", "ended"):
        frame = _sse_frame({"seq": 1, "type": t, "payload": {}})
        assert f"event: {t}\n" in frame, t


# ------------------------------------------------------- SSE 事件名的转义


def test_sse_frame_renames_business_error_event() -> None:
    """业务 `error` 不能原样上线：`error` 是 EventSource 的保留事件名。

    浏览器的 `EventSource` 在连接断开时派发的事件也叫 `error`。业务里的
    `error`（某一句生成失败）如果同名上线，前端一个监听器同时收到两种东西，
    分不清"连接断了"和"有句话没生成出来"。实测后果：断线判断被业务事件污染，
    空转重连的计数永远清不到阈值，页面从打开起一直挂着「正在重连」。
    """
    from app.api import _sse_frame

    frame = _sse_frame({"seq": 7, "type": "error", "payload": {"message": "一句失败"}})
    assert "event: panel_error\n" in frame
    assert "event: error\n" not in frame


def test_sse_frame_leaves_other_types_alone() -> None:
    """只给 error 开例外，别顺手把别的类型也改名 —— 前端按类型分发。"""
    from app.api import _sse_frame

    for t in ("phase", "panelist_state", "utterance", "consensus", "ended"):
        frame = _sse_frame({"seq": 1, "type": t, "payload": {}})
        assert f"event: {t}\n" in frame, t
