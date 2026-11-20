"""人设装配测试 —— 先写，此时 app.personas 还不存在。

要钉死的东西：每个角色说话要像他自己，而且**不能串味**。
串味是这类多角色系统最常见的失败：四个人说话一个腔调，
观众看到的不是圆桌，是同一个人换了四个名字。
"""

from __future__ import annotations

from app.models import Seat, Turn
from app.personas import build_consensus_prompt, build_expert_prompt, build_host_prompt

SEATS = (
    Seat(id="h", role="host", seat=0, name="陈立", stance="中立", catchphrase="先说结论"),
    Seat(id="e1", role="expert", seat=1, name="周敏", stance="远程伤害弱连接",
         catchphrase="我做过一个追踪研究", focus=("弱连接", "创新扩散"), attack="拿个案当规律"),
    Seat(id="e2", role="expert", seat=2, name="许巍", stance="远程可运营",
         catchphrase="在我们那儿是这么干的", focus=("异步协作",), attack="拿自己公司套所有人"),
    Seat(id="e3", role="expert", seat=3, name="林雪", stance="应按岗位分层",
         catchphrase="先分个类", focus=("岗位可远程性",), attack="把问题拖进分类学"),
    Seat(id="e4", role="expert", seat=4, name="何远", stance="早期必须线下",
         catchphrase="活下来最重要", focus=("默契形成",), attack="拿早期经验否定成熟期"),
)

TOPIC = "远程办公是否应该成为公司默认工作方式"


def turns() -> tuple[Turn, ...]:
    return (
        Turn(seq=1, speaker_id="h", text="今天聊远程办公，只谈证据。"),
        Turn(seq=2, speaker_id="e1", text="弱连接会受损。"),
        Turn(seq=3, speaker_id="e2", text="我不同意。"),
    )


def by_id(sid: str) -> Seat:
    return next(s for s in SEATS if s.id == sid)


# ---------------------------------------------------------------------------
# 人设是否真的进了 prompt
# ---------------------------------------------------------------------------

def test_expert_prompt_contains_own_identity() -> None:
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "周敏" in p
    assert "远程伤害弱连接" in p
    assert "我做过一个追踪研究" in p, "口头禅没进 prompt，角色就没有辨识度"


def test_expert_prompt_carries_focus_and_blind_spot() -> None:
    """关注点决定他看什么，盲区决定他容易错在哪 —— 两个都要写进去。"""
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "弱连接" in p
    assert "拿个案当规律" in p


def test_no_crosstalk_between_personas() -> None:
    """A 的 prompt 里不能出现 B 的口头禅，否则模型会把两个角色混成一个。"""
    for seat in SEATS:
        if seat.role != "expert":
            continue
        p = build_expert_prompt(seat, topic=TOPIC, transcript=turns(), seats=SEATS)
        for other in SEATS:
            if other.id == seat.id:
                continue
            assert other.catchphrase not in p, (
                f"{seat.name} 的 prompt 里混进了 {other.name} 的口头禅「{other.catchphrase}」"
            )


def test_prompts_differ_between_experts() -> None:
    texts = {
        s.id: build_expert_prompt(s, topic=TOPIC, transcript=turns(), seats=SEATS)
        for s in SEATS
        if s.role == "expert"
    }
    assert len(set(texts.values())) == len(texts), "不同专家的 prompt 出现雷同"


# ---------------------------------------------------------------------------
# 硬约束：短、不复述、不吐 JSON
# ---------------------------------------------------------------------------

def test_prompt_limits_length_of_utterance() -> None:
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "1-2 句" in p, "没写「1-2 句」，模型很容易一次说一大段"


def test_prompt_forbids_restating_others() -> None:
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "复述" in p


def test_prompt_forbids_json_output() -> None:
    """需求要求禁止把 JSON 原文显示到页面，最直接的防线是 prompt 里不要求结构化输出。"""
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "JSON" in p


# ---------------------------------------------------------------------------
# 上下文窗口
# ---------------------------------------------------------------------------

def test_prompt_only_includes_recent_window() -> None:
    """transcript 不能整段塞进去：token 会炸，而且早期的内容早就偏离话题了。"""
    long_history = tuple(
        Turn(seq=i, speaker_id=f"e{(i % 4) + 1}", text=f"历史发言{i}") for i in range(1, 41)
    )
    p = build_expert_prompt(
        by_id("e1"), topic=TOPIC, transcript=long_history, seats=SEATS, window=6
    )
    assert "历史发言40" in p, "最近一句应当在窗口里"
    assert "历史发言1\n" not in p and "历史发言2" not in p, "40 句历史被整段塞进了 prompt"
    assert len(p) < 4000, f"prompt 过长：{len(p)} 字符"


def test_prompt_names_the_target_when_responding() -> None:
    p = build_expert_prompt(
        by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS, respond_to_seq=3
    )
    assert "许巍" in p, "要回应谁必须写清楚，否则模型容易自说自话"


# ---------------------------------------------------------------------------
# 主持人：另一种职责，不能和专家共用一套
# ---------------------------------------------------------------------------

def test_host_prompt_differs_from_expert_prompt() -> None:
    host = build_host_prompt(by_id("h"), topic=TOPIC, transcript=turns(), phase="CLOSING")
    expert = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert host != expert
    assert "不预设立场" in host or "中立" in host, "主持人 prompt 必须写明不站队"


def test_host_closing_prompt_asks_for_summary_without_new_claims() -> None:
    p = build_host_prompt(by_id("h"), topic=TOPIC, transcript=turns(), phase="CLOSING")
    assert "收" in p or "总结" in p


# ---------------------------------------------------------------------------
# 共识抽取：必须只输出结构化结果，且带着"基于第几句"
# ---------------------------------------------------------------------------

def test_consensus_prompt_requires_based_on_seq() -> None:
    p = build_consensus_prompt(topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "based_on_seq" in p, "不写基于第几句，版本就没法追溯"


def test_consensus_prompt_asks_to_keep_unresolved_disputes() -> None:
    p = build_consensus_prompt(topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "分歧" in p
    assert "不要" in p or "保留" in p, "没要求保留未消解的分歧，旧分歧会被悄悄抹掉"
