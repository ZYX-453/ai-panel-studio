"""调度器测试 —— 先写，此时 app.scheduler 还不存在，全部应当失败。

被测的问题（见 docs/01-PRD.md 决策 D2）：
    需求同时要求「专家自主决定发言顺序，不允许机械式轮流」和「工程可验证」。
    这两件事天然冲突。解法是把"自主"拆成两层——模型只产出说什么，
    谁下一个说由这个纯函数决定。

因此下面每一条都是在钉死一个**可以被代码保证**的性质，
而不是去断言"讨论看起来自然"这种没法测的东西。
"""

from __future__ import annotations

import pytest

from app.models import Seat, Turn
from app.scheduler import Decision, pick_next


# ---------------------------------------------------------------------------
# 夹具：一位主持 + 四位立场互斥的专家
# ---------------------------------------------------------------------------

def make_seats() -> tuple[Seat, ...]:
    return (
        Seat(id="h", role="host", seat=0, name="主持", stance="中立"),
        Seat(id="e1", role="expert", seat=1, name="周敏", stance="远程伤害弱连接"),
        Seat(id="e2", role="expert", seat=2, name="许巍", stance="远程可运营"),
        Seat(id="e3", role="expert", seat=3, name="林雪", stance="应按岗位分层"),
        Seat(id="e4", role="expert", seat=4, name="何远", stance="早期必须线下"),
    )


def turn(seq: int, speaker: str, mentions: tuple[str, ...] = (), intent: str = "") -> Turn:
    return Turn(seq=seq, speaker_id=speaker, text=f"第{seq}句", mentions=mentions, intent=intent)


def run(state_turns: list[Turn], phase: str = "EXPLORE", seed: int = 7) -> Decision:
    return pick_next(seats=make_seats(), turns=tuple(state_turns), phase=phase, seed=seed)


# ---------------------------------------------------------------------------
# 边界：谁有资格被选中
# ---------------------------------------------------------------------------

def test_host_never_picked_during_debate() -> None:
    """主持人只在开场和收尾说话，自由讨论阶段不能把发言权丢给主持人。"""
    turns: list[Turn] = []
    for i in range(12):
        d = run(turns)
        assert d.seat_id != "h", f"第 {i} 次选中了主持人"
        turns.append(turn(i + 1, d.seat_id))


def test_pick_is_deterministic_for_same_seed() -> None:
    """同一状态 + 同一 seed 必须给同一结果 —— 否则测试和回放都没法做。"""
    turns = [turn(1, "e1"), turn(2, "e2")]
    a = run(turns, seed=42)
    b = run(turns, seed=42)
    assert a.seat_id == b.seat_id
    assert a.reason == b.reason


def test_decision_carries_reason() -> None:
    """决策必须能解释自己，日志和前端提示都靠它。"""
    d = run([])
    assert isinstance(d.reason, str) and d.reason.strip(), "reason 不能为空"


def test_empty_transcript_still_picks_an_expert() -> None:
    d = run([])
    assert d.seat_id in {"e1", "e2", "e3", "e4"}


# ---------------------------------------------------------------------------
# 公平：不能有人饿死在场外
# ---------------------------------------------------------------------------

def test_everyone_speaks_before_anyone_speaks_twice() -> None:
    """前四轮必须把四位专家各点一次，这是「都有机会开口」的硬保证。"""
    turns: list[Turn] = []
    picked: list[str] = []
    for i in range(4):
        d = run(turns)
        picked.append(d.seat_id)
        turns.append(turn(i + 1, d.seat_id))
    assert len(set(picked)) == 4, f"前四轮出现重复发言者：{picked}"


def test_nobody_speaks_three_times_in_a_row() -> None:
    turns: list[Turn] = []
    for i in range(10):
        d = run(turns)
        turns.append(turn(i + 1, d.seat_id))
        if len(turns) >= 3:
            last3 = [t.speaker_id for t in turns[-3:]]
            assert not (last3[0] == last3[1] == last3[2]), f"出现三连发言：{last3}"


# ---------------------------------------------------------------------------
# 「自主」的核心：被点名的人优先，顺序不是排班表
# ---------------------------------------------------------------------------

def test_mentioned_expert_is_picked_next() -> None:
    """上一条发言里被点名的人，下一句就该是他。这是「有来有回」的最低要求。"""
    turns = [
        turn(1, "e1"),
        turn(2, "e2", mentions=("e3",)),
    ]
    d = run(turns)
    assert d.seat_id == "e3", f"点的是 e3，选了 {d.seat_id}"


def test_mentions_break_round_robin() -> None:
    """反复点名同一个人时，选择必须偏离座位顺序，不能退化成轮流念稿。"""
    turns = [
        turn(1, "e1"),
        turn(2, "e3", mentions=("e2",)),
    ]
    picks: list[str] = []
    for i in range(6):
        d = run(turns)
        picks.append(d.seat_id)
        turns.append(turn(len(turns) + 1, d.seat_id, mentions=("e2",) if d.seat_id != "e2" else ()))

    # 纯轮转下 e2 在 6 次里最多出现 2 次；被反复点名应当明显更多
    assert picks.count("e2") >= 3, f"点名没有带来优先级，选择序列：{picks}"

    # 而且不能是座位顺序的循环
    seats = make_seats()
    order = {s.id: s.seat for s in seats if s.role == "expert"}
    sorted_cycle = [k for k, _ in sorted(order.items(), key=lambda kv: kv[1])]
    assert picks[:4] != sorted_cycle, f"退化成机械轮转：{picks}"


def test_respond_to_points_at_the_mentioning_turn() -> None:
    """要回应就明确回应第几句 —— 前端才能显示「回应 周敏 第 2 句」。"""
    turns = [
        turn(1, "e1"),
        turn(2, "e2", mentions=("e3",)),
    ]
    d = run(turns)
    assert d.respond_to_seq == 2


def test_intent_hint_follows_situation() -> None:
    """被点名→追问；接话给刚说完的人→反驳；其余→补充。"""
    mentioned = run([turn(1, "e1"), turn(2, "e2", mentions=("e3",))])
    assert mentioned.intent_hint == "追问"

    # 点名会优先，所以"反驳"这条要在没人被点名时测
    rebut = run([turn(1, "e1"), turn(2, "e2")])
    assert rebut.intent_hint in {"反驳", "补充"}


# ---------------------------------------------------------------------------
# 冷却：刚说完的人不该连着被点到
# ---------------------------------------------------------------------------

def test_recent_speaker_gets_cooled_down() -> None:
    """无人被点名时，已经说过话的人要排在没说过的人后面。"""
    turns = [turn(1, "e1"), turn(2, "e2")]
    d = run(turns)
    assert d.seat_id in {"e3", "e4"}, f"应优先没开口的人，却选了 {d.seat_id}"


@pytest.mark.parametrize("phase", ["EXPLORE", "CLASH"])
def test_only_experts_in_both_debate_phases(phase: str) -> None:
    d = run([turn(1, "e1")], phase=phase)
    assert d.seat_id.startswith("e")
