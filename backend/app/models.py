"""核心数据契约。

全部 frozen：这些对象会被多个协程/线程同时读，
一旦可变就会出现"回放时和别人看到的不是同一份"的问题。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Seat:
    """一位台上的人。主持人也是 Seat，只是 role 不同。"""

    id: str
    role: str  # 'host' | 'expert'
    seat: int
    name: str
    stance: str = ""
    catchphrase: str = ""
    focus: tuple[str, ...] = ()
    attack: str = ""
    #: 抢话倾向 0~1。同样是"性格"的一部分：有人一有缝就插话，有人得被问才开口。
    #: 由人设生成阶段提供；留 None 时调度器按 id 派生一个稳定值（不是随机）。
    eagerness: float | None = None


@dataclass(frozen=True, slots=True)
class Turn:
    """一句已经发生的发言。

    mentions 是从这句话里抽出的"被点名的人"，调度器靠它决定下一个谁说话。
    intent 由模型给，scheduler 只给建议值 —— 决定权在代码，措辞权在模型。
    """

    seq: int
    speaker_id: str
    text: str = ""
    intent: str = ""
    mentions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Consensus:
    """共识与分歧的唯一真相源。

    页面上的「实时共识/分歧」和最终的主持人总结都从这里取，
    不允许各自再读一遍 transcript 生成 —— 否则同屏会出现两套说法。
    """

    version: int
    points: tuple[str, ...] = ()
    disputes: tuple[str, ...] = ()
    based_on_seq: int = 0
