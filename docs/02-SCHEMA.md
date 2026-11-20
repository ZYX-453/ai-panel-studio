# 数据模型与状态机

> 阶段：SDD-2 ｜ 数据库：SQLite（需求硬性规定）

---

## 1. 设计原则

1. **一切以 `discussion_id` 为隔离边界**。多场讨论并行时，任何查询都必须带这个条件，杜绝跨场串流。
2. **内部事件不入 transcript**。"举手/让位"只进 `event` 表，`utterance` 表结构上就没有这类字段。
3. **事件表是唯一的重放源**。页面刷新、断线重连、结束后回看，都靠 `event.seq` 单调递增重放。
4. **共识状态带版本号**。每次抽取产生新版本，页面显示 `v3` 这种编号，评审一眼能看出"是实时更新的"。

---

## 2. ER 图

```mermaid
erDiagram
    DISCUSSION ||--o{ PANELIST : "阵容"
    DISCUSSION ||--o{ UTTERANCE : "发言"
    DISCUSSION ||--|| CONSENSUS_STATE : "当前共识"
    DISCUSSION ||--o{ CONSENSUS_SNAPSHOT : "历史版本"
    DISCUSSION ||--o{ EVENT : "事件流"
    PANELIST ||--o{ UTTERANCE : "说了"
    UTTERANCE ||--o| UTTERANCE : "回应"

    DISCUSSION {
        text id PK
        text topic
        int  expert_count
        text status "GENERATING/AWAITING_CONFIRM/LIVE/FINISHED/FAILED"
        text ended_reason "converged/max_turns/degraded"
        int  turn_count
        int  max_turns
        text config_json "K值、权重等可调参数"
        text created_at
        text started_at
        text ended_at
    }

    PANELIST {
        text id PK
        text discussion_id FK
        text role "host/expert"
        int  seat
        text name
        text title "职业/Title"
        text stance "立场一句话"
        text color_token "专属色 token"
        text persona_json "口头禅/攻击面/关注点"
    }

    UTTERANCE {
        text id PK
        text discussion_id FK
        text panelist_id FK
        int  seq
        text phase "OPENING/EXPLORE/CLASH/CLOSING"
        text intent "补充/反驳/追问/抛论点"
        text content
        text reply_to_id FK
        text created_at
    }

    CONSENSUS_STATE {
        text discussion_id PK
        int  version
        text points_json "已达成的共识"
        text disputes_json "仍存在的分歧"
        int  based_on_seq
        text updated_at
    }

    CONSENSUS_SNAPSHOT {
        text id PK
        text discussion_id FK
        int  version
        text points_json
        text disputes_json
        int  based_on_seq
        text created_at
    }

    EVENT {
        int  id PK
        text discussion_id FK
        int  seq "每场独立单调递增"
        text type "phase/panelist_state/utterance/consensus/ended/error"
        text payload_json
        text created_at
    }
```

---

## 3. 索引与约束

| 约束 | 说明 |
|---|---|
| `UNIQUE(discussion_id, seq)` on `EVENT` | 保证事件流可重放、可去重 |
| `UNIQUE(discussion_id, seat)` on `PANELIST` | 座位唯一，颜色按座位分配 |
| `INDEX(discussion_id, created_at)` on `UTTERANCE` | 按场次取 transcript |
| `CHECK(status IN (...))` | 状态机非法值入库即报错 |
| `CONSENSUS_STATE` 以 `discussion_id` 作主键 | 天然保证"一场只有一个当前版本" |

---

## 4. 讨论状态机

```mermaid
stateDiagram-v2
    [*] --> GENERATING
    GENERATING --> AWAITING_CONFIRM : 阵容生成成功
    GENERATING --> FAILED : 连续失败
    AWAITING_CONFIRM --> GENERATING : 重掷阵容
    AWAITING_CONFIRM --> LIVE : 用户确认
    LIVE --> FINISHED : converged / max_turns
    LIVE --> FAILED : degraded
    FINISHED --> [*]
    FAILED --> [*]
```

**关键点**：`AWAITING_CONFIRM` 是独立状态，不是"LIVE 的一个开关"。因为用户可能在确认前反复重掷阵容，这段不能开始烧模型。

---

## 5. 单轮发言的时序（调度核心）

```mermaid
sequenceDiagram
    participant U as 浏览器
    participant A as FastAPI
    participant S as 调度器(纯函数)
    participant L as 大模型

    U->>A: GET /stream?from_seq=N (SSE)
    loop 每一轮
        A->>S: pick_next(state)
        S-->>A: panelist_id + intent + 理由
        A-->>U: event: panelist_state(准备发言)
        A->>L: 人设 + 最近窗口 + 指定intent
        L-->>A: 1-2 句发言
        A-->>U: event: utterance
        A-->>U: event: panelist_state(待机)
        alt 每 K 条发言
            A->>L: 结构化抽取共识/分歧
            L-->>A: points / disputes
            A-->>U: event: consensus(v+1)
        end
        A->>S: should_end(state)
        S-->>A: false / converged
    end
    A-->>U: event: ended(reason)
```

注意图中顺序：`panelist_state(准备发言)` **先于** `utterance` 发出，这样侧边小窗的"准备发言 → 发言中"是真的跟时间走的，而不是事后补的动画。

---

## 6. 回放机制

- `event` 表按 `discussion_id` 存全量事件，`seq` 从 0 递增。
- 前端重连时带 `from_seq`，服务端从该位置续推；`from_seq=0` 即完整回放。
- 回放与直播走**同一套事件类型**，前端只有一条渲染管线。这样"回放"不需要额外开发，且不会出现"直播能看、回放丢东西"。
