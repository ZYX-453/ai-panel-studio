"""发言调度：决定「下一个谁说话」。

这是整个项目最核心的一处设计（PRD 决策 D2）。

需求同时要求「专家自主决定发言顺序，不允许机械式轮流」和「工程可验证」。
这两件事天然冲突：让模型自由决定顺序，就没法测；写死轮转，就不是讨论。

解法是把"自主"拆成两层：
    模型负责「说什么」（意图 + 那 1-2 句），
    代码负责「谁来说」（本模块）。
于是涌现感来自提示词，可测性来自纯函数，两边都不牺牲。

本模块不读取任何外部状态、不发网络请求、不看时钟 —— 同样的输入永远给同样的输出。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

from .models import Seat, Turn

#: 只有这两个阶段需要抢发言权
DEBATE_PHASES = frozenset({"EXPLORE", "CLASH"})

#: 同一个人最多连说几句。设为 2 是刻意的：
#: 允许"我说完你反驳、我再回一句"，但不允许一个人独白三段。
MAX_CONSECUTIVE = 2


@dataclass(frozen=True, slots=True)
class Decision:
    seat_id: str
    intent_hint: str
    respond_to_seq: int | None
    reason: str


def _jitter(seed: int, seat_id: str, n: int) -> float:
    """确定性抖动。

    不能用 random：那会让测试变成偶发失败，回放也不可复现。
    用 seed + 座位 + 轮数做哈希，看起来随机，实际上完全可重放。
    """
    digest = sha256(f"{seed}|{seat_id}|{n}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def _trailing_run(turns: tuple[Turn, ...], speaker_id: str) -> int:
    """某人末尾连说了几句。"""
    n = 0
    for t in reversed(turns):
        if t.speaker_id != speaker_id:
            break
        n += 1
    return n


def pick_next(
    seats: tuple[Seat, ...] | list[Seat],
    turns: tuple[Turn, ...] | list[Turn],
    phase: str,
    seed: int = 0,
) -> Decision:
    seats = tuple(seats)
    turns = tuple(turns)

    hosts = [s for s in seats if s.role == "host"]
    experts = [s for s in seats if s.role == "expert"]

    # 开场和收尾由主持人控制，不参与抢话
    if phase not in DEBATE_PHASES and hosts:
        host = hosts[0]
        label = "开场" if phase == "OPENING" else "收尾"
        return Decision(
            seat_id=host.id,
            intent_hint=label,
            respond_to_seq=None,
            reason=f"{phase} 阶段由主持人控场",
        )

    if not experts:
        raise ValueError("阵容里没有任何专家，无法调度")

    if not turns:
        first = min(experts, key=lambda s: s.seat)
        return Decision(
            seat_id=first.id,
            intent_hint="抛论点",
            respond_to_seq=None,
            reason="尚无发言，按座位序由第一位专家开场陈述",
        )

    last = turns[-1]
    counts = Counter(t.speaker_id for t in turns)
    candidates = list(experts)

    # 规则 1：还有人没开过口，就先让没开口的人上。
    # 这一条保证"四位专家都会说话"，否则模型很容易捧着一两个人聊完。
    silent = [s for s in candidates if counts[s.id] == 0]
    if silent:
        candidates = silent

    # 规则 2：不许连说超过 MAX_CONSECUTIVE 句
    if _trailing_run(turns, last.speaker_id) >= MAX_CONSECUTIVE:
        narrowed = [s for s in candidates if s.id != last.speaker_id]
        if narrowed:
            candidates = narrowed

    # 规则 3：被点名的人优先。
    # 这是"有来有回"的机械保证 —— 不需要模型记得自己被问了。
    mentioned = [s for s in candidates if s.id in last.mentions]
    forced = bool(mentioned)
    if mentioned:
        candidates = mentioned

    def score(seat: Seat) -> float:
        value = 0.0
        if seat.id in last.mentions:
            value += 6.0
        if any(seat.id in t.mentions for t in turns[-2:]):
            value += 2.0
        c = counts[seat.id]
        value += 4.0 if c == 0 else (1.5 if c == 1 else 0.0)
        last_idx = max(
            (i for i, t in enumerate(turns) if t.speaker_id == seat.id), default=-1
        )
        gap = len(turns) - last_idx
        value += min(gap, 6) * 0.5
        value += _jitter(seed, seat.id, len(turns)) * 0.8
        return value

    # 并列时座位靠前的先说话，保证结果与输入顺序无关
    best = max(candidates, key=lambda s: (score(s), -s.seat))

    if forced:
        intent = "追问"
        respond_to = last.seq
        reason = f"上一条发言点名了{best.name}，优先回应"
    elif best.id == last.speaker_id:
        intent = "反驳"
        respond_to = last.seq
        reason = f"{best.name}继续追问刚才的话题"
    else:
        intent = "补充"
        respond_to = None
        reason = f"{best.name}已等待 {len(turns) - (counts[best.id] or 0)} 轮，且尚未开口" if counts[best.id] == 0 else f"轮到{best.name}接话"

    return Decision(
        seat_id=best.id,
        intent_hint=intent,
        respond_to_seq=respond_to,
        reason=reason,
    )
