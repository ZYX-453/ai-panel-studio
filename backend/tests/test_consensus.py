"""共识 / 分歧合并测试 —— 先写，此时 app.consensus 还不存在。

这一组测的是本项目的核心不变量（PRD 决策 D3）：共识与分歧只有一份真相源。
最容易出的错不是"抽得不够准"，而是**旧分歧被悄悄抹掉**：
新一轮抽取只看到最近几句，如果直接用抽取结果覆盖，之前未解决的分歧就消失了，
屏幕上会出现"已全部达成共识"这种明显不成立的结论。
"""

from __future__ import annotations

import pytest

from app.consensus import merge
from app.models import Consensus


EMPTY = Consensus(version=0, points=(), disputes=(), based_on_seq=0)


def c(version: int, points=(), disputes=(), based_on_seq: int = 0) -> Consensus:
    return Consensus(
        version=version,
        points=tuple(points),
        disputes=tuple(disputes),
        based_on_seq=based_on_seq,
    )


# ---------------------------------------------------------------------------
# 版本与追溯
# ---------------------------------------------------------------------------

def test_version_increments_on_change() -> None:
    out = merge(EMPTY, points=["远程损害弱连接"], disputes=[], based_on_seq=4)
    assert out.version == 1


def test_based_on_seq_is_recorded() -> None:
    out = merge(EMPTY, points=["A"], disputes=[], based_on_seq=7)
    assert out.based_on_seq == 7


def test_based_on_seq_never_goes_backwards() -> None:
    """乱序到达的抽取结果不能把水位线往回拨，否则前端显示的"基于前 N 句"会跳。"""
    prev = c(2, points=["A"], disputes=["B"], based_on_seq=10)
    out = merge(prev, points=["A", "C"], disputes=["B"], based_on_seq=6)
    assert out.based_on_seq == 10


def test_no_change_returns_same_version() -> None:
    """抽出来和现在一模一样时不该刷版本号 —— 否则"实时更新"会变成噪声。"""
    prev = c(3, points=["A"], disputes=["B"], based_on_seq=8)
    out = merge(prev, points=["A"], disputes=["B"], based_on_seq=8)
    assert out.version == 3, "内容没变却涨了版本，界面会一直闪"
    assert out == prev


# ---------------------------------------------------------------------------
# 分歧不能丢
# ---------------------------------------------------------------------------

def test_unresolved_dispute_survives_when_not_mentioned() -> None:
    """新一轮没提到某条分歧，不代表它解决了。这条是本模块存在的主要理由。"""
    prev = c(1, points=["A"], disputes=["K 靠合同还是靠规范"], based_on_seq=4)
    out = merge(prev, points=["A", "B"], disputes=[], based_on_seq=8)
    assert "K 靠合同还是靠规范" in out.disputes, "未提到的旧分歧被抹掉了"
    assert out.version == 2


def test_dispute_promoted_to_point_when_resolved() -> None:
    """抽取结果里把它列进了共识，才算解决，并且要从分歧里移除。"""
    prev = c(1, points=[], disputes=["产线能不能四天"], based_on_seq=4)
    out = merge(prev, points=["产线不能四天"], disputes=[], based_on_seq=8)
    assert "产线能不能四天" not in out.disputes
    assert "产线不能四天" in out.points


def test_points_and_disputes_are_disjoint() -> None:
    """同一条文字不能同时出现在两边，那是最刺眼的同屏矛盾。"""
    prev = c(1, points=[], disputes=["X"])
    out = merge(prev, points=["X"], disputes=["X"], based_on_seq=5)
    assert not (set(out.points) & set(out.disputes)), f"两边重复：{set(out.points) & set(out.disputes)}"


# ---------------------------------------------------------------------------
# 去重与容量
# ---------------------------------------------------------------------------

def test_points_are_deduplicated() -> None:
    out = merge(EMPTY, points=["A", "A", "B", "B", "A"], disputes=[], based_on_seq=4)
    assert list(out.points) == ["A", "B"]


def test_points_capped_at_max() -> None:
    """面板是给人看的，堆到十几条就没人读了。"""
    out = merge(EMPTY, points=[f"P{i}" for i in range(20)], disputes=[], based_on_seq=4, max_points=6)
    assert len(out.points) == 6


def test_cap_keeps_earliest_points() -> None:
    out = merge(EMPTY, points=[f"P{i}" for i in range(20)], disputes=[], based_on_seq=4, max_points=3)
    assert list(out.points) == ["P0", "P1", "P2"]


# ---------------------------------------------------------------------------
# 类型安全：返回不可变对象，防止调用方改到共享状态
# ---------------------------------------------------------------------------

def test_result_is_immutable() -> None:
    out = merge(EMPTY, points=["A"], disputes=[], based_on_seq=2)
    assert isinstance(out.points, tuple)
    assert isinstance(out.disputes, tuple)
    with pytest.raises((AttributeError, TypeError)):
        out.points.append("B")  # type: ignore[attr-defined]


def test_prev_is_not_mutated() -> None:
    prev = c(2, points=["A"], disputes=["B"], based_on_seq=4)
    merge(prev, points=["A", "C"], disputes=["B"], based_on_seq=8)
    assert prev == c(2, points=["A"], disputes=["B"], based_on_seq=4), "合并不能改动入参"
