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


#: 以下四句逐字取自 11-20 改人设后的首场真实运行（d_f91db4b0）。
#: 它们都是"抛出靶子再举反例"的标准攻击，但当时全被判成「补充/抛论点」，
#: 导致统计表上「反驳 0」而正文其实已经打起来了。
TARGET_FIRE_SENTENCES = (
    "有人会说工具够好、人在哪儿都一样。那我们上个跨部门项目，"
    "远程时需求在群里来回三天没定，搬到同一层楼两小时就吵完了。",
    "有人会说，员工不在眼皮底下团队就会散。我拿个数：GitLab 全远程一千多人，"
    "去年营收还两位数增长，散的是靠盯人撑起来的管理。",
    "有人会说远程是全员默认权利。那把客服排班表发下去：晚八点一人请假，"
    "远程坐席在别的城市补不上，弃呼率翻倍，这权利谁在替他们行使？",
    # 第二场（d_5fb53d06）长出来的变体：同一个动作换了三种说法。
    "有人在说基础训练可以全部省略、新人直接上手高阶任务——那我问一句："
    "线上半夜内存泄漏，日志里连续三次 GC 抖动，没有手调过的人拿什么认？",
    "谁要再说初级必须从简单重复开始，报个数：我们组去年校招六个，今年两个。",
    "别跟我谈培养，先看看今年校招 HC 还剩。",
)


@pytest.mark.parametrize("text", TARGET_FIRE_SENTENCES)
def test_target_firing_counts_as_rebuttal(text: str) -> None:
    """「有人会说 X」是把靶子摆上台面的固定句式，属于攻击而不是补充。

    这个句式不是凭空加的：专家 prompt 里要求"没人开火时把靶子抛出来，
    写成『有人会说……』然后当场拆掉它"，是那条指令的直接产物。
    分类器不认它，就等于新的分支跑起来了、旧的口径没跟上。
    """
    assert classify_intent(text, phase="CLASH") == "反驳"


def test_contrast_with_que_marker_is_rebuttal() -> None:
    """「却」是对比转折，前后必然互相否定。"""
    text = "客服上线缺口一人，接通率掉八个百分点，坐办公室的却多睡一小时。"
    assert classify_intent(text, phase="EXPLORE") == "反驳"


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
