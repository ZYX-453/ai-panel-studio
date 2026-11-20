import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { openStream, type StreamStatus } from './api'

/**
 * 这一组用例守的是一个**只在真机上才看得出来**的缺陷：
 * 打开演播厅，顶部一直挂着「连接中断，正在重连…」，而后端访问日志里
 * 从头到尾只有一次 `/stream` 请求 —— 根本没有重连，纯粹是状态机误报。
 *
 * 成因：`open` 事件必然在任何 message 之前触发，所以在 `open` 里读
 * "本轮有没有收到事件"得到的永远是 false。首次连上就被记成一次空闲重连。
 *
 * 为什么要用假 EventSource 而不是打真接口：这类缺陷的判据是**事件顺序**
 * （open → message → error → open），真接口无法稳定复现某一次特定的重连。
 */

class FakeEventSource {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  static instances: FakeEventSource[] = []

  url: string
  readyState = FakeEventSource.CONNECTING
  closed = false
  private listeners = new Map<string, Array<(ev: unknown) => void>>()

  constructor(url: string) {
    this.url = url
    FakeEventSource.instances.push(this)
  }

  addEventListener(type: string, fn: (ev: unknown) => void): void {
    const arr = this.listeners.get(type) ?? []
    arr.push(fn)
    this.listeners.set(type, arr)
  }

  listenerCount(type: string): number {
    return (this.listeners.get(type) ?? []).length
  }

  close(): void {
    this.closed = true
    this.readyState = FakeEventSource.CLOSED
  }

  /** 模拟浏览器：连接建立 */
  fireOpen(): void {
    this.readyState = FakeEventSource.OPEN
    for (const fn of this.listeners.get('open') ?? []) fn({})
  }

  /** 模拟浏览器：服务端推来一帧 */
  fireEvent(type: string, seq: number, payload: unknown): void {
    const ev = { lastEventId: String(seq), data: JSON.stringify(payload) }
    for (const fn of this.listeners.get(type) ?? []) fn(ev)
  }

  /** 模拟浏览器：连接断了（readyState 仍是 CONNECTING，浏览器会自己重连） */
  fireError(): void {
    this.readyState = FakeEventSource.CONNECTING
    for (const fn of this.listeners.get('error') ?? []) fn({})
  }

  static latest(): FakeEventSource {
    const es = FakeEventSource.instances[FakeEventSource.instances.length - 1]
    if (!es) throw new Error('没有创建过 EventSource')
    return es
  }
}

function collect(): { statuses: StreamStatus[]; onStatus: (s: StreamStatus) => void } {
  const statuses: StreamStatus[] = []
  return { statuses, onStatus: (s) => statuses.push(s) }
}

const PHASE = { phase: 'OPENING' }
const SAY = {
  utterance_seq: 1,
  utterance_id: 'u1',
  panelist_id: 'p1',
  name: '陈立',
  title: '主持人',
  content: '今天讨论一下。',
  intent: '开场',
  reply_to_utterance_seq: null,
  phase: 'OPENING',
  created_at: '2026-11-20T11:37:58',
}

describe('openStream 的连接状态', () => {
  beforeEach(() => {
    FakeEventSource.instances = []
    vi.stubGlobal('EventSource', FakeEventSource)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('刚连上、还没收到任何事件时不许报「重连中」', () => {
    const { statuses, onStatus } = collect()
    openStream('d_01', { onEvent: () => {}, onStatus })

    FakeEventSource.latest().fireOpen()

    expect(statuses).toEqual(['connecting', 'open'])
    expect(statuses).not.toContain('reconnecting')
  })

  it('断线才报「重连中」，重连成功后回到正常', () => {
    const { statuses, onStatus } = collect()
    openStream('d_01', { onEvent: () => {}, onStatus })
    const es = FakeEventSource.latest()

    es.fireOpen()
    es.fireEvent('phase', 1, PHASE)
    es.fireError()
    expect(statuses[statuses.length - 1]).toBe('reconnecting')

    es.fireOpen()
    es.fireEvent('phase', 2, PHASE)
    expect(statuses[statuses.length - 1]).toBe('open')
  })

  it('重连后立刻又收到事件，就不该停留在「重连中」', () => {
    const { statuses, onStatus } = collect()
    openStream('d_01', { onEvent: () => {}, onStatus })
    const es = FakeEventSource.latest()

    es.fireOpen()
    es.fireError()
    es.fireOpen()
    es.fireEvent('utterance', 3, SAY)

    expect(statuses.filter((s) => s === 'reconnecting').length).toBe(1)
    expect(statuses[statuses.length - 1]).toBe('open')
  })

  it('首次连上不算空转：要连续 4 次白连才收手', () => {
    // 只断言"最后关了"是不够的 —— 把首次 open 也计一次空转，
    // 结果是 3 次就关，用户少看一屏内容却没人发现。次数必须钉死。
    const { onStatus } = collect()
    openStream('d_01', { onEvent: () => {}, onStatus })
    const es = FakeEventSource.latest()

    for (let i = 0; i < 4; i += 1) {
      es.fireOpen()
      es.fireError()
    }
    expect(es.closed).toBe(false) // 首次 + 3 次空转：还该继续等

    es.fireOpen() // 第 4 次空转
    expect(es.closed).toBe(true)
  })

  it('业务错误帧不占用 error 这个保留事件名', () => {
    // 服务端把业务 error 上线成 panel_error（见后端 SSE_EVENT_ALIAS）。
    // 如果原样上线，浏览器断线派发的 error 会同时命中业务监听器，
    // 「本轮收到过事件」被置真，空转计数永远清不掉。
    const seen: string[] = []
    openStream('d_01', { onEvent: (e) => seen.push(e.type), onStatus: () => {} })
    const es = FakeEventSource.latest()

    expect(es.listenerCount('error')).toBe(1) // 只有传输层那个
    es.fireOpen()
    es.fireEvent('panel_error', 5, { message: '有一句生成失败' })

    expect(seen).toEqual(['error'])
  })

  it('收到 ended 就地关闭，不再重连', () => {
    const { onStatus, statuses } = collect()
    openStream('d_01', { onEvent: () => {}, onStatus })
    const es = FakeEventSource.latest()

    es.fireOpen()
    es.fireEvent('ended', 9, { reason: 'max_turns', turn_count: 24 })

    expect(es.closed).toBe(true)
    expect(statuses[statuses.length - 1]).toBe('closed')
  })
})
