/**
 * 边界适配层的用例。
 *
 * 夹具不是手写的：`__fixtures__/d_01.events.json` 是从 data/panel.db 的
 * event 表直接导出的真实事件流（seed 的 d_01，41 帧）。手写夹具只能证明
 * "我按自己想象的形状写的转换是对的"，导出夹具才能证明"我按后端的真实输出写"。
 *
 * 这里刻意只测**纯函数**：不发请求、不碰 DOM。所以它能进 CI，
 * 也不需要起后端。
 */

import { describe, expect, it } from 'vitest'
import rawFrames from './__fixtures__/d_01.events.json'
import {
  toConsensus,
  toDetail,
  toEvent,
  toSummary,
  type RawFrame,
  type RawSnapshot,
} from './adapter'
import { replay, type StudioEvent } from './types'

const frames = rawFrames as RawFrame[]

/** 取真实流里第一条某类型帧 */
function frameOf(type: string): RawFrame {
  const f = frames.find((x) => x.type === type)
  if (!f) throw new Error(`夹具里没有 ${type} 帧`)
  return f
}

describe('SSE 帧 → 前端事件', () => {
  it('发言帧的 seq 与 utterance_seq 是两个数，不许混用', () => {
    const frame = frameOf('utterance')
    const ev = toEvent(frame)
    expect(ev?.type).toBe('utterance')
    if (ev?.type !== 'utterance') return

    // 真实数据里这条发言是第 1 句，但它是第 3 个事件
    expect(ev.utterance.seq).toBe(1)
    expect(ev.seq).toBe(frame.seq)
    expect(ev.utterance.seq).not.toBe(ev.seq)
  })

  it('回应关系取 reply_to_utterance_seq，没有回应时是 null 而不是 0', () => {
    const events = frames
      .map(toEvent)
      .filter((e): e is Extract<StudioEvent, { type: 'utterance' }> => e?.type === 'utterance')

    expect(events.length).toBeGreaterThan(1)
    expect(events[0]?.utterance.replyToSeq).toBeNull()
    // 第 2 句一定在回应第 1 句（seed 的剧本如此），0 会让界面去指"第 0 句"
    expect(events[1]?.utterance.replyToSeq).toBe(1)
    expect(events[1]?.utterance.replyToSeq).not.toBe(0)
  })

  it('共识的 based_on_seq=0 要保留成 0，不能变成 undefined', () => {
    const c = toConsensus({ version: 1, points: ['x'], disputes: [], based_on_seq: 0 })
    expect(c.basedOnSeq).toBe(0)
    expect(Number.isFinite(c.basedOnSeq)).toBe(true)
  })

  it('未知事件类型返回 null —— 后端加新事件，老前端不该崩', () => {
    expect(toEvent({ seq: 99, type: 'mood', payload: {} })).toBeNull()
  })

  it('引擎级故障没有 panelist_id 时用空串，不伪造专家 id', () => {
    const ev = toEvent({ seq: 12, type: 'error', payload: { code: 'ENGINE_CRASH' } })
    expect(ev).toMatchObject({ type: 'error', panelistId: '', code: 'ENGINE_CRASH' })
  })

  it('结束帧只带 reason 与 turns，不重复携带共识版本', () => {
    const ev = toEvent(frameOf('ended'))
    expect(ev).toMatchObject({ type: 'ended', reason: 'converged' })
    expect(ev && 'consensusVersion' in ev).toBe(false)
  })
})

describe('真实事件流回放', () => {
  const events = frames.map(toEvent).filter((e): e is StudioEvent => e !== null)

  it('41 帧全部可识别，没有一帧被丢弃', () => {
    expect(events).toHaveLength(frames.length)
  })

  it('回放后得到 11 条发言、converged 收尾、共识版本 3', () => {
    const state = replay(events)
    expect(state.transcript).toHaveLength(11)
    expect(state.ended).toEqual({ reason: 'converged', turns: 11 })
    expect(state.consensus.version).toBe(3)
    expect(state.phase).toBe('CLOSING')
  })

  it('内部状态不进 transcript —— 22 条 panelist_state 一条都不许混进来', () => {
    const state = replay(events)
    const stateCount = frames.filter((f) => f.type === 'panelist_state').length
    expect(stateCount).toBe(22)
    expect(state.transcript).toHaveLength(frames.filter((f) => f.type === 'utterance').length)
    expect(state.transcript.every((u) => u.content.length > 0)).toBe(true)
  })

  it('中途截断（模拟断线）再补剩余帧，与一次跑完结果一致', () => {
    // 断线重连的本质：服务端从 last seq 的下一帧开始补发，
    // 前端把两段接起来，界面必须和没断过一模一样。
    const cut = 17
    const first = replay(events.slice(0, cut))
    const rejoined = replay(events.slice(cut)).transcript
    const merged = [...first.transcript, ...rejoined].map((u) => u.seq)

    expect(merged).toEqual(replay(events).transcript.map((u) => u.seq))
  })
})

describe('快照 → 前端明细', () => {
  const snap: RawSnapshot = {
    id: 'd_01',
    topic: '远程办公是否应该成为公司默认工作方式',
    status: 'FINISHED',
    ended_reason: 'converged',
    turn_count: 11,
    created_at: '2026-11-18T09:13:05',
    max_turns: 24,
    final_summary: '收尾文本',
    panel: [
      { id: 'p_01_0', role: 'host', seat: 0, name: '陈立', title: '主持', stance: '中立', color: '#c2410c' },
      { id: 'p_01_1', role: 'expert', seat: 1, name: '周敏', title: '教授', stance: '反对', color: '#1d4ed8' },
      { id: 'p_01_2', role: 'expert', seat: 2, name: '许巍', title: 'CTO', stance: '支持', color: '#15803d' },
    ],
    transcript: [
      { seq: 1, panelist_id: 'p_01_0', content: '开场', phase: 'OPENING', intent: '抛论点', reply_to_seq: null, created_at: '2026-11-18T09:13:05' },
      { seq: 2, panelist_id: 'p_01_1', content: '回应', phase: 'EXPLORE', intent: '反驳', reply_to_seq: 1, created_at: '2026-11-18T09:13:32' },
    ],
    consensus: { version: 3, points: ['a'], disputes: ['b'], based_on_seq: 10 },
  }

  it('快照没给 expert_count，按 panel 里的 expert 数推（主持人不算专家）', () => {
    const d = toDetail(snap)
    expect(d.expertCount).toBe(2)
    expect(d.panel).toHaveLength(3)
  })

  it('快照没给共识版本，按 consensus.version 推', () => {
    expect(toDetail(snap).consensusVersion).toBe(3)
  })

  it('快照发言合成稳定 id，phase 与 replyToSeq 都对得上', () => {
    const d = toDetail(snap)
    expect(d.transcript.map((u) => u.id)).toEqual(['u_1', 'u_2'])
    expect(d.transcript[1]).toMatchObject({ panelistId: 'p_01_1', replyToSeq: 1, phase: 'EXPLORE' })
  })

  it('发言时间戳取自后端，不前端自己 new Date()', () => {
    const ev = frames.map(toEvent).find((e) => e?.type === 'utterance')
    if (ev?.type !== 'utterance') throw new Error('夹具里没有发言帧')
    // 直播那一刻的时间只有后端知道，前端补一个现在时间会与回放对不上
    expect(ev.utterance.createdAt).toBe('2026-11-17T09:13:05')
    expect(toDetail(snap).transcript[0]?.createdAt).toBe('2026-11-18T09:13:05')
  })

  it('列表端点给了权威计数就不许再推导覆盖', () => {
    const s = toSummary({
      id: 'd_01',
      topic: 't',
      status: 'FINISHED',
      ended_reason: 'converged',
      turn_count: 11,
      created_at: 'now',
      expert_count: 4,
      utterance_count: 11,
      consensus_version: 3,
    })
    expect(s).toMatchObject({ expertCount: 4, utteranceCount: 11, consensusVersion: 3 })
  })

  it('未知状态落到 FAILED 而不是 LIVE —— 否则界面会去连一条不会结束的流', () => {
    const d = toDetail({ ...snap, status: 'PAUSED' })
    expect(d.status).toBe('FAILED')
  })
})
