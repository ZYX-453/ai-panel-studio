"""共识 / 分歧合并。

这一层存在的理由只有一个：**旧分歧不许被悄悄抹掉**。
每 K 句做一次抽取，模型只看到最近几句；如果直接用抽取结果覆盖当前状态，
之前那些没人再提、但也从没被推翻的分歧就会消失，
屏幕上于是出现"已全部达成共识"这种明显不成立的结论。

所以规则是「累加 + 显式解决」：
    新共识加入 → 与某条旧分歧实质相同，才算该分歧被解决 → 从分歧里移除。
没被提到，就原样留着。
"""

from __future__ import annotations

from .models import Consensus

#: 判定「这条分歧已经被解决了」的相似度门槛。
#: 这是一个启发式阈值，不是定理：中文短句用二元组 Jaccard 判断，
#: 0.5 能覆盖"产线能不能四天"→"产线不能四天"这类改写，
#: 又不会把无关的两条混为一谈。改这个值必须同步看 test_consensus。
RESOLVE_THRESHOLD = 0.5

#: 判重门槛：几乎完全相同才算重复
DEDUPE_THRESHOLD = 0.95


def _norm(text: str) -> str:
    return " ".join(str(text).split()).strip()


def _bigrams(text: str) -> set[str]:
    compact = _norm(text).replace(" ", "")
    if len(compact) < 2:
        return set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def _similar(a: str, b: str) -> float:
    if _norm(a) == _norm(b):
        return 1.0
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def merge(
    prev: Consensus,
    points: list[str] | tuple[str, ...],
    disputes: list[str] | tuple[str, ...],
    based_on_seq: int,
    max_points: int = 6,
) -> Consensus:
    """把一次抽取结果并进当前状态，返回新的共识对象（不改动入参）。"""
    merged_points = list(prev.points)
    merged_disputes = list(prev.disputes)

    # 水位线只进不退：乱序到达的旧结果不能把"基于前 N 句"往回拨
    seq = max(prev.based_on_seq, int(based_on_seq))

    # 1. 并入新共识（去重）
    for p in points:
        np_ = _norm(p)
        if not np_:
            continue
        if any(_similar(np_, existing) >= DEDUPE_THRESHOLD for existing in merged_points):
            continue
        merged_points.append(np_)

    # 2. 显式解决：与某条共识实质相同的旧分歧，才允许移出
    surviving = [
        d
        for d in merged_disputes
        if not any(_similar(_norm(d), p) >= RESOLVE_THRESHOLD for p in merged_points)
    ]
    merged_disputes = surviving

    # 3. 并入新分歧（与共识或已有分歧重复的不要）
    for d in disputes:
        nd = _norm(d)
        if not nd:
            continue
        if any(_similar(nd, p) >= RESOLVE_THRESHOLD for p in merged_points):
            continue
        if any(_similar(nd, existing) >= DEDUPE_THRESHOLD for existing in merged_disputes):
            continue
        merged_disputes.append(nd)

    # 4. 面板是给人看的，堆太多就没人读了
    merged_points = merged_points[:max_points]

    changed = (
        tuple(merged_points) != prev.points
        or tuple(merged_disputes) != prev.disputes
        or seq != prev.based_on_seq
    )
    if not changed:
        # 内容没变就不刷版本号，否则界面上的 v3→v4→v5 全是噪声
        return prev

    return Consensus(
        version=prev.version + 1,
        points=tuple(merged_points),
        disputes=tuple(merged_disputes),
        based_on_seq=seq,
    )
