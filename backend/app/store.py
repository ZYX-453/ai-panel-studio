"""SQLite 访问层。

只在最外层做一件事：把"业务上的一次原子动作"包成单个事务。
上层（engine / api）不应该自己拼 BEGIN —— 一旦两处各自控制事务，
就会出现"事件写了、发言没写"这种只在回放时才暴露的缝。

seq 由这里统一分配，保证单调且不跳号。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

#: store.py 位于 backend/app/，项目根要往上三层
#: （与 scripts/init_db.py 的 ROOT 口径保持一致，否则后端和初始化脚本会读写两个不同的库）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(ROOT, "data", "panel.db")
SCHEMA_PATH = os.path.join(ROOT, "db", "schema.sql")


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class PanelistRow:
    id: str
    role: str
    seat: int
    name: str
    title: str
    stance: str
    color: str
    persona: dict[str, Any]


class Store:
    def __init__(self, path: str = DB_PATH) -> None:
        self.path = path
        self._local = threading.local()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    # ---------------------------------------------------------------- 连接

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=15.0)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys = ON")
            # WAL：一边写事件一边读快照，读不会被写卡住
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA synchronous = NORMAL")
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None

    def init_schema(self) -> None:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.conn.commit()

    # ---------------------------------------------------------------- 读

    def get_discussion(self, did: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM discussion WHERE id = ?", (did,)).fetchone()
        return dict(row) if row else None

    def list_discussions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM v_discussion_list
               ORDER BY CASE status WHEN 'LIVE' THEN 0 WHEN 'AWAITING_CONFIRM' THEN 1
                                    WHEN 'FINISHED' THEN 2 ELSE 3 END,
                        created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_panel(self, did: str) -> list[PanelistRow]:
        rows = self.conn.execute(
            "SELECT * FROM panelist WHERE discussion_id = ? ORDER BY seat", (did,)
        ).fetchall()
        out: list[PanelistRow] = []
        for r in rows:
            try:
                persona = json.loads(r["persona_json"] or "{}")
            except json.JSONDecodeError:
                persona = {}
            out.append(
                PanelistRow(
                    id=r["id"],
                    role=r["role"],
                    seat=r["seat"],
                    name=r["name"],
                    title=r["title"],
                    stance=r["stance"],
                    color=r["color_token"],
                    persona=persona,
                )
            )
        return out

    def get_utterances(self, did: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM utterance WHERE discussion_id = ? ORDER BY seq", (did,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_consensus(self, did: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT * FROM consensus_state WHERE discussion_id = ?", (did,)
        ).fetchone()
        if not row:
            return {"version": 0, "points": [], "disputes": [], "based_on_seq": 0}
        return {
            "version": row["version"],
            "points": json.loads(row["points_json"] or "[]"),
            "disputes": json.loads(row["disputes_json"] or "[]"),
            "based_on_seq": row["based_on_seq"],
        }

    def get_events(self, did: str, from_seq: int = 0) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT seq, type, payload_json, created_at FROM event
               WHERE discussion_id = ? AND seq > ? ORDER BY seq""",
            (did, from_seq),
        ).fetchall()
        return [
            {
                "seq": r["seq"],
                "type": r["type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def max_seq(self, did: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM event WHERE discussion_id = ?", (did,)
        ).fetchone()
        return int(row["m"])

    def snapshot(self, did: str) -> dict[str, Any] | None:
        d = self.get_discussion(did)
        if not d:
            return None
        return {
            "id": d["id"],
            "topic": d["topic"],
            "status": d["status"],
            "ended_reason": d["ended_reason"],
            "turn_count": d["turn_count"],
            "max_turns": d["max_turns"],
            "created_at": d["created_at"],
            "started_at": d["started_at"],
            "ended_at": d["ended_at"],
            "panel": [
                {
                    "id": p.id,
                    "role": p.role,
                    "seat": p.seat,
                    "name": p.name,
                    "title": p.title,
                    "stance": p.stance,
                    "color": p.color,
                    **p.persona,
                }
                for p in self.get_panel(did)
            ],
            "transcript": [
                {
                    "seq": u["seq"],
                    "panelist_id": u["panelist_id"],
                    "content": u["content"],
                    "phase": u["phase"],
                    "intent": u["intent"],
                    "reply_to_seq": self._reply_seq(u),
                }
                for u in self.get_utterances(did)
            ],
            "consensus": self.get_consensus(did),
            "final_summary": d["final_summary"],
        }

    def _reply_seq(self, u: dict[str, Any]) -> int | None:
        rid = u.get("reply_to_id")
        if not rid:
            return None
        row = self.conn.execute("SELECT seq FROM utterance WHERE id = ?", (rid,)).fetchone()
        return int(row["seq"]) if row else None

    # ---------------------------------------------------------------- 写

    def create_discussion(
        self,
        *,
        did: str,
        topic: str,
        expert_count: int,
        max_turns: int,
        panel: Iterable[dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> None:
        c = self.conn
        ts = now_iso()
        with c:  # 一个事务：讨论、阵容、共识初值必须同时成立
            c.execute(
                """INSERT INTO discussion
                   (id, topic, expert_count, status, turn_count, max_turns,
                    config_json, created_at)
                   VALUES (?, ?, ?, 'AWAITING_CONFIRM', 0, ?, ?, ?)""",
                (did, topic, expert_count, max_turns, json.dumps(config or {}, ensure_ascii=False), ts),
            )
            c.executemany(
                """INSERT INTO panelist
                   (id, discussion_id, role, seat, name, title, stance, color_token, persona_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        p["id"], did, p["role"], p["seat"], p["name"], p["title"],
                        p["stance"], p["color"],
                        json.dumps(p.get("persona", {}), ensure_ascii=False),
                    )
                    for p in panel
                ],
            )
            c.execute(
                """INSERT INTO consensus_state
                   (discussion_id, version, points_json, disputes_json, based_on_seq, updated_at)
                   VALUES (?, 0, '[]', '[]', 0, ?)""",
                (did, ts),
            )

    def replace_panel(self, did: str, panel: Iterable[dict[str, Any]]) -> None:
        c = self.conn
        with c:
            c.execute("DELETE FROM panelist WHERE discussion_id = ?", (did,))
            c.executemany(
                """INSERT INTO panelist
                   (id, discussion_id, role, seat, name, title, stance, color_token, persona_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        p["id"], did, p["role"], p["seat"], p["name"], p["title"],
                        p["stance"], p["color"],
                        json.dumps(p.get("persona", {}), ensure_ascii=False),
                    )
                    for p in panel
                ],
            )

    def set_status(self, did: str, status: str, **fields: Any) -> None:
        allowed = {"ended_reason", "started_at", "ended_at", "final_summary", "turn_count"}
        sets = ["status = ?"]
        vals: list[Any] = [status]
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(f"不允许更新字段: {k}")
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(did)
        with self.conn as c:
            c.execute(f"UPDATE discussion SET {', '.join(sets)} WHERE id = ?", vals)

    def next_seq(self, did: str) -> int:
        return self.max_seq(did) + 1

    def append_event(self, did: str, seq: int, etype: str, payload: dict[str, Any]) -> None:
        """写一条事件。seq 由调用方在同一事务里通过 next_seq 取得。"""
        with self.conn as c:
            c.execute(
                """INSERT INTO event (discussion_id, seq, type, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (did, seq, etype, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    def append_utterance(
        self,
        *,
        did: str,
        uid: str,
        panelist_id: str,
        seq: int,
        phase: str,
        intent: str,
        content: str,
        reply_to_id: str | None,
    ) -> None:
        """发言与它的事件必须一起落地，否则回放会出现"事件有、发言没有"。"""
        c = self.conn
        with c:
            c.execute(
                """INSERT INTO utterance
                   (id, discussion_id, panelist_id, seq, phase, intent, content,
                    reply_to_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, did, panelist_id, seq, phase, intent, content, reply_to_id, now_iso()),
            )
            c.execute(
                "UPDATE discussion SET turn_count = turn_count + 1 WHERE id = ?", (did,)
            )

    def save_consensus(
        self,
        *,
        did: str,
        version: int,
        points: list[str],
        disputes: list[str],
        based_on_seq: int,
    ) -> None:
        c = self.conn
        ts = now_iso()
        with c:
            c.execute(
                """INSERT INTO consensus_state
                   (discussion_id, version, points_json, disputes_json, based_on_seq, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(discussion_id) DO UPDATE SET
                     version = excluded.version,
                     points_json = excluded.points_json,
                     disputes_json = excluded.disputes_json,
                     based_on_seq = excluded.based_on_seq,
                     updated_at = excluded.updated_at""",
                (
                    did, version,
                    json.dumps(points, ensure_ascii=False),
                    json.dumps(disputes, ensure_ascii=False),
                    based_on_seq, ts,
                ),
            )
            c.execute(
                """INSERT OR REPLACE INTO consensus_snapshot
                   (id, discussion_id, version, points_json, disputes_json,
                    based_on_seq, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"{did}_v{version}", did, version,
                    json.dumps(points, ensure_ascii=False),
                    json.dumps(disputes, ensure_ascii=False),
                    based_on_seq, ts,
                ),
            )

    def utterance_id_by_seq(self, did: str, seq: int) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM utterance WHERE discussion_id = ? AND seq = ?", (did, seq)
        ).fetchone()
        return row["id"] if row else None
