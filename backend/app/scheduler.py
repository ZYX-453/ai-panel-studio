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


def _eagerness(seat: Seat) -> float:
    """抢话倾向。

    有人一有缝就插话，有人得被点名才开口 —— 这是性格，不是随机。
    人设生成阶段应当显式给出；缺省时按 id 派生一个稳定值，
    保证同一个阵容永远得到同一种"性格分布"，回放才一致。
    """
    if seat.eagerness is not None:
        return max(0.0, min(1.0, seat.eagerness))
    digest = sha256(seat.id.encode()).digest()
    return 0.35 + int.from_bytes(digest[:2], "big") / 0xFFFF * 0.6


def _affinity(seat: Seat, text: str) -> float:
    """这段话有没有说到他关心的点上。

    这是让顺序"由内容驱动"的那一项：光靠冷却和随机，
    讨论会退化成排班表；只有内容相关度能让某个人真的插进来。
    用子串匹配而不是向量检索，是刻意的：可解释、可断言、零依赖。
    """
    if not seat.focus or not text:
        return 0.0
    hits = sum(1 for f in seat.focus if f and f in text)
    if hits == 0:
        return 0.0
    # 命中一个就已经足够抢话；再多的边际收益很小
    return 4.0 + min(hits - 1, 1) * 1.0


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
        # 被点名是最强信号：有人直接问你，你就得接
        if seat.id in last.mentions:
            value += 6.0
        if any(seat.id in t.mentions for t in turns[-2:]):
            value += 2.0
        # 话题踩到他的关注点是第二强信号
        value += _affinity(seat, last.text)
        # 还没开过口的人优先级最高
        c = counts[seat.id]
        value += 4.0 if c == 0 else (1.5 if c == 1 else 0.0)
        # 冷却：久没说的人略微占优。
        # 权重刻意压得很低（0.25/轮）—— 权重大了就会精确地变成
        # 「谁最久没说话谁先说」，也就是机械轮转
        last_idx = max(
            (i for i, t in enumerate(turns) if t.speaker_id == seat.id), default=-1
        )
        gap = len(turns) - last_idx
        value += min(gap, 6) * 0.25
        # 性格：有人天生爱抢话
        value += _eagerness(seat) * 2.0
        value += _jitter(seed, seat.id, len(turns)) * 0.8
        return value

    # 并列时座位靠前的先说话，保证结果与输入顺序无关
    best = max(candidates, key=lambda s: (score(s), -s.seat))

    affinity_hit = _affinity(best, last.text) > 0

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
        if affinity_hit:
            reason = f"上一条说到{best.name}关注的方向，他先接话"
        elif counts[best.id] == 0:
            reason = f"{best.name}还没开过口，优先给他"
        else:
            reason = f"{best.name}轮到接话"

    return Decision(
        seat_id=best.id,
        intent_hint=intent,
        respond_to_seq=respond_to,
        reason=reason,
    )
