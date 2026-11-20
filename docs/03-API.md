# API 契约

> 阶段：SDD-3 ｜ 风格：HTTP + SSE（服务端推送）｜ 端口：8000（后端）/ 5173（前端开发）

## 0. 约定

- 所有响应 `application/json`，统一信封：成功 `{"ok": true, "data": ...}`，失败 `{"ok": false, "error": {"code": "...", "message": "..."}}`。
- 时间统一 ISO 8601 本地时间字符串。
- **API Key 只在后端从环境变量读**（`PANEL_LLM_KEY`），任何响应体、任何日志都不得回显。
- 每个请求带 `X-Request-Id`（前端生成），后端日志记录，便于和 `AGENT_LOG.md` 对账。

---

## 1. 端点清单

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/health` | 存活 + 模型连通性 |
| `GET` | `/api/discussions` | 讨论列表（进行中 / 已结束） |
| `POST` | `/api/discussions` | 创建讨论 → 生成阵容 |
| `POST` | `/api/discussions/{id}/regenerate` | 重掷阵容（仅在 `AWAITING_CONFIRM`） |
| `POST` | `/api/discussions/{id}/start` | 确认阵容，开始讨论 |
| `GET` | `/api/discussions/{id}` | 全量快照（阵容 + transcript + 共识 + 状态） |
| `GET` | `/api/discussions/{id}/stream` | **SSE 事件流** |
| `GET` | `/api/discussions/{id}/export` | 导出 markdown transcript（选做） |

---

## 2. 关键端点的请求 / 响应

### 2.1 创建讨论

```http
POST /api/discussions
Content-Type: application/json

{"topic": "远程办公是否应该成为公司默认工作方式", "expert_count": 4}
```

```json
{
  "ok": true,
  "data": {
    "id": "d_7f3a91",
    "status": "AWAITING_CONFIRM",
    "topic": "远程办公是否应该成为公司默认工作方式",
    "panel": [
      {"id":"p1","role":"host","seat":0,"name":"陈立","title":"圆桌主持 · 商业评论人",
       "stance":"不预设立场，负责串场与追问","color":"#c2410c"},
      {"id":"p2","role":"expert","seat":1,"name":"周敏","title":"组织行为学教授",
       "stance":"远程办公会削弱弱连接，长期伤害创新","color":"#1d4ed8"},
      {"id":"p3","role":"expert","seat":2,"name":"许巍","title":"SaaS 公司 CTO",
       "stance":"远程是可运营的，问题在管理能力不在距离","color":"#15803d"},
      {"id":"p4","role":"expert","seat":3,"name":"林雪","title":"HR 咨询顾问",
       "stance":"一刀切都错，应按岗位分层","color":"#7e22ce"},
      {"id":"p5","role":"expert","seat":4,"name":"何远","title":"创业公司创始人",
       "stance":"早期公司必须线下，规模上去后才有得选","color":"#b45309"}
    ]
  }
}
```

**约束**：`expert_count` ∈ [2, 6]，默认 4。`panel` 长度 = `expert_count + 1`（含主持人）。立场两两不得同义——服务端做一次重复度校验，重复则重掷（最多 2 次）。

### 2.2 确认并开始

```http
POST /api/discussions/d_7f3a91/start
```

返回 `202 Accepted`，讨论进入 `LIVE`，事件流由后台任务生成。**不在这个请求里同步跑完讨论**，否则前端拿不到实时感。

### 2.3 SSE 事件流

```http
GET /api/discussions/d_7f3a91/stream?from_seq=0
Accept: text/event-stream
```

**用 GET 而不是 POST**：这里没有请求体（不限长中文议题），且需要浏览器原生断线重连语义。

事件格式（`id:` 即事件序号，浏览器断线重连会自动带 `Last-Event-ID`）：

```
id: 12
event: utterance
data: {"utterance_seq":7,"utterance_id":"u_d7f3a91_007_a1b2c3","panelist_id":"p3",
       "name":"许巍","title":"SaaS 公司 CTO",
       "content":"我不同意弱连接这个说法。远程之后我们跨部门的连接反而变多了，因为不用约会议室。",
       "intent":"反驳","reply_to_utterance_seq":9,"phase":"CLASH",
       "created_at":"2026-11-19T09:13:32"}
```

**字段名刻意区分 `seq` 与 `utterance_seq`**：SSE 的 `id:` 是**事件序号**（一条发言会产生 3 个事件：准备发言 / 发言 / 待机），
而 `utterance_seq` 是**第几句发言**。两者数值不相等。早期版本在 payload 里也写成 `seq`，
结果同一个名字在事件里指了两个东西 —— 这是"同屏两套口径"最典型的起点，已改掉。

**同一份 payload 有两个产生者**：运行时由 `backend/app/engine.py` 写出，样例数据由 `scripts/init_db.py` 投影出来。
两边的字段集必须逐字相同，由 `backend/tests/test_event_shape_parity.py` 守住 —— 这条测试是真的抓到过两处分叉（`seq` vs `utterance_seq`、多带 `consensus_version`）。

**事件类型全表**

| event | 何时发 | payload 关键字段 | 去向 |
|---|---|---|---|
| `phase` | 阶段切换 | `phase` | 顶部阶段指示 |
| `panelist_state` | 待机/准备发言/发言中 | `panelist_id, state` | **仅侧边小窗** |
| `utterance` | 一条发言完成 | `utterance_seq, utterance_id, panelist_id, content, intent, reply_to_utterance_seq, phase, created_at` | **仅 transcript** |
| `consensus` | 每 K 条抽取后 | `version, points[], disputes[]` | 共识面板 |
| `ended` | 结束 | `reason, turns` | 收尾提示 |
| `error` | 单次调用失败但可继续 | `panelist_id, code` | 面板角标（不打断） |
| `ping` | 每 15s | — | 保活，防代理断连 |

**序号从 1 开始**：`event.seq` 的取值是 `1..N`，与运行时引擎的 `next_seq` 一致。
投影脚本早期用 `enumerate(events)` 从 0 起，而 `/stream` 的查询是 `WHERE seq > from_seq` ——
`from_seq=0` 时第 0 帧（开场 `phase`）会被静默过滤，回看时永远缺「开场」阶段标识。
现在两侧都从 1 起，`scripts/init_db.py` 的自检与 `test_event_shape_parity.py` 双重守住。

**业务 `error` 上线时叫 `panel_error`**：`error` 是 EventSource 的保留事件名，
浏览器在连接断开时也会派发一个 `error`。两个语义挤进同一个监听器时，前端分不清
"连接断了"和"有一句没生成出来"，实测会让断线判断被业务事件污染。
所以序列化层做一次转义（`api.py` 的 `SSE_EVENT_ALIAS`），**数据库里仍然是 `error`**，
只有线上叫 `panel_error`，前端 `api.ts` 的 `WIRE_NAME` 再转回来。

**`ping` 是传输层注释，不入事件表**：它以 SSE 注释行（`: ping\n\n`）发出，不占 `seq`、不写库。
理由很实际——保活是"当下这条连接还活着"的信号，把它持久化只会污染重放：
回放一场昨天的讨论时，重放出一堆昨天的 ping 没有任何意义。
所以 `event.type` 的 CHECK 约束里刻意不包含 `ping`。

**硬性约束**：`panelist_state` 与 `utterance` 是两类事件，前端两条渲染路径。**"举手"只在 `panelist_state` 里以 `state=准备发言` 出现，永远不进 transcript。**

### 2.4 快照

```http
GET /api/discussions/d_7f3a91
```

```json
{
  "ok": true,
  "data": {
    "id": "d_7f3a91", "status": "FINISHED", "ended_reason": "converged",
    "turn_count": 17,
    "panel": [ ... ],
    "transcript": [ {"seq":1,"panelist_id":"p1","content":"...","phase":"OPENING"} ],
    "consensus": {
      "version": 4,
      "points": ["双方都认同'岗位可远程性'是关键变量"],
      "disputes": ["远程对创新的影响是否可逆，未达成一致"]
    },
    "final_summary": "……"
  }
}
```

`final_summary` 由主持人基于 `consensus.version` 最后一次的状态写成，**不重新读 transcript 抽取**。

---

## 3. 错误码

| code | HTTP | 含义 | 前端行为 |
|---|---|---|---|
| `INVALID_TOPIC` | 400 | 议题为空或过长 | 表单提示 |
| `INVALID_COUNT` | 400 | 人数越界 | 表单提示 |
| `NOT_CONFIRMABLE` | 409 | 状态不允许该操作 | 刷新快照 |
| `LLM_UNAVAILABLE` | 503 | 模型连续失败 | 提示降级，允许重试 |
| `DISCUSSION_NOT_FOUND` | 404 | 场次不存在 | 回首页 |

---

## 4. 可观测性

每个 SSE 事件在服务端日志落一行（不落数据库，避免膨胀）：

```
[d_7f3a91] seq=12 type=utterance panelist=p3 intent=反驳 latency_ms=1840 tokens=312 retry=0
```

`/api/health` 返回
`{"ok":true,"data":{"db":"ok","llm":{"endpoint":"https://api.deepseek.com","model":"deepseek-chat","key_configured":true}}}`
——**只报 endpoint、model 名和"配没配 key"这个布尔值，绝不回显 key 本身**
（连前四位都不给：脱敏过的片段照样能拿去撞库）。

---

## 5. 与前端的分工边界

- 后端只保证：事件有序、`seq` 单调、重连可补。
- 前端不做任何"看起来像在讨论"的仿真动画。**侧边小窗的状态变化必须由 `panelist_state` 事件驱动**，不允许 `setTimeout` 造节奏。
- 这条是需求「不能前端做仿真」的落地形式，写在契约里比写在口头上可靠。
