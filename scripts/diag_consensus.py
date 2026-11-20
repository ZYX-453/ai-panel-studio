"""一次性诊断：用真实场次复现一次共识抽取，打印模型原始输出。

目的：查清为什么某场的 points 恒为空。不写库、不发事件。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.llm import LLMClient  # noqa: E402
from app.models import Consensus, Seat, Turn  # noqa: E402
from app.personas import build_consensus_prompt  # noqa: E402

DID = sys.argv[1] if len(sys.argv) > 1 else "d_30aa5772"
DB = Path(__file__).resolve().parents[1] / "data" / "panel.db"


def main() -> int:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    topic = conn.execute("SELECT topic FROM discussion WHERE id=?", (DID,)).fetchone()["topic"]
    seats = tuple(
        Seat(id=r["id"], role=r["role"], seat=r["seat"], name=r["name"], stance=r["stance"])
        for r in conn.execute("SELECT * FROM panelist WHERE discussion_id=? ORDER BY seat", (DID,))
    )
    upto = int(sys.argv[2]) if len(sys.argv) > 2 else 999
    turns = tuple(
        Turn(seq=r["seq"], speaker_id=r["panelist_id"], text=r["content"])
        for r in conn.execute(
            "SELECT seq, panelist_id, content FROM utterance WHERE discussion_id=? AND seq<=? ORDER BY seq",
            (DID, upto),
        )
    )
    print(f"transcript 用到第 {turns[-1].seq} 句，共 {len(turns)} 句")
    row = conn.execute(
        "SELECT points_json, disputes_json FROM consensus_state WHERE discussion_id=?", (DID,)
    ).fetchone()
    prev = Consensus(
        version=0,
        points=tuple(json.loads(row["points_json"])),
        disputes=tuple(json.loads(row["disputes_json"])),
        based_on_seq=0,
    )

    prev_points = "\n".join(f"- {p}" for p in prev.points) or "（无）"
    prev_disputes = "\n".join(f"- {d}" for d in prev.disputes) or "（无）"
    prompt = build_consensus_prompt(
        topic=topic,
        transcript=turns,
        seats=seats,
        previous=f"【已达成共识】\n{prev_points}\n\n【仍未解决的分歧】\n{prev_disputes}",
    )

    client = LLMClient()
    for i in range(3):
        res = client.chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "现在抽取共识与分歧。"},
            ],
            temperature=0.2,
            max_tokens=900,
            json_mode=True,
        )
        print(f"===== 第 {i + 1} 次 =====")
        print(res.content)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
