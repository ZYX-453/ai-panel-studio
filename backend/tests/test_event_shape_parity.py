"""两侧同源的一致性测试。

问题背景（真实踩到的）：
同一个「发言」事件有两个产生者 —— 运行时由 `app/engine.py` 写出，
样例数据由 `scripts/init_db.py` 投影出来。两边各自拼字典，
字段名一开始就不一样（一边 `seq`、一边 `utterance_seq`）。

这种分叉在测试里看不出来（各自都"能用"），在演示时也看不出来，
只有把两个来源的事件喂给同一段前端代码时才会有一边渲染空白。
所以这里直接钉死：**两边的 payload 字段集必须逐字相同**。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.init_db import project_events  # noqa: E402

from app.engine import Engine, EngineConfig, EventBus  # noqa: E402
from app.lineup import build_from_pool, validate  # noqa: E402
from app.store import Store  # noqa: E402

from tests.test_engine import FakeLLM  # noqa: E402


@pytest.fixture()
def seeded_db(tmp_path) -> sqlite3.Connection:
    """用正式 DDL + seed 建一个临时库，再投影事件。"""
    db = tmp_path / "seeded.db"
    conn = sqlite3.connect(str(db))
    conn.executescript((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((ROOT / "db" / "seed.sql").read_text(encoding="utf-8"))
    project_events(conn)
    conn.commit()
    yield conn
    conn.close()


def _keys(db_path: str, did_prefix: str = "") -> dict[str, set[str]]:
    c = sqlite3.connect(db_path)
    rows = c.execute(
        "SELECT type, payload_json FROM event WHERE discussion_id LIKE ?",
        (f"{did_prefix}%",),
    ).fetchall()
    c.close()
    out: dict[str, set[str]] = {}
    for etype, payload in rows:
        out.setdefault(etype, set()).update(json.loads(payload).keys())
    return out


def test_projected_and_runtime_events_share_same_fields(seeded_db, tmp_path) -> None:
    projected = _keys(
        str(seeded_db.execute("PRAGMA database_list").fetchone()[2])
    )

    # 运行时跑一场，落到另一个临时库
    runtime_path = tmp_path / "runtime.db"
    s = Store(str(runtime_path))
    s.init_schema()
    panel = build_from_pool("要不要引入代码评审制度", 3)
    validate(panel, 3)
    s.create_discussion(
        did="d_rt",
        topic="要不要引入代码评审制度",
        expert_count=3,
        max_turns=12,
        panel=panel,
    )
    Engine(s, FakeLLM(), EventBus(), config=EngineConfig(max_turns=12)).run("d_rt")
    s.close()

    runtime = _keys(str(runtime_path), "d_rt")

    shared = set(projected) & set(runtime)
    assert shared >= {"phase", "panelist_state", "utterance", "ended"}, (
        f"两侧共有的类型太少，无法比对：{sorted(shared)}"
    )

    for etype in sorted(shared):
        # 投影侧可能没有 error 类事件，但凡是两边都有的类型，字段集必须完全一致
        assert projected[etype] == runtime[etype], (
            f"{etype} 事件字段不一致：\n"
            f"  投影侧(init_db): {sorted(projected[etype])}\n"
            f"  运行时(engine):  {sorted(runtime[etype])}"
        )


def test_projected_event_seq_starts_at_one(seeded_db) -> None:
    """两侧的序号起点必须一致，否则 SSE 补发会吃掉第一帧。

    真实踩到的：投影用 `enumerate(events)` 从 0 起，运行时引擎从 1 起，
    而 `/stream` 的查询是 `WHERE seq > from_seq`。于是 from_seq=0 时
    投影数据的第一帧（开场 phase）被静默过滤 —— 回看时界面直接少一个阶段。
    """
    path = str(seeded_db.execute("PRAGMA database_list").fetchone()[2])
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT discussion_id, MIN(seq), MAX(seq), COUNT(*) FROM event GROUP BY discussion_id"
    ).fetchall()
    conn.close()

    assert rows, "夹具库里没有任何事件，测试前提不成立"
    for did, mn, mx, cnt in rows:
        assert (mn, mx, cnt) == (1, cnt, cnt), (
            f"{did}: seq 应为 1..{cnt}，实际 min={mn} max={mx} 共 {cnt} 条"
        )


def test_projected_event_seq_starts_at_one(seeded_db) -> None:
    """两侧的序号起点必须一致，否则 SSE 补发会吃掉第一帧。

    真实踩到的：投影用 `enumerate(events)` 从 0 起，运行时引擎从 1 起，
    而 `/stream` 的查询是 `WHERE seq > from_seq`。于是 from_seq=0 时
    投影数据的第一帧（开场 phase）被静默过滤 —— 回看时界面直接少一个阶段。
    """
    path = str(seeded_db.execute("PRAGMA database_list").fetchone()[2])
    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT discussion_id, MIN(seq), MAX(seq), COUNT(*) FROM event GROUP BY discussion_id"
    ).fetchall()
    conn.close()

    assert rows, "夹具库里没有任何事件，测试前提不成立"
    for did, mn, mx, cnt in rows:
        assert (mn, mx, cnt) == (1, cnt, cnt), (
            f"{did}: seq 应为 1..{cnt}，实际 min={mn} max={mx} 共 {cnt} 条"
        )
