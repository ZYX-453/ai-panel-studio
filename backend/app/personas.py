"""系统提示词装配。

三类角色各有一套：主持人 / 专家 / 共识记录员。
它们的约束是**互相排斥**的 —— 专家要站定立场不许改口，
主持人恰恰要中立；如果三段 prompt 长得像，说明角色就没分开。

本模块只拼字符串，不调模型。这样"prompt 里到底写了什么"可以被直接断言，
串味、漏写限制这类问题在测试阶段就能拦住，而不是等看录屏才发现。
"""

from __future__ import annotations

from .models import Seat, Turn

#: 一句发言的字数上限。测试里断言 1-2 句，这里给一个具体数字兜底。
MAX_CHARS = 60

_RULES_COMMON = (
    "1. 只说 1-2 句，不超过 60 字。宁可少说，不要把话讲满。\n"
    "2. 直接输出你要说的那句话本身。不要输出 JSON、不要加引号、不要写「某某说：」。\n"
    "3. 不要提到自己在扮演角色，也不要提到 AI、模型、提示词。\n"
)


def _by_id(seats: tuple[Seat, ...] | list[Seat]) -> dict[str, Seat]:
    return {s.id: s for s in seats}


def _speaker_label(turn: Turn, index: dict[str, Seat], self_id: str) -> str:
    seat = index.get(turn.speaker_id)
    name = seat.name if seat else turn.speaker_id
    return f"{name}（你）" if turn.speaker_id == self_id else name


def _format_window(
    transcript: tuple[Turn, ...] | list[Turn],
    seats: tuple[Seat, ...] | list[Seat],
    window: int,
    self_id: str = "",
) -> str:
    index = _by_id(seats)
    recent = tuple(transcript)[-window:]
    if not recent:
        return "（还没有人发言）"
    return "\n".join(
        f"第{t.seq}句 {_speaker_label(t, index, self_id)}：{t.text}" for t in recent
    )


def _own_window(
    transcript: tuple[Turn, ...] | list[Turn],
    self_id: str,
    limit: int = 2,
) -> list[Turn]:
    """自己最近说过的几句。

    窗口里 6 句有 5 句是别人的，模型对自己的上一句反而没有记忆 ——
    实测出现过同一专家隔十句把同一句判断原样说第二遍。
    """
    mine = [t for t in transcript if t.speaker_id == self_id and t.text]
    return mine[-limit:]


def build_expert_prompt(
    seat: Seat,
    *,
    topic: str,
    transcript: tuple[Turn, ...] | list[Turn],
    seats: tuple[Seat, ...] | list[Seat],
    respond_to_seq: int | None = None,
    window: int = 6,
    avoid_repeat: bool = False,
    too_long: bool = False,
    push_back: bool = False,
) -> str:
    focus = "、".join(seat.focus) if seat.focus else "（未设定）"
    parts = [
        f"你是「{seat.name}」，正在参加一场圆桌讨论。",
        "",
        f"【你的立场】{seat.stance}",
        f"【你的关注点】{focus}",
        f"【你的说话习惯】你习惯这样开口：「{seat.catchphrase}」。保持这个语气，但不要每句都用同一句开场白。",
        f"【你容易犯的错】{seat.attack} —— 注意不要在这里翻车。",
        "",
        f"【讨论议题】{topic}",
        "",
        "【最近发言】",
        _format_window(transcript, seats, window, self_id=seat.id),
        "",
    ]

    if respond_to_seq is not None:
        index = _by_id(seats)
        target = next((t for t in transcript if t.seq == respond_to_seq), None)
        if target is not None:
            name = index.get(target.speaker_id).name if target.speaker_id in index else target.speaker_id
            parts += [
                f"【你要回应的对象】{name}（第 {respond_to_seq} 句）—— 直接针对那句话讲，不要绕开。",
                "",
            ]

    own = _own_window(transcript, seat.id)
    if own:
        parts += [
            "【你刚才说过的】这几句已经说过了，不要换个说法再讲一遍：",
            "\n".join(f"第{t.seq}句 你：{t.text}" for t in own),
            "",
        ]

    parts += [
        "【硬性规则】",
        _RULES_COMMON
        + "4. 不要复述或概括别人刚说过的话，直接给你的判断。\n"
        + "5. 不要因为别人反对就改口。你可以被说服，但要给出理由。\n"
        + "6. 不要重复你自己已经说过的判断，每一轮都要往前推进一个\n"
        + "   新的细节、具体场景或数字。\n"
        + "7. 不要借用别人刚用过的关键词和比喻，用你所在领域里的词说同一件事。\n"
        + "8. 不要每轮都走「先肯定对方、再转折」这条路，更不要开头就写「我认」。\n"
        + "   直接给判断，理由放在后面。",
    ]
    if avoid_repeat:
        parts += [
            "",
            "【重复预警】你上一句与场上已有发言几乎一样。换一个具体案例、数字或场景重说，"
            "换掉原来的措辞，不要只改开头两个字。",
        ]
    if too_long:
        # 实测：写了「不超过 60 字」，7 场 168 句里仍有 47 句越界，最长 82 字。
        # 模型不会数数，所以得用代码量、量到了再让它按这个提示重说。
        parts += [
            "",
            "【太长了】你上一版超过了 60 字上限。压到 55 字以内：只留最有力的那一句，"
            "砍掉铺垫、举例的细节和重复的解释。短一点比说全更重要。",
        ]
    if push_back:
        parts += [
            "",
            "【这一轮的要求】场上已经连着几句都在互相补充、没人反对了。"
            "你必须指出一个具体的反例或代价：按刚才那个做法，在什么情况下会失效、"
            "谁会因此受损，说一件具体的事。",
        ]
    return "\n".join(parts)


def build_host_prompt(
    seat: Seat,
    *,
    topic: str,
    transcript: tuple[Turn, ...] | list[Turn],
    phase: str,
    seats: tuple[Seat, ...] | list[Seat] | None = None,
    window: int = 8,
    avoid_repeat: bool = False,
) -> str:
    lineup = seats if seats is not None else (seat,)
    phase_text = {
        "OPENING": "开场",
        "EXPLORE": "各方陈述",
        "CLASH": "交锋",
        "CLOSING": "收尾",
    }.get(phase, phase)

    parts = [
        f"你是「{seat.name}」，本场圆桌的主持人。",
        "",
        f"【你的定位】{seat.stance}",
        f"【讨论议题】{topic}",
        f"【当前阶段】{phase_text}",
        "",
        "【你的职责】",
        "- 不预设立场，不替任何一方说话。",
        "- 追问、串联，点出谁在回避问题。",
        "- 允许尖锐，但不要人身攻击。",
        "",
        "【最近发言】",
        _format_window(transcript, lineup, window, self_id=seat.id),
        "",
    ]

    if phase == "CLOSING":
        parts += [
            "【本轮任务】请做收尾总结：说清哪些已经达成共识、哪些仍然悬着、下一步该做什么。",
            "不要引入新的论点，也不要假装全场已经统一。",
            "",
        ]

    parts += [
        "【硬性规则】",
        _RULES_COMMON
        + "4. 你总结的内容必须与给定的共识/分歧清单一致，不得新增判断。",
    ]
    # 主持人也会复读：实测收尾时把开场那句原样搬回来。这条对两个角色一视同仁
    if avoid_repeat:
        parts += [
            "",
            "【重复预警】你这句话与场上已有发言几乎一样。换一个具体案例、数字或场景重说，"
            "换掉原来的措辞，不要只改开头两个字。",
        ]
    return "\n".join(parts)


def build_consensus_prompt(
    *,
    topic: str,
    transcript: tuple[Turn, ...] | list[Turn],
    seats: tuple[Seat, ...] | list[Seat],
    previous: str = "",
    window: int = 10,
) -> str:
    """共识抽取。

    这里允许且要求输出 JSON —— 与专家 prompt 的"禁止 JSON"并不矛盾：
    专家的话要直接上屏，所以不能带结构；抽取结果是给程序用的，必须结构化。
    """
    recent = tuple(transcript)[-window:]
    last_seq = recent[-1].seq if recent else 0

    parts = [
        "你是这场圆桌讨论的记录员。唯一任务是把讨论整理成结构化的共识与分歧。",
        "",
        f"【讨论议题】{topic}",
        "",
        "【最近发言】",
        _format_window(transcript, seats, window),
        "",
        '【输出格式】严格输出一个 JSON 对象，不要输出任何其他文字：',
        '{"points": ["已达成共识的结论"], "disputes": ["仍存在的分歧"], '
        f'"based_on_seq": {last_seq}}}',
        "",
        "【规则】",
        "- points 只写多方已经明确认同的结论，不要把某一方的观点写进来充数。",
        "- disputes 写成「一个悬而未决的问题」，而不是「某人不同意」。",
        "- 之前已经存在、这次发言没有推翻的分歧，必须原样保留；不要因为这次没被提到就删掉。",
        "- 已解决的分歧要挪进 points，不要在两边都出现。",
        "- 每条不超过 30 字。",
        "- based_on_seq 必须填本次抽取所依据的最后一句的序号。",
    ]

    if previous:
        parts += ["", "【已有的共识与分歧】", previous]

    return "\n".join(parts)


def build_summary_prompt(seat: Seat, *, topic: str, consensus) -> str:
    """收尾总结的 prompt。

    **刻意不传 transcript。** 总结只允许基于 consensus_state 写，
    因为同一份 consensus 也在页面上渲染成共识面板 ——
    如果总结另读一遍原文，就必然出现「面板说已达成、总结说仍有分歧」。
    这是本作品在"同屏两套口径"上唯一可行的根治办法：
    不是靠纪律要求两处写一致，而是让它们物理上只有一份可读的输入。
    """
    points = "\n".join(f"- {p}" for p in consensus.points) or "（无）"
    disputes = "\n".join(f"- {d}" for d in consensus.disputes) or "（无）"

    return "\n".join(
        [
            f"你是「{seat.name}」，本场圆桌的收尾人。",
            "",
            f"【讨论议题】{topic}",
            f"【总结所依据】第 {consensus.based_on_seq} 句为止的共识记录（v{consensus.version}）",
            "",
            "【已达成的共识】",
            points,
            "",
            "【仍未解决的分歧】",
            disputes,
            "",
            "【硬性规则】",
            "1. 只能使用上面列出的内容，不得引入新论点、新数字、新人物。",
            "2. 分歧为零时可以说已经收敛；但不要因此夸大成「取得一致意见」——",
            "   共识条目各自的边界仍要交代清楚。",
            "3. 4-6 句，直接说结论，不要「感谢各位」这类客套。",
            "4. 不给行动建议清单，除非共识里已经明确写了。",
        ]
    )
