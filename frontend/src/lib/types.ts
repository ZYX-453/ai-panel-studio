/** 与 docs/03-API.md 的契约一一对应。改这里必须回头改文档。 */

export type DiscussionStatus =
  | 'GENERATING'
  | 'AWAITING_CONFIRM'
  | 'LIVE'
  | 'FINISHED'
  | 'FAILED'

export type EndedReason = 'converged' | 'max_turns' | 'degraded'

export type Phase = 'OPENING' | 'EXPLORE' | 'CLASH' | 'CLOSING'

/** 内部状态，只在侧边小窗显示，绝不进 transcript */
export type PanelistState = '待机' | '准备发言' | '发言中'

export interface Panelist {
  id: string
  role: 'host' | 'expert'
  seat: number
  name: string
  title: string
  stance: string
  color: string
}

export interface Utterance {
  id: string
  panelistId: string
  seq: number
  phase: Phase
  intent: string
  content: string
  replyToSeq: number | null
  createdAt: string
}

export interface Consensus {
  version: number
  points: string[]
  disputes: string[]
  basedOnSeq: number
}

export interface DiscussionSummary {
  id: string
  topic: string
  status: DiscussionStatus
  endedReason: EndedReason | null
  turnCount: number
  expertCount: number
  utteranceCount: number
  consensusVersion: number
  createdAt: string
}

export interface DiscussionDetail extends DiscussionSummary {
  panel: Panelist[]
  transcript: Utterance[]
  consensus: Consensus
  finalSummary: string | null
  maxTurns: number
}

/** SSE 事件。type 的取值范围与 event 表的 CHECK 一致（不含 ping）。 */
export type StudioEvent =
  | { seq: number; type: 'phase'; phase: Phase }
  | {
      seq: number
      type: 'panelist_state'
      panelistId: string
      state: PanelistState
      /** 公开的关注点摘要。注意：这是给人看的短句，不是模型的隐藏思维链 */
      focus?: string
    }
  | { seq: number; type: 'utterance'; utterance: Utterance }
  | { seq: number; type: 'consensus'; consensus: Consensus }
  | { seq: number; type: 'ended'; reason: EndedReason; turns: number }
  | { seq: number; type: 'error'; panelistId: string; code: string }

/** 演播厅的渲染状态：直播和回放共用同一份结构 */
export interface StudioState {
  phase: Phase | null
  states: Record<string, PanelistState>
  transcript: Utterance[]
  consensus: Consensus
  ended: { reason: EndedReason; turns: number } | null
  errors: Array<{ panelistId: string; code: string }>
  /** 已应用到第几个事件，回放/断线重连靠它定位 */
  cursor: number
}

export const emptyConsensus: Consensus = {
  version: 0,
  points: [],
  disputes: [],
  basedOnSeq: 0,
}

/**
 * 单条事件 → 新状态。纯函数。
 *
 * 之所以把状态演进收在一个函数里，是因为直播、断线重连补发、
 * 结束后回放三条路径必须产生**完全一样**的界面。
 * 如果各写一套，就一定会出现「直播有、回放丢」的缝。
 */
export function applyEvent(state: StudioState, event: StudioEvent): StudioState {
  switch (event.type) {
    case 'phase':
      return { ...state, phase: event.phase, cursor: state.cursor + 1 }
    case 'panelist_state':
      return {
        ...state,
        states: { ...state.states, [event.panelistId]: event.state },
        cursor: state.cursor + 1,
      }
    case 'utterance':
      return {
        ...state,
        transcript: [...state.transcript, event.utterance],
        cursor: state.cursor + 1,
      }
    case 'consensus':
      return { ...state, consensus: event.consensus, cursor: state.cursor + 1 }
    case 'ended':
      return {
        ...state,
        ended: { reason: event.reason, turns: event.turns },
        states: {},
        cursor: state.cursor + 1,
      }
    case 'error':
      return {
        ...state,
        errors: [...state.errors, { panelistId: event.panelistId, code: event.code }],
        cursor: state.cursor + 1,
      }
    default:
      return state
  }
}

export function replay(events: StudioEvent[]): StudioState {
  return events.reduce(applyEvent, {
    phase: null,
    states: {},
    transcript: [],
    consensus: emptyConsensus,
    ended: null,
    errors: [],
    cursor: 0,
  })
}
