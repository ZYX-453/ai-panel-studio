"""发言文本判定的用例：意图归类 + 复读检测。

这两件事都是"看文字本身"就能判的，所以写成纯函数、直接钉住。
它们对付的是真实跑出来的两种难看：

1. 一场 22 句的讨论，18 句都挂「补充」，而界面标着「交锋」；
2. 同一专家隔十四句把同一句话原样说第二遍。
"""

from __future__ import annotations

import pytest

from app.content import classify_intent, is_repeat, most_similar
from app.models import Turn


# ------------------------------------------------------------------ 意图


def test_opening_and_closing_belong_to_phase_not_content() -> None:
    """主持人的开场/收尾由阶段决定，跟他说了什么无关。"""
    assert classify_intent("今天聊远程办公，只谈证据。", phase="OPENING") == "开场"
    assert classify_intent("好，今天就到这儿。", phase="CLOSING") == "收尾"


@pytest.mark.parametrize(
    "text",
    [
        "我不同意弱连接这个说法。",
        "这个结论站不住，样本只有一家公司。",
        "恰恰相反，跨部门沟通反而变多了。",
        "别把管理问题算到远程头上。",
    ],
)
def test_rebuttal_is_detected_from_wording(text: str) -> None:
    assert classify_intent(text, phase="CLASH") == "反驳"


def test_question_is_follow_up() -> None:
    assert classify_intent("那拒签之后谁来补这份清单？", phase="CLASH") == "追问"


def test_rhetorical_question_is_rebuttal_not_question() -> None:
    """反问形式上是问句，实质是否定 —— 先判反驳再判问号，否则交锋会被写成提问。"""
    text = "你让一个按按钮的人，去替造按钮的人坐牢？"
    assert classify_intent(text, phase="CLASH") == "反驳"


def test_first_statement_is_claim_and_later_ones_are_supplement() -> None:
    assert classify_intent("担责得看签字栏是谁的名字。", phase="EXPLORE", first_time=True) == "抛论点"
    assert classify_intent("担责得看签字栏是谁的名字。", phase="EXPLORE", first_time=False) == "补充"


def test_empty_text_never_crashes() -> None:
    assert classify_intent("", phase="CLASH") == "补充"
    assert classify_intent("   ", phase="EXPLORE") == "补充"


# ------------------------------------------------------------------ 复读


def test_identical_sentence_is_repeat() -> None:
    prev = ["代码是你合的，锅就是你的，这没什么好商量。"]
    assert is_repeat("代码是你合的，锅就是你的，这没什么好商量。", prev)


def test_same_sentence_with_new_opening_is_repeat() -> None:
    """实测的形态：换个开头把同一句判断再说一遍。"""
    prev = ["代码是你合的，锅就是你的，这没什么好商量。真怕担不起，就別合。"]
    again = "代码是你合的，锅就是你的，这没什么好商量。要真怕背不动，就别签那一栏。"
    assert is_repeat(again, prev)


def test_normal_echo_is_not_repeat() -> None:
    """讨论本来就会互相引用同一批概念，别把正常呼应误杀。"""
    prev = ["远程办公会削弱弱连接，创新扩散会变慢。"]
    fresh = "我同意弱连接受损，但要看岗位：研发和销售的损失完全不是一个量级。"
    assert not is_repeat(fresh, prev)


def test_repeat_against_empty_history_is_false() -> None:
    assert not is_repeat("第一句发言", [])
    assert not is_repeat("", ["随便一句"])


def test_most_similar_points_at_the_offending_turn() -> None:
    turns = [
        Turn(seq=1, speaker_id="a", text="先看数据再下判断。"),
        Turn(seq=2, speaker_id="b", text="代码是你合的，锅就是你的。"),
        Turn(seq=3, speaker_id="c", text="签字栏上写谁的名字谁担责。"),
    ]
    common, seq = most_similar("代码是你合的，锅就是你的。", turns)
    assert seq == 2 and common >= 12
