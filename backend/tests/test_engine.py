"""引擎契约测试。

用假模型跑完整场讨论，测的不是"模型说了什么"（那不可断言），
而是**引擎在拿到确定的输入时，是否按规矩推进**：
阶段顺序、事件与发言的先后、收尾原因、降级能否留下痕迹。

这几条正是"前端没有做仿真"的反面证据 —— 如果引擎能自己在无网络下
跑完一场并把事件按正确顺序写进库，那屏幕上的时间线就不是装出来的。
"""

from __future__ import annotations

import json

import pytest

from app.engine import (
    Engine,
    EngineConfig,
    EventBus,
    extract_mentions,
    phase_for,
    should_close,
)
from app.lineup import build_from_pool, validate, LineupError
from app.llm import ChatResult, LLMError, Usage
from app.models import Consensus, Seat
from app.store import Store


# ------------------------------------------------------------------ 假模型


class FakeLLM:
    """按 prompt 里的标记决定返回什么。不联网、可预测。"""

    def __init__(self, *, fail_times: int = 0, converge_after: int = 2) -> None:
        self.fail_times = fail_times
        self.converge_after = converge_after
        self.calls = 0
        self.consensus_calls = 0

    def chat(self, messages, *, temperature=0.9, max_tokens=800, json_mode=False):
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise LLMError("模拟失败", retryable=True)

        if json_mode:
            self.consensus_calls += 1
            n = self.consensus_calls
            points = [f"第{i}条共识" for i in range(1, min(n, 3) + 1)]
            # 前几版故意留一条分歧；达到阈值后清空，用来触发收敛
            disputes = [] if n >= self.converge_after else ["双方对适用范围仍未达成一致"]
            body = json.dumps(
                {"points": points, "disputes": disputes, "based_on_seq": 999},
                ensure_ascii=False,
            )
            return ChatResult(content=body, usage=Usage(10, 20), latency_ms=5, model="fake")

        return ChatResult(
            content="我不同意这个前提。先看数据再下判断，否则讨论会停在感受层面。",
            usage=Usage(10, 20),
            latency_ms=5,
            model="fake",
        )


@pytest.fixture()
def store(tmp_path):
    s = Store(str(tmp_path / "t.db"))
    s.init_schema()
    yield s
    s.close()


def make_discussion(store: Store, *, max_turns: int = 24, expert_count: int = 4) -> str:
    panel = build_from_pool("远程办公是否应该成为默认工作方式", expert_count)
    validate(panel, expert_count)
    did = "d_test"
    store.create_discussion(
        did=did,
        topic="远程办公是否应该成为默认工作方式",
        expert_count=expert_count,
        max_turns=max_turns,
        panel=panel,
    )
    return did


# ------------------------------------------------------------------ 纯函数


@pytest.mark.parametrize(
    "idx,expected",
    [(1, "OPENING"), (2, "EXPLORE"), (10, "EXPLORE"), (11, "CLASH"), (24, "CLOSING")],
)
def test_phase_for_follows_turn_budget(idx: int, expected: str) -> None:
    # 24×0.4=9.6 向上取整为 10，所以第 10 句仍是 EXPLORE，第 11 句进入 CLASH
    assert phase_for(idx, 24) == expected


def test_phase_for_always_ends_with_closing() -> None:
    for max_turns in (6, 12, 24, 40):
        assert phase_for(max_turns, max_turns) == "CLOSING"


def test_should_close_respects_min_turns() -> None:
    """轮数太早就宣布收敛会显得敷衍，所以有下限。"""
    c = Consensus(version=3, points=("a", "b", "c"), disputes=(), based_on_seq=5)
    ok, _ = should_close(turn_count=5, max_turns=24, consensus=c, zero_dispute_streak=2)
    assert not ok


def test_should_close_distinguishes_converged_from_max_turns() -> None:
    """两种收尾必须给出不同原因：谈完了 ≠ 时间到了。"""
    c = Consensus(version=3, points=("a", "b", "c"), disputes=(), based_on_seq=12)
    ok, reason = should_close(turn_count=12, max_turns=24, consensus=c, zero_dispute_streak=2)
    assert (ok, reason) == (True, "converged")

    ok2, reason2 = should_close(
        turn_count=23, max_turns=24, consensus=c, zero_dispute_streak=0
    )
    assert (ok2, reason2) == (True, "max_turns")


def test_should_close_needs_two_clean_versions() -> None:
    """只有一版没分歧就收尾太急：可能是这一轮没人提，而不是真的谈拢了。"""
    c = Consensus(version=3, points=("a", "b", "c"), disputes=(), based_on_seq=12)
    ok, _ = should_close(turn_count=12, max_turns=24, consensus=c, zero_dispute_streak=1)
    assert not ok


def test_extract_mentions_only_full_names() -> None:
    seats = (
        Seat("p0", "host", 0, "陈立"),
        Seat("p1", "expert", 1, "周敏"),
        Seat("p2", "expert", 2, "许巍"),
    )
    assert extract_mentions("周敏刚才说的弱连接，我有不同看法", seats) == ("p1",)
    assert extract_mentions("我同意这个判断", seats) == ()


# ------------------------------------------------------------------ 阵容


def test_template_lineup_is_deterministic_and_valid() -> None:
    a = build_from_pool("任意议题", 4, seed=0)
    b = build_from_pool("任意议题", 4, seed=0)
    assert [p["name"] for p in a] == [p["name"] for p in b]
    validate(a, 4)


def test_template_lineup_changes_with_seed() -> None:
    a = build_from_pool("任意议题", 4, seed=0)
    b = build_from_pool("任意议题", 4, seed=1)
    assert [p["name"] for p in a] != [p["name"] for p in b]


def test_validate_rejects_duplicate_stance() -> None:
    panel = build_from_pool("议题", 3)
    panel[2]["stance"] = panel[1]["stance"]
    with pytest.raises(LineupError, match="立场过近"):
        validate(panel, 3)


def test_validate_rejects_duplicate_color() -> None:
    panel = build_from_pool("议题", 3)
    panel[2]["color"] = panel[1]["color"]
    with pytest.raises(LineupError, match="重复颜色"):
        validate(panel, 3)


def test_validate_rejects_bad_seat() -> None:
    panel = build_from_pool("议题", 3)
    panel[2]["seat"] = 9
    with pytest.raises(LineupError, match="座位"):
        validate(panel, 3)


# ------------------------------------------------------------------ 整场


def test_run_produces_ordered_events_and_finishes(store: Store) -> None:
    did = make_discussion(store, max_turns=24)
    engine = Engine(store, FakeLLM(converge_after=2), EventBus(), config=EngineConfig(max_turns=24))
    engine.run(did)

    d = store.get_discussion(did)
    assert d["status"] == "FINISHED"
    assert d["ended_reason"] in ("converged", "max_turns")
    assert d["final_summary"]

    events = store.get_events(did)
    # seq 必须连续，否则重放会漏帧
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    # 事件类型必须都在契约里
    assert {e["type"] for e in events} <= {
        "phase", "panelist_state", "utterance", "consensus", "ended", "error"
    }

    # 每条发言之前，必须先有同一人的"准备发言"
    pending: dict[str, int] = {}
    for i, e in enumerate(events):
        if e["type"] == "panelist_state" and e["payload"]["state"] == "准备发言":
            pending[e["payload"]["panelist_id"]] = i
        if e["type"] == "utterance":
            pid = e["payload"]["panelist_id"]
            assert pid in pending, f"{pid} 没有说话前的状态事件"
            assert pending[pid] < i

    # 收尾事件必须是最后一条
    assert events[-1]["type"] == "ended"
    assert events[-1]["payload"]["reason"] == d["ended_reason"]


def test_run_uses_every_expert_and_advances_phases(store: Store) -> None:
    """一条回归：曾经整场 24 轮全是主持人独白。

    原因是阶段推进里多写了一个「OPENING 要粘住」的分支，导致 phase 永远停在 OPENING，
    而 OPENING 阶段调度器只会选主持人。当时 71 条测试全绿 —— 因为它们只检查
    "事件顺序对不对"，没有一条检查"到底有没有别人说过话"。

    所以这里同时钉两件事：
      1. 每位专家都必须真的上过场（调度器有"未开口者优先"，前几轮就该覆盖全）；
      2. 阶段必须真的从 OPENING 推进到 EXPLORE / CLASH。
    """
    did = make_discussion(store, max_turns=16, expert_count=4)
    Engine(store, FakeLLM(), EventBus(), config=EngineConfig(max_turns=16)).run(did)

    speakers = {u["panelist_id"] for u in store.get_utterances(did)}
    experts = {p.id for p in store.get_panel(did) if p.role == "expert"}
    assert experts <= speakers, f"有专家没说过话：{sorted(experts - speakers)}"

    phases = {e["payload"]["phase"] for e in store.get_events(did) if e["type"] == "phase"}
    assert "OPENING" in phases
    assert phases & {"EXPLORE", "CLASH"}, f"阶段没有推进：{sorted(phases)}"


def test_run_never_puts_internal_state_into_transcript(store: Store) -> None:
    """举手/准备发言是内部事件，结构上就不该出现在 transcript 里。"""
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(), EventBus(), config=EngineConfig(max_turns=12)).run(did)

    for u in store.get_utterances(did):
        for word in ("准备发言", "发言中", "待机", "举手"):
            assert word not in u["content"]


def test_consensus_is_single_source_for_summary(store: Store) -> None:
    """总结与面板同源：总结提到的版本必须等于库里当前共识版本。"""
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(), EventBus(), config=EngineConfig(max_turns=12)).run(did)

    snap = store.snapshot(did)
    assert snap["final_summary"]
    events = [e for e in store.get_events(did) if e["type"] == "consensus"]
    if events:
        assert snap["consensus"]["version"] == events[-1]["payload"]["version"]


def test_consecutive_failures_degrade_and_record_reason(store: Store) -> None:
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(fail_times=99), EventBus(), config=EngineConfig(max_turns=12)).run(did)

    d = store.get_discussion(did)
    assert d["status"] == "FINISHED"
    assert d["ended_reason"] == "degraded"
    # 降级也要说话，不能留下空白的 summary
    assert d["final_summary"]

    types = [e["type"] for e in store.get_events(did)]
    assert "error" in types
    assert types[-1] == "ended"


def test_snapshot_shape_matches_contract(store: Store) -> None:
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(), EventBus(), config=EngineConfig(max_turns=12)).run(did)
    snap = store.snapshot(did)

    for key in ("id", "topic", "status", "ended_reason", "turn_count", "panel",
                "transcript", "consensus", "final_summary"):
        assert key in snap

    assert len(snap["panel"]) == 5
    assert snap["panel"][0]["role"] == "host"
    assert all("color" in p and p["color"].startswith("#") for p in snap["panel"])
    assert set(snap["consensus"]) >= {"version", "points", "disputes", "based_on_seq"}
