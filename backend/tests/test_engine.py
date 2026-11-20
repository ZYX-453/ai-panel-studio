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
    MAX_SPEECH_CHARS,
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
    ok, _ = should_close(turn_count=5, max_turns=24, consensus=c, settled_streak=2)
    assert not ok


def test_should_close_distinguishes_converged_from_max_turns() -> None:
    """两种收尾必须给出不同原因：谈完了 ≠ 时间到了。"""
    c = Consensus(version=3, points=("a", "b", "c"), disputes=(), based_on_seq=12)
    ok, reason = should_close(turn_count=12, max_turns=24, consensus=c, settled_streak=2)
    assert (ok, reason) == (True, "converged")

    ok2, reason2 = should_close(
        turn_count=23, max_turns=24, consensus=c, settled_streak=0
    )
    assert (ok2, reason2) == (True, "max_turns")


def test_should_close_needs_two_settled_versions() -> None:
    """只有一版收口就收尾太急：可能是这一轮没人提，而不是真的谈拢了。"""
    c = Consensus(version=3, points=("a", "b", "c"), disputes=(), based_on_seq=12)
    ok, _ = should_close(turn_count=12, max_turns=24, consensus=c, settled_streak=1)
    assert not ok


def test_last_one_dispute_still_allows_converged() -> None:
    """11-20 放宽：悬着 1 条核心分歧也可以收尾。

    原判据要求"零分歧"，但专家 prompt 明确写着「不要因为别人反对就改口」，
    两条互斥，13 场没有一场走到。现在承认"只剩那个核心命题没谈拢"也算收尾。
    这是放宽而不是修复，所以必须有一条用例把"放宽了什么"钉住 ——
    否则以后有人把判据改回零分歧，不会有任何东西报红。
    """
    c = Consensus(
        version=5,
        points=("a", "b", "c"),
        disputes=("周报是否必要：一方认为书面留痕，一方认为看板足够",),
        based_on_seq=18,
    )
    ok, reason = should_close(turn_count=20, max_turns=24, consensus=c, settled_streak=2)
    assert (ok, reason) == (True, "converged")


def test_too_many_disputes_blocks_converged() -> None:
    """但分歧还有两条以上时不能收尾 —— 那说明还没聚焦。"""
    c = Consensus(
        version=5,
        points=("a", "b", "c"),
        disputes=("D1", "D2"),
        based_on_seq=18,
    )
    ok, _ = should_close(turn_count=20, max_turns=24, consensus=c, settled_streak=2)
    assert not ok


def test_converged_requires_having_reached_clash() -> None:
    """不能跳过交锋阶段就收尾。

    放宽判据后真跑出过一场：10 句 converged，阶段是
    OPENING → EXPLORE → CLOSING，前端时间线上整段 CLASH 消失，
    读起来像"刚摆完立场就宣布谈拢"。24 轮里 EXPLORE 到第 10 句
    （ceil(24*0.4)），所以第 10 句不该收、第 11 句才可以。
    """
    c = Consensus(version=5, points=("a", "b", "c"), disputes=("唯一那条",), based_on_seq=9)
    ok, _ = should_close(turn_count=10, max_turns=24, consensus=c, settled_streak=3)
    assert not ok, "还在 EXPLORE 就收尾，会缺掉 CLASH 阶段"

    ok2, reason2 = should_close(turn_count=11, max_turns=24, consensus=c, settled_streak=3)
    assert (ok2, reason2) == (True, "converged")


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


# ------------------------------------------------------------------ 文本质量


class RepeatThenFreshLLM(FakeLLM):
    """模拟实测到的复读：前几次都吐同一句，被重复预警后才换一句。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.speaker_prompts: list[str] = []

    def chat(self, messages, *, temperature=0.9, max_tokens=800, json_mode=False):
        if not json_mode:
            self.speaker_prompts.append(messages[0]["content"])
        if json_mode:
            return super().chat(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
        self.calls += 1
        if "【重复预警】" in messages[0]["content"]:
            return ChatResult(
                content="换个具体场景：上次线上回滚，日志里查不到是谁放进来的那行。",
                usage=Usage(10, 20),
                latency_ms=5,
                model="fake",
            )
        return ChatResult(
            content="代码是你合的，锅就是你的，这没什么好商量。",
            usage=Usage(10, 20),
            latency_ms=5,
            model="fake",
        )


def test_repeated_sentence_gets_regenerated(store: Store) -> None:
    """复读由代码判、由代码要求重说 —— 模型看不见自己的完整历史。"""
    did = make_discussion(store, max_turns=12)
    llm = RepeatThenFreshLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=12)).run(did)

    texts = [u["content"] for u in store.get_utterances(did)]
    dup = [t for t in texts if t.startswith("代码是你合的")]
    assert len(dup) == 1, f"同一句话被写进了 transcript {len(dup)} 次"
    assert any("换个具体场景" in t for t in texts), "重说之后的新内容没有被采用"
    assert any("【重复预警】" in p for p in llm.speaker_prompts), "第二轮没有带上重复预警"

    # 重说不能吃掉这一轮：轮次数与实际发言行数仍要对得上
    assert len(texts) == store.get_discussion(did)["turn_count"]


def test_intent_comes_from_content_not_schedule(store: Store) -> None:
    """意图必须由说出来的话决定。

    调度器的 intent_hint 只知道「谁被点名」「谁连说了两句」，实测一场 22 句里
    18 句都落到「补充」，而界面标着「交锋」—— 同屏两套口径。
    """
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(converge_after=99), EventBus(), config=EngineConfig(max_turns=12)).run(did)

    intents = [u["intent"] for u in store.get_utterances(did)]
    assert intents[0] == "开场"
    # FakeLLM 每句都以「我不同意」开头 → 必须被判成反驳，而不是一律「补充」
    assert intents.count("反驳") >= len(intents) - 2, intents


# ------------------------------------------------------------------ 文本质量


class RepeatThenFreshLLM(FakeLLM):
    """模拟实测到的复读：前几次都吐同一句，被重复预警后才换一句。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.speaker_prompts: list[str] = []

    def chat(self, messages, *, temperature=0.9, max_tokens=800, json_mode=False):
        if not json_mode:
            self.speaker_prompts.append(messages[0]["content"])
        if json_mode:
            return super().chat(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
        self.calls += 1
        if "【重复预警】" in messages[0]["content"]:
            return ChatResult(
                content="换个具体场景：上次线上回滚，日志里查不到是谁放进来的那行。",
                usage=Usage(10, 20),
                latency_ms=5,
                model="fake",
            )
        return ChatResult(
            content="代码是你合的，锅就是你的，这没什么好商量。",
            usage=Usage(10, 20),
            latency_ms=5,
            model="fake",
        )


def test_repeated_sentence_gets_regenerated(store: Store) -> None:
    """复读由代码判、由代码要求重说 —— 模型看不见自己的完整历史。"""
    did = make_discussion(store, max_turns=12)
    llm = RepeatThenFreshLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=12)).run(did)

    texts = [u["content"] for u in store.get_utterances(did)]
    dup = [t for t in texts if t.startswith("代码是你合的")]
    assert len(dup) == 1, f"同一句话被写进了 transcript {len(dup)} 次"
    assert any("换个具体场景" in t for t in texts), "重说之后的新内容没有被采用"
    assert any("【重复预警】" in p for p in llm.speaker_prompts), "第二轮没有带上重复预警"

    # 重说不能吃掉这一轮：轮次数与实际发言行数仍要对得上
    assert len(texts) == store.get_discussion(did)["turn_count"]


def test_intent_comes_from_content_not_schedule(store: Store) -> None:
    """意图必须由说出来的话决定。

    调度器的 intent_hint 只知道「谁被点名」「谁连说了两句」，实测一场 22 句里
    18 句都落到「补充」，而界面标着「交锋」—— 同屏两套口径。
    """
    did = make_discussion(store, max_turns=12)
    Engine(store, FakeLLM(converge_after=99), EventBus(), config=EngineConfig(max_turns=12)).run(did)

    intents = [u["intent"] for u in store.get_utterances(did)]
    assert intents[0] == "开场"
    # FakeLLM 每句都以「我不同意」开头 → 必须被判成反驳，而不是一律「补充」
    assert intents.count("反驳") >= len(intents) - 2, intents


def test_clash_phase_pushes_back_when_everyone_just_agrees(store: Store) -> None:
    """交锋阶段连着几句都在互相补充时，必须要求这一轮给个反例。

    实测的一场：CLASH 里 13 句全被判成「补充」，每个人都往方案上加条款，
    读起来像评审会。判据是**内容**（前面几轮有没有反驳/追问），所以也由代码判。
    """
    did = make_discussion(store, max_turns=12)
    llm = RepeatThenFreshLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=12)).run(did)

    joined = "\n".join(llm.speaker_prompts)
    assert "【这一轮的要求】" in joined, "整场都没有触发反例要求"


def test_clash_phase_pushes_back_when_everyone_just_agrees(store: Store) -> None:
    """交锋阶段连着几句都在互相补充时，必须要求这一轮给个反例。

    实测的一场：CLASH 里 13 句全被判成「补充」，每个人都往方案上加条款，
    读起来像评审会。判据是**内容**（前面几轮有没有反驳/追问），所以也由代码判。
    """
    did = make_discussion(store, max_turns=12)
    llm = RepeatThenFreshLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=12)).run(did)

    joined = "\n".join(llm.speaker_prompts)
    assert "【这一轮的要求】" in joined, "整场都没有触发反例要求"


class LongThenShortLLM(FakeLLM):
    """模拟"不听字数要求"的模型：不说它太长就一直写长句。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.speaker_prompts: list[str] = []

    def chat(self, messages, *, temperature=0.9, max_tokens=800, json_mode=False):
        if json_mode:
            return super().chat(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
        prompt = messages[0]["content"]
        self.speaker_prompts.append(prompt)
        self.calls += 1
        if "【太长了】" in prompt:
            body = "短一句：这事在预算紧的时候第一个被砍。"
        else:
            # 70 字以上，必然越过上限
            body = "我们上线那套流程之后每个团队都配了专门的人盯着，结果季度复盘时发现" \
                   "真正干活的还是原来那几个人，多出来的人只在填表，所以我认为这套做法在" \
                   "规模没到之前不该硬上。"
        return ChatResult(content=body, usage=Usage(10, 20), latency_ms=5, model="fake")


def test_overlong_speech_is_regenerated(store: Store) -> None:
    """字数由代码量，不由 prompt 自觉。

    prompt 里写着「不超过 60 字」，实测 7 场 168 句里有 47 句越界（最长 82 字），
    模型对量化约束基本不当回事。这条钉住"超了就重说一次"确实发生。
    """
    did = make_discussion(store, max_turns=10)
    llm = LongThenShortLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=10)).run(did)

    texts = [u["content"] for u in store.get_utterances(did)]
    # 开场/收尾来自主持人，本来就允许长；只看专家那几句
    expert_texts = texts[1:-1]
    assert expert_texts, "这一场没有专家发言，测试前提不成立"
    assert all(len(t) <= MAX_SPEECH_CHARS for t in expert_texts), [
        len(t) for t in expert_texts
    ]
    assert any("【太长了】" in p for p in llm.speaker_prompts), "没有触发过长重说"


class LongThenShortLLM(FakeLLM):
    """模拟"不听字数要求"的模型：不说它太长就一直写长句。"""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.speaker_prompts: list[str] = []

    def chat(self, messages, *, temperature=0.9, max_tokens=800, json_mode=False):
        if json_mode:
            return super().chat(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=True
            )
        prompt = messages[0]["content"]
        self.speaker_prompts.append(prompt)
        self.calls += 1
        if "【太长了】" in prompt:
            body = "短一句：这事在预算紧的时候第一个被砍。"
        else:
            # 70 字以上，必然越过上限
            body = "我们上线那套流程之后每个团队都配了专门的人盯着，结果季度复盘时发现" \
                   "真正干活的还是原来那几个人，多出来的人只在填表，所以我认为这套做法在" \
                   "规模没到之前不该硬上。"
        return ChatResult(content=body, usage=Usage(10, 20), latency_ms=5, model="fake")


def test_overlong_speech_is_regenerated(store: Store) -> None:
    """字数由代码量，不由 prompt 自觉。

    prompt 里写着「不超过 60 字」，实测 7 场 168 句里有 47 句越界（最长 82 字），
    模型对量化约束基本不当回事。这条钉住"超了就重说一次"确实发生。
    """
    did = make_discussion(store, max_turns=10)
    llm = LongThenShortLLM(converge_after=99)
    Engine(store, llm, EventBus(), config=EngineConfig(max_turns=10)).run(did)

    texts = [u["content"] for u in store.get_utterances(did)]
    # 开场/收尾来自主持人，本来就允许长；只看专家那几句
    expert_texts = texts[1:-1]
    assert expert_texts, "这一场没有专家发言，测试前提不成立"
    assert all(len(t) <= MAX_SPEECH_CHARS for t in expert_texts), [
        len(t) for t in expert_texts
    ]
    assert any("【太长了】" in p for p in llm.speaker_prompts), "没有触发过长重说"
