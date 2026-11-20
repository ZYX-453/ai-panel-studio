"""讨论引擎：把「选谁 → 说什么 → 记下来 → 更新共识 → 是否收尾」跑成一条真实的时间线。

三条边界必须守住，否则这个系统就退化成"前端放动画、后端念稿子"：

1. **谁说话由代码决定**。模型只拿到"该你说了"，不参与调度；
2. **结束由代码判定**，模型无权宣布讨论结束（它总爱提前总结）；
3. **降级也要留下痕迹**。模型连续失败时不是静默跳过，而是写明
   `ended_reason=degraded`，让读的人知道这场是被迫停的，不是自然结束的。

阶段推进与收敛判定都写成纯函数，便于单测 —— 它们是"什么时候该闭嘴"
这件事上唯二值得被钉死的东西。
"""

from __future__ import annotations

import logging
import math
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from . import lineup as lineup_mod
from .consensus import merge
from .content import classify_intent, is_repeat, most_similar
from .llm import LLMClient, LLMError, strip_code_fence
from .models import Consensus, Seat, Turn
from .personas import build_consensus_prompt, build_expert_prompt, build_host_prompt
from .scheduler import pick_next
from .store import PanelistRow, Store, now_iso

log = logging.getLogger("panel.engine")

PHASES = ("OPENING", "EXPLORE", "CLASH", "CLOSING")

#: 每 K 条发言抽取一次共识。太小会把成本推高且噪声大，太大则屏幕上长时间不动
CONSENSUS_EVERY = 3

#: 专家单句的长度上限（字符）。prompt 里写的是 60 字，这里留 5 字余量：
#: 模型对"字数"这种量化约束遵守得很差（实测 28% 越界，最长 82 字），
#: 所以只能事后量。超了让重说一次，而不是截断 —— 截断会切掉半句，
#: 读起来比长一点更糟。
MAX_SPEECH_CHARS = 65

#: 收敛所需的最小轮数。低于这个数就宣布"已达成共识"会显得敷衍，
#: 因为 EXPLORE 阶段本来就是用来暴露分歧的
CONVERGE_MIN_TURNS = 8

#: 连续这么多句失败才降级。给一次机会是因为网络抖动很常见，
#: 连着三次都失败就是上游真的挂了
MAX_CONSECUTIVE_FAILURES = 3

#: EXPLORE 占整场的比例，之后进入 CLASH
EXPLORE_RATIO = 0.4


# --------------------------------------------------------------------- 纯函数


def phase_for(turn_index: int, max_turns: int) -> str:
    """第 turn_index 句（从 1 开始）属于哪个阶段。

    收尾必须由主持人做，所以最后一轮固定 CLOSING；
    倒数第二轮就开始收尾的信号留给 should_close 决定，
    这里只管"按轮次看应该在哪"。
    """
    if turn_index <= 1:
        return "OPENING"
    if turn_index >= max_turns:
        return "CLOSING"
    explore_until = max(2, math.ceil(max_turns * EXPLORE_RATIO))
    return "EXPLORE" if turn_index <= explore_until else "CLASH"


def should_close(
    *,
    turn_count: int,
    max_turns: int,
    consensus: Consensus,
    settled_streak: int,
) -> tuple[bool, str]:
    """是否该收尾。返回 (是否, 原因)。

    两个条件都会触发收尾，但 ended_reason 不同 —— 这个区别必须保留：
    「谈完了」和「时间到了」对读的人是完全不同的两件事。

    `settled_streak` 的含义在 11-20 被放宽过，原因值得留在这里：
    D1 原本要求"连续两版**零**分歧"。但跑 13 场没有一场走到 ——
    查下来发现专家 prompt 明确写着「不要因为别人反对就改口」，
    只要专家守立场，最后那条核心分歧就一定还在，零分歧永远不可能出现。
    两条规则互斥，改不了 prompt 就只能改判据。

    所以现在判的是"分歧已经收窄到 1 条以内，且连续两版没有增加"：
    - 收窄到 1 条：多轮之后还悬着的只剩那个核心命题本身；
    - 不增加：没有再长出新的分歧。
    这是**放宽**，不是修复：它承认"还有一条没谈拢"也可以收尾，
    只要讨论确实已经聚焦。真正谈拢了（0 条）当然也满足。
    """
    if turn_count >= max_turns - 1:
        return True, "max_turns"
    if turn_count < CONVERGE_MIN_TURNS:
        return False, ""

    # 收敛不能让讨论跳过交锋阶段。
    # 放宽判据后实测出现过一场：10 句就 converged，阶段是
    # OPENING → EXPLORE → CLOSING，前端时间线上整段 CLASH 不见了 ——
    # 读起来就是"两人刚摆完立场，主持人就宣布谈拢了"。
    # 需求要求四个阶段都出现，所以必须已经走进 CLASH 才允许提前收尾。
    if phase_for(turn_count, max_turns) == "EXPLORE":
        return False, ""

    # 连续两版都已经收口，且已经有实质产出，才算真的收敛。
    # 分歧条数直接从 consensus 读，不靠调用方维护的计数器 ——
    # settled_streak 只回答"有没有连续两版不增加"，"现在剩几条"由数据本身回答，
    # 两件事分开，才不会出现"计数器说收口了、consensus 里还躺着 5 条"的错位。
    if (
        settled_streak >= 2
        and len(consensus.disputes) <= 1
        and consensus.version >= 2
        and len(consensus.points) >= 3
    ):
        return True, "converged"
    return False, ""


def extract_mentions(text: str, seats: tuple[Seat, ...] | list[Seat]) -> tuple[str, ...]:
    """从一句话里找出被点名的人。

    只认完整姓名，不做模糊匹配：真实讨论里点名就是叫全名，
    用模糊匹配会把"许巍"和"许"这类误判进来，反而破坏调度。
    """
    hits = [s.id for s in seats if s.role == "expert" and s.name and s.name in text]
    return tuple(dict.fromkeys(hits))


def to_seats(rows: list[PanelistRow]) -> tuple[Seat, ...]:
    seats: list[Seat] = []
    for r in rows:
        focus = r.persona.get("focus") or []
        if isinstance(focus, str):
            focus = [focus]
        seats.append(
            Seat(
                id=r.id,
                role=r.role,
                seat=r.seat,
                name=r.name,
                stance=r.stance,
                catchphrase=str(r.persona.get("catchphrase") or ""),
                focus=tuple(str(f) for f in focus),
                # 兼容历史数据：改名之前入库的阵容，persona_json 里只有 attack。
                # 迁移只在这一个边界上做一次，往下就只剩 target 一套口径，
                # 不允许 prompt 层再各自去猜该读哪个键。
                target=str(r.persona.get("target") or r.persona.get("attack") or ""),
                title=r.title,
            )
        )
    return tuple(seats)


# ------------------------------------------------------------------- 事件总线


class EventBus:
    """进程内广播。直播用总线，回放走数据库 —— 两条路径读的是同一批数据。

    刻意不引入 Redis 之类的外部依赖：这是本地单机应用，
    多一个中间件只会多一个演示当天可能挂掉的东西。
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[Any]] = {}
        self._lock = threading.Lock()

    def subscribe(self, did: str):
        import queue

        q: queue.Queue = queue.Queue(maxsize=512)
        with self._lock:
            self._subs.setdefault(did, []).append(q)
        return q

    def unsubscribe(self, did: str, q) -> None:
        with self._lock:
            subs = self._subs.get(did, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(did, None)

    def publish(self, did: str, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs.get(did, ()))
        for q in subs:
            try:
                q.put_nowait(event)
            except Exception:
                # 订阅者太慢就丢给它的队列满，不能阻塞整个讨论
                log.warning("[%s] 订阅队列已满，丢弃 seq=%s", did, event.get("seq"))


# ------------------------------------------------------------------- 讨论引擎


@dataclass(frozen=True, slots=True)
class EngineConfig:
    max_turns: int = 24
    consensus_every: int = CONSENSUS_EVERY
    temperature_expert: float = 0.95
    temperature_host: float = 0.7
    temperature_consensus: float = 0.2
    seed: int = 0


class Engine:
    def __init__(
        self,
        store: Store,
        client: LLMClient,
        bus: EventBus,
        *,
        config: EngineConfig | None = None,
    ) -> None:
        self.store = store
        self.client = client
        self.bus = bus
        self.config = config or EngineConfig()

    # ------------------------------------------------------------ 对外入口

    def start(self, did: str) -> bool:
        """把讨论从 AWAITING_CONFIRM 推到 LIVE 并起后台线程。

        返回 False 表示状态不允许（已在跑或已结束），由 API 层转 409。
        """
        d = self.store.get_discussion(did)
        if not d or d["status"] != "AWAITING_CONFIRM":
            return False
        self.store.set_status(did, "LIVE", started_at=now_iso())
        t = threading.Thread(target=self._run_guarded, args=(did,), daemon=True)
        t.start()
        return True

    def _run_guarded(self, did: str) -> None:
        try:
            self.run(did)
        except Exception:
            # 引擎崩溃必须留下可查的痕迹，并且把状态推到终态，
            # 否则首页会永远显示"进行中"——那是最容易露馅的假象
            log.exception("[%s] 引擎异常退出", did)
            try:
                self._emit(did, "error", {"code": "ENGINE_CRASH", "message": "引擎异常，已降级结束"})
                self._finish(did, "degraded", summary=None)
            except Exception:
                log.exception("[%s] 收尾也失败了", did)

    # ------------------------------------------------------------ 主循环

    def run(self, did: str) -> None:
        d = self.store.get_discussion(did)
        if not d:
            return
        topic: str = d["topic"]
        max_turns: int = int(d["max_turns"])
        seats = to_seats(self.store.get_panel(did))
        hosts = [s for s in seats if s.role == "host"]
        if not hosts:
            self._finish(did, "degraded", summary="阵容缺少主持人，无法开场。")
            return
        host = hosts[0]

        turns: list[Turn] = []
        consensus = Consensus(version=0)
        settled_streak = 0
        consecutive_failures = 0
        # 初值刻意留空而不是 "OPENING"：留空才会在第一轮发出 phase=OPENING 事件。
        # 若初值就是 OPENING，比较结果为"没变化"，前端拿到的第一个 phase 事件
        # 会是 EXPLORE —— 于是开局那一段顶部没有阶段指示，而且与 seed 投影不一致
        # （投影从 None 起算，第一条 phase 就是 OPENING）。
        phase = ""

        while True:
            turn_index = len(turns) + 1
            # 轮次触发的阶段（收尾兜底），但仍允许 should_close 提前进入 CLOSING
            natural_phase = phase_for(turn_index, max_turns)
            # 只有 CLOSING 需要"粘住"：should_close 可能提前把 phase 设成 CLOSING，
            # 但此时 turn_index 还没到 max_turns，按轮次算会退回 CLASH。
            # OPENING 恰恰相反 —— 它只是第一轮，绝不能粘住。
            # 早期版本在这里多写了一个 elif phase == "OPENING" 分支，
            # 结果 phase 永远是 OPENING，整场 24 轮全是主持人独白，专家一句上不来。
            if phase == "CLOSING":
                natural_phase = "CLOSING"

            if natural_phase != phase:
                phase = natural_phase
                self._emit(did, "phase", {"phase": phase})

            decision = pick_next(seats, turns, phase, seed=self.config.seed)
            speaker = next((s for s in seats if s.id == decision.seat_id), None)
            if speaker is None:
                self._finish(did, "degraded", summary="调度返回了不存在的人。")
                return

            self._emit_state(did, speaker, "准备发言")

            try:
                content = self._speak(
                    speaker=speaker,
                    phase=phase,
                    topic=topic,
                    turns=turns,
                    seats=seats,
                    decision_respond_to=decision.respond_to_seq,
                )
                consecutive_failures = 0
            except LLMError as exc:
                consecutive_failures += 1
                log.warning("[%s] seq=%d 发言失败(%d): %s", did, turn_index, consecutive_failures, exc)
                self._emit(
                    did,
                    "error",
                    {
                        "panelist_id": speaker.id,
                        "code": "LLM_RETRY_FAILED" if exc.retryable else "LLM_NOT_CONFIGURED",
                        "message": "本句生成失败，已跳过",
                    },
                )
                self._emit_state(did, speaker, "待机")
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    self._finish(did, "degraded", summary=self._degraded_summary(consensus))
                    return
                # 跳过后仍然推进轮次，否则会卡在同一个人身上死循环
                turns.append(Turn(seq=turn_index, speaker_id=speaker.id, text="", intent="跳过"))
                if len(turns) >= max_turns:
                    self._finish(did, "max_turns", summary=self._degraded_summary(consensus))
                    return
                continue

            self._emit_state(did, speaker, "发言中")
            reply_to_id = self._seq_to_uid(did, decision.respond_to_seq)
            # 意图由**说出来的是什么**决定，不用调度器的 intent_hint。
            # hint 是排班表猜的：它只知道"点了谁的名字"和"谁连说了两句"，
            # 剩下全落到「补充」。实测 22 句里 18 句都是「补充」，
            # 而界面上还标着「交锋」，两套口径自己打自己。
            intent = classify_intent(
                content,
                phase=phase,
                first_time=not any(t.speaker_id == speaker.id and t.text for t in turns),
            )
            uid = self._record_utterance(
                did=did,
                speaker=speaker,
                seq=turn_index,
                phase=phase,
                intent=intent,
                content=content,
                reply_to_id=reply_to_id,
            )
            self._emit_state(did, speaker, "待机")

            turn = Turn(
                seq=turn_index,
                speaker_id=speaker.id,
                text=content,
                intent=intent,
                mentions=extract_mentions(content, seats),
            )
            turns.append(turn)

            # ---- 收尾预判
            # 这里只回答"这一句之后是不是就该收了"，用来决定要不要补抽一次共识。
            # 真正的收尾判定放在抽取之后重做（见下面），否则会拿旧状态定生死。
            will_close, _ = should_close(
                turn_count=len(turns),
                max_turns=max_turns,
                consensus=consensus,
                settled_streak=settled_streak,
            )

            # ---- 共识抽取
            # 常规节奏：每 N 句一次，且已经进入陈述/交锋。
            # 另外"就要收尾了"也必须抽一次 —— 收尾总结只读 consensus_state，
            # 它落后于正文，总结就会漏掉最后几句。实测不加这一条时，24 句场
            # 一律停在"基于前 21 句"：最后 3 句（往往正是最关键的那次反驳或反例）
            # 根本没进共识，收尾总结却当成已经读过了。
            # CLOSING 阶段不抽：那时每句都会让 should_close 为真，会白烧调用。
            due_by_rhythm = (
                len(turns) % self.config.consensus_every == 0
                and phase in ("EXPLORE", "CLASH")
            )
            due_to_close = will_close and phase != "CLOSING"
            if due_by_rhythm or due_to_close:
                prev_disputes = len(consensus.disputes)
                new_consensus = self._extract_consensus(did, topic, turns, consensus, seats)
                if new_consensus is not None:
                    consensus = new_consensus
                    self._emit(
                        did,
                        "consensus",
                        {
                            "version": consensus.version,
                            "points": list(consensus.points),
                            "disputes": list(consensus.disputes),
                            "based_on_seq": consensus.based_on_seq,
                        },
                    )
                    # 「已经收口」= 分歧只剩 1 条以内，且这一版没有比上一版更多。
                    # 注意 prev_disputes 在首次抽取时是 0，而这一版至少 1 条，
                    # 所以第一版不会误判成收口（1 <= 0 为假），这是故意的。
                    n_disputes = len(consensus.disputes)
                    if n_disputes <= 1 and n_disputes <= prev_disputes:
                        settled_streak += 1
                    else:
                        settled_streak = 0

            # ---- 收尾判定
            # 用抽取之后的状态重判：补抽如果让分歧重新变多，这一轮就不该收，
            # 否则会出现"ended_reason 写着 converged、consensus 里躺着 3 条分歧"。
            close, reason = should_close(
                turn_count=len(turns),
                max_turns=max_turns,
                consensus=consensus,
                settled_streak=settled_streak,
            )
            if close and phase != "CLOSING":
                phase = "CLOSING"
                self._emit(did, "phase", {"phase": phase})
                continue
            if phase == "CLOSING":
                summary = self._final_summary(host, topic, consensus)
                self._finish(did, reason or "max_turns", summary=summary)
                return

    # ------------------------------------------------------------ 单句生成

    def _speak(
        self,
        *,
        speaker: Seat,
        phase: str,
        topic: str,
        turns: list[Turn],
        seats: tuple[Seat, ...],
        decision_respond_to: int | None,
    ) -> str:
        # 交锋阶段连续几句都在互相补充、没人反对时，要求这一轮给个具体反例。
        # 不加这条，实测会出现"交锋"阶段 18 句全是「补充」的场面：
        # 每个人都往方案上加条款，读起来像在开评审会，不是圆桌。
        recent = [t.intent for t in turns[-3:]]
        needs_push = (
            phase == "CLASH"
            and len(recent) == 3
            and not ({"反驳", "追问"} & set(recent))
        )
        text = self._generate(
            speaker=speaker,
            phase=phase,
            topic=topic,
            turns=turns,
            seats=seats,
            respond_to=decision_respond_to,
            push_back=needs_push,
        )
        # 复读检测：与场上任何一句几乎逐字相同就重说一次。
        # 这条**不由模型自查**（它看不见自己的完整历史），而是由代码判：
        # 实测同一专家把「代码是你合的，锅就是你的」隔十四句又说了一遍。
        repeated = is_repeat(text, [t.text for t in turns])
        # 长度同样是代码管，不是 prompt 管。prompt 里写着「不超过 60 字」，
        # 实测 7 场 168 句里有 47 句超了（28%），最长 82 字 ——
        # 模型对"字数"这种量化约束基本不当回事，只能事后量、量到了让重说。
        # 主持人的开场与收尾不在此列：收尾本来就要 4-6 句。
        too_long = phase not in ("OPENING", "CLOSING") and len(text) > MAX_SPEECH_CHARS
        if too_long:
            log.info("%s 这句 %d 字，超过 %d 字上限，重说一次", speaker.name, len(text), MAX_SPEECH_CHARS)

        if repeated or too_long:
            if repeated:
                common, seq = most_similar(text, turns)
                log.info(
                    "检测到复读：%s 这句与第%s句连续 %d 字相同，重说一次",
                    speaker.name,
                    seq,
                    common,
                )
            retried = self._generate(
                speaker=speaker,
                phase=phase,
                topic=topic,
                turns=turns,
                seats=seats,
                respond_to=decision_respond_to,
                avoid_repeat=repeated,
                too_long=too_long,
                push_back=needs_push,
            )
            # 重说之后仍然撞车就保留新的那句：宁可内容一般，
            # 也不要让这一轮空着 —— 空轮会让轮次与实际发言数对不上。
            if retried:
                # 长度超标时取较短的那句（重说不保证更短）；只是复读时取重说的那句，
                # 因为要的是"换一种说法"，长度不是重点。
                text = min((text, retried), key=len) if too_long else retried

        # 重说之后还是超长，只能截断。常量写 65 就不能发出 66 字的句子 ——
        # 否则 check_ui 的门禁（卡 65）和引擎（放过任意长度）各有一套口径，
        # 而"同一语义两处各写一遍"是这个项目复发率最高的缺陷。
        # 实测确实有一句 66 字溜过了门禁，就是这条路。
        if too_long and len(text) > MAX_SPEECH_CHARS:
            log.info(
                "%s 重说后仍有 %d 字，截到 %d 字",
                speaker.name,
                len(text),
                MAX_SPEECH_CHARS,
            )
            text = text[: MAX_SPEECH_CHARS - 1].rstrip("，。；、,; ") + "…"

        return text

    def _generate(
        self,
        *,
        speaker: Seat,
        phase: str,
        topic: str,
        turns: list[Turn],
        seats: tuple[Seat, ...],
        respond_to: int | None,
        avoid_repeat: bool = False,
        too_long: bool = False,
        push_back: bool = False,
    ) -> str:
        if phase in ("OPENING", "CLOSING"):
            prompt = build_host_prompt(
                speaker,
                topic=topic,
                transcript=turns,
                phase=phase,
                seats=seats,
                avoid_repeat=avoid_repeat,
            )
            temp = self.config.temperature_host
        else:
            prompt = build_expert_prompt(
                speaker,
                topic=topic,
                transcript=turns,
                seats=seats,
                respond_to_seq=respond_to,
                avoid_repeat=avoid_repeat,
                too_long=too_long,
                push_back=push_back,
            )
            temp = self.config.temperature_expert

        res = self.client.chat(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "现在轮到你发言。"},
            ],
            temperature=temp,
        )
        text = strip_code_fence(res.content).strip()
        # 模型偶尔会把"发言"写成一段带标签的东西，这里只做最基本的清洗：
        # 去掉"某某："前缀，否则 transscript 里会出现"周敏：……"这种重复署名
        for prefix in (f"{speaker.name}：", f"{speaker.name}:", f"【{speaker.name}】"):
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
        if not text:
            raise LLMError("模型返回空内容", retryable=True)
        return text

    # ------------------------------------------------------------ 共识抽取

    def _extract_consensus(
        self,
        did: str,
        topic: str,
        turns: list[Turn],
        prev: Consensus,
        seats: tuple[Seat, ...],
    ) -> Consensus | None:
        # 分区给：模型要能**逐字**引用某条旧分歧才能把它写进 resolved，
        # 混成一坨它只能概括，概括出来的东西匹配不上（见 consensus.merge 的说明）。
        prev_points = "\n".join(f"- {p}" for p in prev.points) or "（无）"
        prev_disputes = "\n".join(f"- {d}" for d in prev.disputes) or "（无）"
        prompt = build_consensus_prompt(
            topic=topic,
            transcript=turns,
            seats=seats,
            previous=f"【已达成共识】\n{prev_points}\n\n【仍未解决的分歧】\n{prev_disputes}",
        )
        try:
            res = self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "现在抽取共识与分歧。"},
                ],
                temperature=self.config.temperature_consensus,
                max_tokens=900,
                json_mode=True,
            )
        except LLMError as exc:
            # 抽取失败不算致命：屏幕上少更新一次版本，讨论继续
            log.warning("[%s] 共识抽取失败: %s", did, exc)
            self._emit(did, "error", {"code": "CONSENSUS_FAILED", "message": "本轮共识未更新"})
            return None

        payload = _loads_lenient(res.content)
        if not isinstance(payload, dict):
            return None
        points = [str(x).strip() for x in payload.get("points") or [] if str(x).strip()]
        disputes = [str(x).strip() for x in payload.get("disputes") or [] if str(x).strip()]
        resolved = [str(x).strip() for x in payload.get("resolved") or [] if str(x).strip()]
        based_on = int(payload.get("based_on_seq") or turns[-1].seq)

        merged = merge(prev, points, disputes, based_on_seq=based_on, resolved=resolved)
        if resolved:
            log.info("[%s] 模型声明已解决 %d 条分歧", did, len(resolved))
        if merged.version != prev.version:
            self.store.save_consensus(
                did=did,
                version=merged.version,
                points=list(merged.points),
                disputes=list(merged.disputes),
                based_on_seq=merged.based_on_seq,
            )
        return merged

    # ------------------------------------------------------------ 收尾

    def _final_summary(self, host: Seat, topic: str, consensus: Consensus) -> str:
        """主持人总结只注入 consensus_state，不重读 transcript。

        这是 D3 的落地：页面上的共识面板与最终总结同源，
        所以不可能出现"面板说已达成、总结说仍有分歧"。
        """
        if consensus.version == 0:
            return (
                f"关于「{topic}」，这场讨论没有形成可记录的共识。"
                "观点分散在各自的立场上，没有收敛到可以行动的结论。"
            )
        from .personas import build_summary_prompt

        prompt = build_summary_prompt(host, topic=topic, consensus=consensus)
        try:
            res = self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请给出收尾总结。"},
                ],
                temperature=self.config.temperature_host,
                max_tokens=600,
            )
            text = res.content.strip()
            if text:
                return text
        except LLMError as exc:
            log.warning("收尾总结生成失败，改用结构化兜底: %s", exc)

        # 兜底也要说人话，而不是把 JSON 直接甩到屏幕上
        pts = "；".join(consensus.points[:3]) or "（无）"
        dsp = "；".join(consensus.disputes[:2]) or "（无）"
        return (
            f"这场讨论在第 {consensus.based_on_seq} 句时形成了 {len(consensus.points)} 条共识：{pts}。"
            f"仍未解决的分歧是：{dsp}。"
        )

    def _degraded_summary(self, consensus: Consensus) -> str:
        if consensus.version == 0:
            return "讨论因模型调用连续失败而提前结束，未形成可记录的共识。"
        pts = "；".join(consensus.points[:3]) or "（无）"
        return (
            f"讨论因模型调用连续失败而提前结束。"
            f"在此之前已记录 {len(consensus.points)} 条共识：{pts}。"
            f"剩余分歧：{'；'.join(consensus.disputes[:2]) or '（无）'}。"
        )

    def _finish(
        self,
        did: str,
        reason: str,
        *,
        summary: str | None,
    ) -> None:
        self.store.set_status(
            did,
            "FINISHED",
            ended_reason=reason,
            ended_at=now_iso(),
            final_summary=summary,
        )
        # ended 只表达"结束了、为什么"，不重复携带共识版本 ——
        # 版本已经由 consensus 事件发过，两处都带就会有两套可读的来源
        self._emit(
            did,
            "ended",
            {"reason": reason, "turns": self.store.get_discussion(did)["turn_count"]},
        )

    # ------------------------------------------------------------ 写入

    def _record_utterance(
        self,
        *,
        did: str,
        speaker: Seat,
        seq: int,
        phase: str,
        intent: str,
        content: str,
        reply_to_id: str | None,
    ) -> str:
        uid = f"u_{did}_{seq:03d}_{uuid.uuid4().hex[:6]}"
        created_at = self.store.append_utterance(
            did=did,
            uid=uid,
            panelist_id=speaker.id,
            seq=seq,
            phase=phase,
            intent=intent,
            content=content,
            reply_to_id=reply_to_id,
        )
        self._emit(
            did,
            "utterance",
            {
                # 字段名刻意用 utterance_seq 而不是 seq：
                # 事件本身也有一个 seq（SSE 的 id），两者不相等（一条发言含 3 个事件）。
                # 用同一个名字迟早会被谁当成另一个用 —— 这正是"同屏两套口径"的起点。
                "utterance_seq": seq,
                "utterance_id": uid,
                "panelist_id": speaker.id,
                "name": speaker.name,
                "title": speaker.title,
                "content": content,
                "intent": intent,
                "reply_to_utterance_seq": self._seq_of_uid(did, reply_to_id),
                "phase": phase,
                "created_at": created_at,
            },
        )
        return uid

    def _emit_state(self, did: str, speaker: Seat, state: str) -> None:
        """内部状态只走这一条出口。

        单独抽出来是为了让"内部事件不得进 transcript"这件事有个物理边界：
        这个函数只会写 panelist_state，不可能顺手写进 utterance。
        """
        self._emit(
            did,
            "panelist_state",
            {"panelist_id": speaker.id, "name": speaker.name, "state": state},
        )

    def _emit(self, did: str, etype: str, payload: dict[str, Any]) -> int:
        seq = self.store.max_seq(did) + 1
        self.store.append_event(did, seq, etype, payload)
        event = {"seq": seq, "type": etype, "payload": payload, "created_at": now_iso()}
        self.bus.publish(did, event)
        return seq

    def _seq_to_uid(self, did: str, seq: int | None) -> str | None:
        if seq is None:
            return None
        return self.store.utterance_id_by_seq(did, seq)

    def _seq_of_uid(self, did: str, uid: str | None) -> int | None:
        if not uid:
            return None
        row = self.store.conn.execute(
            "SELECT seq FROM utterance WHERE id = ?", (uid,)
        ).fetchone()
        return int(row["seq"]) if row else None


def _loads_lenient(text: str) -> Any:
    import json
    import re

    s = strip_code_fence(text)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
