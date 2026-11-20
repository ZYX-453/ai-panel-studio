"""发言文本的两件判定：**意图归类**与**复读检测**。

为什么这两件事必须由代码做，而不是交给模型：

- 意图：调度器只知道"该谁说话"，不知道"说出口的是什么"。真实跑一场
  24 句的讨论，调度器的 intent_hint 给出 18 个「补充」—— 因为它的规则里
  「追问」要求有人点名、「反驳」要求同一个人连说两句，剩下全落到兜底分支。
  于是时间线上一边标着「交锋」，一边每句挂个「补充」，自相矛盾。
  意图该由**已经生成的那句话**反推，而不是由排班表猜。
- 复读：实测同一场里同一专家把「代码是你合的，锅就是你的」说了两遍，
  逐字相同；跨专家还会出现一个词（那场是「签字栏」）被所有人反复借用。
  这类问题模型自己看不见（它每轮只看到最近 6 句），但读的人一眼就看见。

两个函数都是纯函数，不碰网络、不碰库，所以能被单测直接钉住。
"""

from __future__ import annotations

import re

from .models import Turn

#: 判定"在反驳"的标记。刻意用短词而不是整句：模型措辞每轮都在变，
#: 但"不同意 / 站不住 / 恰恰相反"这类开口方式是稳定的。
REBUT_MARKERS: tuple[str, ...] = (
    "不同意",
    "不认同",
    "不对",
    "不成立",
    "站不住",
    "说不通",
    "恰恰相反",
    "我反对",
    "我怀疑",
    "未必",
    "并非",
    "问题不在",
    "错在",
    "别把",
    "这没什么好商量",
)

#: 追问的形态：真的在问，而不是反问式地否定。
QUESTION = re.compile(r"[？?]")

#: 反问的开口方式。带问号但实质是否定 —— 这两种在界面上不能都写成「追问」，
#: 否则"交锋"阶段看起来像记者会。
RHETORICAL_MARKERS: tuple[str, ...] = (
    "难道",
    "凭什么",
    "你让",
    "真以为",
    "算什么",
    "有什么用",
    "不就得",
    "不就是",
)


def classify_intent(
    text: str,
    *,
    phase: str,
    first_time: bool = False,
) -> str:
    """按**说了什么**给这句发言贴意图标签。

    顺序是有讲究的：
    1. 开场 / 收尾由阶段直接决定（这两句是主持人的，不是专家在表态）；
    2. 先判反驳再判追问 —— 「你让按按钮的人替造按钮的人坐牢？」是反问，
       形式上有问号，实质是反驳。反过来判会把交锋写成提问；
    3. 第一次开口且没有对抗标记 → 抛论点；
    4. 其余为补充。
    """
    if phase == "OPENING":
        return "开场"
    if phase == "CLOSING":
        return "收尾"

    body = (text or "").strip()
    if not body:
        return "补充"

    if any(mark in body for mark in REBUT_MARKERS):
        return "反驳"
    if QUESTION.search(body):
        if any(mark in body for mark in RHETORICAL_MARKERS):
            return "反驳"
        return "追问"
    if first_time:
        return "抛论点"
    return "补充"


def _compact(text: str) -> str:
    return "".join((text or "").split())


def _lcs_length(a: str, b: str) -> int:
    """最长公共子串长度（不是子序列）。

    选它而不是 Jaccard，是拿真实数据量出来的：把一场 22 句的讨论两两比对，
    那对真复读（同一专家隔十四句说同一句话）最长公共段 21 字，
    其余所有正常呼应的最长公共段都不超过 8 字。区分度比 n-gram 集合好得多 ——
    Jaccard 会被两句长度差异稀释到 0.28，落在任何合理阈值之下，等于漏判。
    """
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


#: 判为复读的门槛：连续 12 个字一模一样。
#: 中文里 12 个连续相同的字不可能是巧合，也不可能只是"借用了同一个词"；
#: 而正常的呼应、反驳、补充，公共段一般只有几个字（数据里最长 8）。
REPEAT_MIN_COMMON = 12

#: 太短的句子不判复读：一句"我同意"重复出现是正常的接话
REPEAT_MIN_LEN = 12


def is_repeat(text: str, previous: list[str] | tuple[str, ...]) -> bool:
    """这句话是不是把已有某一句几乎原样重说了一遍。"""
    mine = _compact(text)
    if len(mine) < REPEAT_MIN_LEN:
        return False
    return any(
        _lcs_length(mine, _compact(p)) >= REPEAT_MIN_COMMON for p in previous if p
    )


def most_similar(text: str, turns: list[Turn]) -> tuple[int, int | None]:
    """给日志用：最长公共段多长、撞的是第几句。判定本身用 `is_repeat`。"""
    mine = _compact(text)
    best, best_seq = 0, None
    for t in turns:
        score = _lcs_length(mine, _compact(t.text))
        if score > best:
            best, best_seq = score, t.seq
    return best, best_seq
