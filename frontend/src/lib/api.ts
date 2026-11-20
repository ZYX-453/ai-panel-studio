/**
 * 传输层：HTTP 请求 + SSE 连接。
 *
 * 只做三件事：拼路径、拆 `{ok, data}` 信封、把 SSE 帧丢给 adapter。
 * **不含业务判断** —— 谁该发言、讨论算不算结束，全在后端引擎。
 * 组件也不直接 fetch：这样"错误长什么样"只有一处定义，不会出现
 * 某个端点失败时界面白屏、另一个端点失败时弹提示。
 */

import {
  toDetail,
  toEvent,
  toSummary,
  type RawFrame,
  type RawSnapshot,
  type RawSummary,
} from './adapter'
import type { DiscussionDetail, DiscussionSummary, StudioEvent } from './types'

/** 后端统一错误码，见 docs/03-API.md */
export class ApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

interface Envelope {
  ok: boolean
  data?: unknown
  error?: { code?: string; message?: string }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    // fetch 只在网络层失败时抛异常（后端没起、被防火墙拦），此时没有 HTTP 码
    throw new ApiError('NETWORK_DOWN', '连不上后端服务，请确认后端已启动', 0)
  }

  let body: Envelope | null = null
  try {
    body = (await res.json()) as Envelope
  } catch {
    body = null
  }

  // 代理层报错时返回的是 HTML，不能直接当业务错误读
  if (!body || typeof body.ok !== 'boolean') {
    throw new ApiError('BAD_RESPONSE', `后端返回了非预期内容（HTTP ${res.status}）`, res.status)
  }
  if (!body.ok) {
    throw new ApiError(
      body.error?.code ?? 'UNKNOWN',
      body.error?.message ?? '请求失败',
      res.status,
    )
  }
  return body.data as T
}

function jsonBody(value: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(value),
  }
}

/** 任何异常 → 一句能直接显示给用户的话。界面只有这一个错误文本出口。 */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.message
  if (err instanceof Error) return err.message
  return '未知错误'
}

/* -------------------------------------------------------------------- 端点 */

export async function listDiscussions(): Promise<DiscussionSummary[]> {
  const rows = await request<RawSummary[]>('/api/discussions')
  return rows.map(toSummary)
}

export async function getDiscussion(id: string): Promise<DiscussionDetail> {
  const raw = await request<RawSnapshot>(`/api/discussions/${encodeURIComponent(id)}`)
  return toDetail(raw)
}

export async function createDiscussion(
  topic: string,
  expertCount: number,
): Promise<DiscussionDetail> {
  const raw = await request<RawSnapshot>(
    '/api/discussions',
    jsonBody({ topic, expert_count: expertCount }),
  )
  return toDetail(raw)
}

export async function regeneratePanel(id: string): Promise<DiscussionDetail> {
  const raw = await request<RawSnapshot>(
    `/api/discussions/${encodeURIComponent(id)}/regenerate`,
    jsonBody({}),
  )
  return toDetail(raw)
}

export async function startDiscussion(id: string): Promise<void> {
  await request<{ id: string; status: string }>(
    `/api/discussions/${encodeURIComponent(id)}/start`,
    { method: 'POST' },
  )
}

export async function exportMarkdown(id: string): Promise<string> {
  const data = await request<{ markdown: string }>(
    `/api/discussions/${encodeURIComponent(id)}/export`,
  )
  return data.markdown
}

/* ---------------------------------------------------------------------- SSE */

export type StreamStatus = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface StreamHandle {
  close(): void
}

/** 与后端 event 表 CHECK 约束一致（不含传输层的 ping） */
const EVENT_TYPES = [
  'phase',
  'panelist_state',
  'utterance',
  'consensus',
  'ended',
  'error',
] as const

/**
 * 打开事件流。
 *
 * 用原生 EventSource 而不是手写 fetch 流：断线重连时浏览器会自动带上
 * `Last-Event-ID`，服务端据此从下一个 seq 接着补发 —— 这正是"直播"和
 * "断线补发"共用一条渲染管线的物理基础。手写的话这段逻辑要自己保证。
 *
 * `fromSeq` 只用于「从头回放」：它走查询参数，因为 EventSource 不允许改请求头。
 */
export function openStream(
  id: string,
  handlers: {
    fromSeq?: number
    onEvent: (event: StudioEvent) => void
    onStatus?: (status: StreamStatus) => void
  },
): StreamHandle {
  const from = handlers.fromSeq ?? 0
  const url = `/api/discussions/${encodeURIComponent(id)}/stream?from_seq=${from}`
  const es = new EventSource(url)

  let closed = false
  let seenSinceOpen = false
  let idleReopens = 0

  const close = () => {
    if (closed) return
    closed = true
    es.close()
    handlers.onStatus?.('closed')
  }

  es.addEventListener('open', () => {
    // 服务端正常收尾后连接会被关闭，EventSource 会无条件重连。
    // 若连续几次重连都没有新事件，说明这场讨论确实已经结束，主动收手，
    // 否则会每 2 秒空转一次，直到用户关掉页面。
    if (!seenSinceOpen) idleReopens += 1
    seenSinceOpen = false
    if (idleReopens > 3) {
      close()
      return
    }
    handlers.onStatus?.(idleReopens > 0 ? 'reconnecting' : 'open')
  })

  for (const type of EVENT_TYPES) {
    es.addEventListener(type, (ev) => {
      seenSinceOpen = true
      const message = ev as MessageEvent<string>
      let frame: RawFrame
      try {
        frame = { seq: Number(message.lastEventId), type, payload: JSON.parse(message.data) }
      } catch {
        // 半截帧（连接正好断在 data 中间）直接丢弃，下次重连会从 id 重发
        return
      }
      const event = toEvent(frame)
      if (!event) return
      handlers.onEvent(event)
      if (event.type === 'ended') close()
    })
  }

  es.addEventListener('error', () => {
    if (closed) return
    // readyState=CLOSED 表示浏览器放弃了重连（4xx 之类），此时报错比装死好
    handlers.onStatus?.(es.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting')
    if (es.readyState === EventSource.CLOSED) close()
  })

  handlers.onStatus?.('connecting')
  return { close }
}
