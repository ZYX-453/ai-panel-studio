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
