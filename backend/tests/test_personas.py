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
         catchphrase="我做过一个追踪研究", focus=("弱连接", "创新扩散"),
         target="「远程只是把工位搬回家，沟通并不会变少」"),
    Seat(id="e2", role="expert", seat=2, name="许巍", stance="远程可运营",
         catchphrase="在我们那儿是这么干的", focus=("异步协作",),
         target="「物理距离决定协作质量，远程注定伤效率」"),
    Seat(id="e3", role="expert", seat=3, name="林雪", stance="应按岗位分层",
         catchphrase="先分个类", focus=("岗位可远程性",),
         target="「远程是公司级开关，要么全开要么全关」"),
    Seat(id="e4", role="expert", seat=4, name="何远", stance="早期必须线下",
         catchphrase="活下来最重要", focus=("默契形成",),
         target="「小公司更该一步到位搞远程」"),
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


def test_expert_prompt_carries_focus_and_target() -> None:
    """关注点决定他看什么，靶子决定他朝谁开火 —— 两个都要写进去。

    靶子这一栏原名 attack，存的是"这类人容易犯的错"。那个写法只会让模型
    自我审查（"注意别翻车"），不会去攻击别人，实测 24 轮里二十多句都在互相补充。
    现在要求的是"最想推翻的那句话"，并且必须带上"点名反驳"这个动作。
    """
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "弱连接" in p
    assert "「远程只是把工位搬回家，沟通并不会变少」" in p
    assert "最想推翻" in p
    assert "反驳" in p, "只给靶子不要求反驳，模型会把它当背景介绍"


def test_no_self_censorship_instruction_left() -> None:
    """旧的"注意不要在这里翻车"必须彻底消失。

    它和新指令方向相反：一边让他自我审查，一边让他开火，
    模型会折中成"温和地补充一点不同看法"——正是要治的那个毛病。
    """
    for seat in SEATS:
        if seat.role != "expert":
            continue
        p = build_expert_prompt(seat, topic=TOPIC, transcript=turns(), seats=SEATS)
        assert "容易犯的错" not in p
        assert "不要在这里翻车" not in p


def test_target_section_omitted_when_empty() -> None:
    """模型偶尔不给 target。缺了就不渲染那一段，不能留一个空靶子让模型自己编。"""
    bare = Seat(id="x", role="expert", seat=1, name="某人", stance="某立场")
    p = build_expert_prompt(bare, topic=TOPIC, transcript=turns(), seats=(bare,))
    assert "最想推翻" not in p


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


def test_prompt_forbids_uniform_sentence_pattern() -> None:
    """实测：一场 24 句里有 13 句都以「……我认，但……」开头，四个专家读成一个人。

    角色的区分不只靠立场和口头禅，句式也一样 —— 所以这条写进硬性规则。
    """
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "我认" in p and "句式" in p or "先肯定对方" in p


def test_prompt_repeat_warning_only_when_asked() -> None:
    """重复预警不能常驻：常驻等于告诉模型"你每次都复读"，反而误导。"""
    plain = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    warned = build_expert_prompt(
        by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS, avoid_repeat=True
    )
    assert "【重复预警】" not in plain
    assert "【重复预警】" in warned


def test_expert_prompt_lists_his_own_earlier_lines() -> None:
    """窗口里大多是别人，模型对自己的上一句没有记忆 —— 必须单独列出来。"""
    p = build_expert_prompt(by_id("e1"), topic=TOPIC, transcript=turns(), seats=SEATS)
    assert "【你刚才说过的】" in p
    assert "第2句 你：弱连接会受损。" in p


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


def test_host_prompt_has_exactly_one_task_per_phase() -> None:
    """每个阶段只能有一条【本轮任务】。

    这不是假想的风险：加"把议题收成一个子问题"时，一次误编辑让 CLOSING 的任务块
    落进了 `elif CLASH` 分支，于是交锋阶段同时收到"点明这是两个问题"和
    "做收尾总结"两条互斥指令，而 115 条测试全绿 —— 没有任何一条看着 prompt 本身。
    """
    expectations = {
        "OPENING": "子问题",
        "CLASH": "两个问题",
        "CLOSING": "收尾总结",
    }
    for phase, keyword in expectations.items():
        p = build_host_prompt(by_id("h"), topic=TOPIC, transcript=turns(), phase=phase)
        assert p.count("【本轮任务】") == 1, f"{phase} 有 {p.count('【本轮任务】')} 条任务"
        assert keyword in p, f"{phase} 的任务里没提到「{keyword}」"

    # 陈述阶段不设任务：这一段本来就该让各方把话说完，主持人只点名不推进
    p = build_host_prompt(by_id("h"), topic=TOPIC, transcript=turns(), phase="EXPLORE")
    assert "【本轮任务】" not in p
    assert "收尾总结" not in p, "EXPLORE 阶段混进了收尾任务"


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
