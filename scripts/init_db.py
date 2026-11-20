#!/usr/bin/env python3
"""初始化 AI Panel Studio 的 SQLite 数据库。

用法：
    python scripts/init_db.py            # 首次初始化，库已存在则拒绝执行
    python scripts/init_db.py --force    # 删库重建
    python scripts/init_db.py --db path/to/x.db

做三件事：
    1. 执行 db/schema.sql 建表、建索引、建触发器
    2. 执行 db/seed.sql 灌入 5 场样例讨论
    3. 为 FINISHED 场次补齐 event 事件流（重放需要）

第 3 步是关键：schema 里 event 表是"唯一重放源"，但手写 24 条事件容易和
utterance 对不上。所以 seed 只写 utterance，事件流由本脚本**按固定规则投影**出来——
一处维护，不会出现"transcript 有第 7 句、事件流里没有"这种缝。

脚本最后会跑一遍自检，任何一条不通过就非零退出，避免"建好了但其实坏了"。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = ROOT / "db" / "schema.sql"
SEED_SQL = ROOT / "db" / "seed.sql"
DEFAULT_DB = ROOT / "data" / "panel.db"


# ---------------------------------------------------------------------------
# 事件流投影
# ---------------------------------------------------------------------------

def project_events(conn: sqlite3.Connection) -> int:
    """为已完成场次生成事件流，返回写入的事件条数。

    投影规则（与 docs/02-SCHEMA.md 的时序图一致）：
        阶段变化 → event: phase
        每条发言 → panelist_state(准备发言) → utterance → panelist_state(待机)
        该条发言正好触发共识抽取 → event: consensus
        收尾 → event: ended

    panelist_state 先于 utterance 写入，是为了让侧边小窗的状态变化
    有真实的时间依据，而不是前端补动画。
    """
    finished = conn.execute(
        "SELECT id, ended_reason, turn_count, ended_at FROM discussion "
        "WHERE status = 'FINISHED' ORDER BY created_at"
    ).fetchall()

    written = 0
    for did, ended_reason, turn_count, ended_at in finished:
        rows = conn.execute(
            "SELECT u.id, u.seq, u.phase, u.intent, u.content, u.reply_to_id, "
            "       u.created_at, p.id, p.name, p.title "
            "FROM utterance u JOIN panelist p ON p.id = u.panelist_id "
            "WHERE u.discussion_id = ? ORDER BY u.seq",
            (did,),
        ).fetchall()
        if not rows:
            continue

        id_to_seq = {r[0]: r[1] for r in rows}
        snaps = {
            r[0]: r
            for r in conn.execute(
                "SELECT based_on_seq, version, points_json, disputes_json, created_at "
                "FROM consensus_snapshot WHERE discussion_id = ? ORDER BY version",
                (did,),
            ).fetchall()
        }

        events: list[tuple[str, dict, str]] = []
        current_phase = None

        for uid, useq, phase, intent, content, reply_id, ts, pid, pname, ptitle in rows:
            if phase != current_phase:
                events.append(("phase", {"phase": phase}, ts))
                current_phase = phase

            events.append(
                ("panelist_state", {"panelist_id": pid, "name": pname, "state": "准备发言"}, ts)
            )
            events.append(
                (
                    "utterance",
                    {
                        "utterance_seq": useq,
                        "panelist_id": pid,
                        "name": pname,
                        "title": ptitle,
                        "content": content,
                        "intent": intent,
                        "reply_to_utterance_seq": id_to_seq.get(reply_id),
                        "phase": phase,
                    },
                    ts,
                )
            )
            events.append(
                ("panelist_state", {"panelist_id": pid, "name": pname, "state": "待机"}, ts)
            )

            if useq in snaps:
                bseq, ver, pts, dis, sts = snaps[useq]
                events.append(
                    (
                        "consensus",
                        {
                            "version": ver,
                            "points": json.loads(pts),
                            "disputes": json.loads(dis),
                            "based_on_seq": bseq,
                        },
                        sts,
                    )
                )

        events.append(
            ("ended", {"reason": ended_reason, "turns": turn_count}, ended_at or rows[-1][6])
        )

        conn.executemany(
            "INSERT INTO event (discussion_id, seq, type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (did, i, etype, json.dumps(payload, ensure_ascii=False), ts)
                for i, (etype, payload, ts) in enumerate(events)
            ],
        )
        written += len(events)

    return written


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_check(conn: sqlite3.Connection) -> list[str]:
    """返回问题列表，空表示全部通过。"""
    problems: list[str] = []

    # 1. FINISHED 必须有结束原因（触发器之外的二次确认）
    for did, reason in conn.execute(
        "SELECT id, ended_reason FROM discussion WHERE status = 'FINISHED'"
    ):
        if not reason:
            problems.append(f"{did}: FINISHED 但没有 ended_reason")

    # 2. 每场恰有一条 consensus_state
    for (did,) in conn.execute("SELECT id FROM discussion"):
        n = conn.execute(
            "SELECT COUNT(*) FROM consensus_state WHERE discussion_id = ?", (did,)
        ).fetchone()[0]
        if n != 1:
            problems.append(f"{did}: consensus_state 有 {n} 条，应为 1")

    # 3. 阵容人数与 expert_count 一致，且有且仅有一位主持
    for did, declared in conn.execute("SELECT id, expert_count FROM discussion"):
        experts = conn.execute(
            "SELECT COUNT(*) FROM panelist WHERE discussion_id = ? AND role = 'expert'", (did,)
        ).fetchone()[0]
        hosts = conn.execute(
            "SELECT COUNT(*) FROM panelist WHERE discussion_id = ? AND role = 'host'", (did,)
        ).fetchone()[0]
        if experts != declared:
            problems.append(f"{did}: 声明 {declared} 位专家，实际 {experts} 位")
        if hosts != 1:
            problems.append(f"{did}: 主持人有 {hosts} 位，应为 1")

    # 4. 同场内座位、颜色、立场三者都必须唯一
    for did in [r[0] for r in conn.execute("SELECT id FROM discussion")]:
        for col, label in (("seat", "座位"), ("color_token", "颜色"), ("stance", "立场")):
            rows = conn.execute(
                f"SELECT {col}, COUNT(*) c FROM panelist WHERE discussion_id = ? "
                f"GROUP BY {col} HAVING c > 1",
                (did,),
            ).fetchall()
            for val, c in rows:
                problems.append(f"{did}: {label} 重复 {c} 次 -> {val}")

    # 5. 发言序号必须从 1 连续
    for did, in conn.execute("SELECT id FROM discussion"):
        seqs = [r[0] for r in conn.execute(
            "SELECT seq FROM utterance WHERE discussion_id = ? ORDER BY seq", (did,)
        )]
        if seqs and seqs != list(range(1, len(seqs) + 1)):
            problems.append(f"{did}: 发言序号不连续 {seqs[:12]}")

    # 6. FINISHED 场次事件流 seq 必须从 0 连续，且末条为 ended
    for did, in conn.execute("SELECT id FROM discussion WHERE status = 'FINISHED'"):
        evs = conn.execute(
            "SELECT seq, type FROM event WHERE discussion_id = ? ORDER BY seq", (did,)
        ).fetchall()
        if not evs:
            problems.append(f"{did}: FINISHED 但没有事件流")
            continue
        if [e[0] for e in evs] != list(range(len(evs))):
            problems.append(f"{did}: 事件 seq 不连续（共 {len(evs)} 条）")
        if evs[-1][1] != "ended":
            problems.append(f"{did}: 事件流末条是 {evs[-1][1]}，应为 ended")

    # 7. 发言数必须等于 utterance 事件数（投影没漏）
    for did, in conn.execute("SELECT id FROM discussion WHERE status = 'FINISHED'"):
        n_utt = conn.execute(
            "SELECT COUNT(*) FROM utterance WHERE discussion_id = ?", (did,)
        ).fetchone()[0]
        n_evt = conn.execute(
            "SELECT COUNT(*) FROM event WHERE discussion_id = ? AND type = 'utterance'", (did,)
        ).fetchone()[0]
        if n_utt != n_evt:
            problems.append(f"{did}: 发言 {n_utt} 条，utterance 事件 {n_evt} 条")

    # 8. 内部事件不得污染 transcript：utterance 的 intent 不能是举手类词
    for (did, intent) in conn.execute("SELECT discussion_id, intent FROM utterance"):
        if intent in ("举手", "让位", "准备发言"):
            problems.append(f"{did}: utterance 里出现内部事件 {intent}")

    return problems


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="初始化 AI Panel Studio 数据库")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="数据库文件路径")
    ap.add_argument("--force", action="store_true", help="已存在则删除重建")
    args = ap.parse_args()

    db_path = Path(args.db)
    if db_path.exists():
        if not args.force:
            print(f"[拒绝] {db_path} 已存在。确认要重建请加 --force")
            return 2
        db_path.unlink()
        print(f"[清空] 已删除旧库 {db_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        print(f"[建表] {SCHEMA_SQL.name}")

        conn.executescript(SEED_SQL.read_text(encoding="utf-8"))
        print(f"[灌数据] {SEED_SQL.name}")

        n_events = project_events(conn)
        print(f"[投影事件] 写入 {n_events} 条")

        problems = self_check(conn)
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        print(f"[失败] {type(exc).__name__}: {exc}")
        return 1
    finally:
        conn.close()

    # 汇总
    conn = sqlite3.connect(db_path)
    try:
        print("\n--- 数据汇总 ---")
        for table in ("discussion", "panelist", "utterance", "consensus_state",
                      "consensus_snapshot", "event"):
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<20} {n:>4} 行")
        print("\n--- 场次一览 ---")
        for row in conn.execute(
            "SELECT id, status, ended_reason, turn_count, topic FROM discussion ORDER BY id"
        ):
            did, status, reason, turns, topic = row
            tag = f"{status}" + (f"({reason})" if reason else "")
            print(f"  {did}  {tag:<26} {turns:>2} 轮  {topic}")
    finally:
        conn.close()

    if problems:
        print("\n--- 自检未通过 ---")
        for p in problems:
            print(f"  ★ {p}")
        return 1

    print("\n--- 自检通过（8 项）---")
    print(f"数据库: {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
