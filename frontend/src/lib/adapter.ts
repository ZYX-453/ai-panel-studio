/**
 * 边界翻译层：后端的 snake_case 扁平字段 ←→ 前端的 camelCase 嵌套结构。
 *
 * 为什么单独一层：
 * 后端字段名由 SQL 列名和事件语义决定（`utterance_seq` / `panelist_id`），
 * 前端组件需要的是嵌套对象（`utterance.panelistId`）。如果让组件就地解构，
 * 后端一改名，报错会散落在十几个 TSX 文件里；收在一个文件里，改一处就够。
 *
 * 这一层**只做形状转换**，不做业务判断：
 *   - 缺失字段给安全默认值，不抛异常（列表页少一个计数不该白屏）
 *   - 未知事件类型返回 null —— 后端加了新事件，老前端应当忽略而不是崩
 *   - 计数类字段只在后端没给时才从明细推导，不覆盖后端给的权威值
 *
 * 这一层**不读全局状态、不发请求、不碰 DOM**，所以可以直接被单测覆盖。
 */

import {
  emptyConsensus,
  type Consensus,
  type DiscussionDetail,
  type DiscussionStatus,
  type DiscussionSummary,
  type EndedReason,
  type Panelist,
  type Phase,
  type StudioEvent,
  type Utterance,
} from './types'

/* ------------------------------------------------------------------ 原始形状 */

export interface RawSummary {
  id: string
  topic: string
  status: string
  ended_reason: string | null
  turn_count: number
  created_at: string
  /** 列表端点给，快照端点不给 */
  expert_count?: number
  utterance_count?: number
  consensus_version?: number
}

export interface RawPanelist {
  id: string
  role: string
  seat: number
  name: string
  title: string
  stance: string
  color: string
}

export interface RawUtterance {
  seq: number
  panelist_id: string
  content: string
  phase: string
  intent: string
  reply_to_seq: number | null
  created_at: string
}

export interface RawConsensus {
  version: number
  points: string[]
  disputes: string[]
  based_on_seq: number
}

export interface RawSnapshot extends RawSummary {
  max_turns: number
  final_summary: string | null
  panel: RawPanelist[]
  transcript: RawUtterance[]
  consensus: RawConsensus
}

/** SSE 帧：`{seq, type, payload}`，payload 是各类型的扁平字段 */
export interface RawFrame {
  seq: number
  type: string
  payload: Record<string, unknown>
}

/* ------------------------------------------------------------------ 小工具 */

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function num(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

function nullableNum(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function strArray(v: unknown): string[] {
  if (!Array.isArray(v)) return []
  return v.filter((x): x is string => typeof x === 'string')
}

const STATUSES: ReadonlySet<string> = new Set([
  'GENERATING',
  'AWAITING_CONFIRM',
  'LIVE',
  'FINISHED',
  'FAILED',
])

const PHASES: ReadonlySet<string> = new Set(['OPENING', 'EXPLORE', 'CLASH', 'CLOSING'])

function asStatus(v: unknown): DiscussionStatus {
  const s = str(v)
  // 后端加了新状态而前端还没发版时，落到 FAILED 比落到 LIVE 安全：
  // 前者只是显示"异常结束"，后者会让界面去连一条永远不会结束的流。
  return STATUSES.has(s) ? (s as DiscussionStatus) : 'FAILED'
}

function asPhase(v: unknown): Phase {
  const s = str(v)
  return PHASES.has(s) ? (s as Phase) : 'EXPLORE'
}

function asEndedReason(v: unknown): EndedReason | null {
  return v === 'converged' || v === 'max_turns' || v === 'degraded' ? v : null
}

/* ------------------------------------------------------------------ 实体 */

export function toPanelist(raw: RawPanelist): Panelist {
  return {
    id: str(raw.id),
    role: raw.role === 'host' ? 'host' : 'expert',
    seat: num(raw.seat),
    name: str(raw.name),
    title: str(raw.title),
    stance: str(raw.stance),
    color: str(raw.color, '#475569'),
  }
}

/**
 * 快照里的发言没有 utterance_id（那是事件层的东西），用 seq 合成一个稳定 id。
 * 只当 React key 用，不参与任何判重 —— 判重一律看 seq。
 */
export function snapshotUtterance(raw: RawUtterance): Utterance {
  return {
    id: `u_${num(raw.seq)}`,
    panelistId: str(raw.panelist_id),
    seq: num(raw.seq),
    phase: asPhase(raw.phase),
    intent: str(raw.intent),
    content: str(raw.content),
    replyToSeq: nullableNum(raw.reply_to_seq),
    createdAt: str(raw.created_at),
  }
}

export function toConsensus(raw: RawConsensus | null | undefined): Consensus {
  if (!raw) return emptyConsensus
  return {
    version: num(raw.version),
    points: strArray(raw.points),
    disputes: strArray(raw.disputes),
    basedOnSeq: num(raw.based_on_seq),
  }
}

export function toSummary(raw: RawSummary): DiscussionSummary {
  return {
    id: str(raw.id),
    topic: str(raw.topic),
    status: asStatus(raw.status),
    endedReason: asEndedReason(raw.ended_reason),
    turnCount: num(raw.turn_count),
    expertCount: num(raw.expert_count),
    utteranceCount: num(raw.utterance_count),
    consensusVersion: num(raw.consensus_version),
    createdAt: str(raw.created_at),
  }
}

export function toDetail(raw: RawSnapshot): DiscussionDetail {
  const panel = (raw.panel ?? []).map(toPanelist)
  const transcript = (raw.transcript ?? []).map(snapshotUtterance)
  const consensus = toConsensus(raw.consensus)
  return {
    id: str(raw.id),
    topic: str(raw.topic),
    status: asStatus(raw.status),
    endedReason: asEndedReason(raw.ended_reason),
    // 快照不带这两个计数，从明细推导；列表端点给了权威值就用手上这份
    expertCount: num(raw.expert_count, panel.filter((p) => p.role === 'expert').length),
    utteranceCount: num(raw.utterance_count, transcript.length),
    consensusVersion: num(raw.consensus_version, consensus.version),
    turnCount: num(raw.turn_count),
    createdAt: str(raw.created_at),
    maxTurns: num(raw.max_turns, 24),
    finalSummary: typeof raw.final_summary === 'string' ? raw.final_summary : null,
    panel,
    transcript,
    consensus,
  }
}

/* ------------------------------------------------------------------ 事件 */

/**
 * 一帧 SSE → 一个前端事件。
 *
 * 注意 utterance 事件里的 `utterance_seq` 与帧本身 `seq` 是两个东西：
 * 一条发言会伴生 3 个事件（状态→发言→状态），所以两者永远不相等。
 * 这里显式分开赋值，就是为了避免有人把帧 seq 当成"第几句"。
 */
export function toEvent(frame: RawFrame): StudioEvent | null {
  const p = frame.payload ?? {}
  const seq = num(frame.seq)
  switch (frame.type) {
    case 'phase':
      return { seq, type: 'phase', phase: asPhase(p.phase) }
    case 'panelist_state':
      return {
        seq,
        type: 'panelist_state',
        panelistId: str(p.panelist_id),
        state: p.state === '发言中' || p.state === '准备发言' ? p.state : '待机',
        focus: typeof p.focus === 'string' ? p.focus : undefined,
      }
    case 'utterance':
      return {
        seq,
        type: 'utterance',
        utterance: {
          id: str(p.utterance_id, `u_${num(p.utterance_seq)}`),
          panelistId: str(p.panelist_id),
          seq: num(p.utterance_seq),
          phase: asPhase(p.phase),
          intent: str(p.intent),
          content: str(p.content),
          replyToSeq: nullableNum(p.reply_to_utterance_seq),
          createdAt: str(p.created_at),
        },
      }
    case 'consensus':
      return {
        seq,
        type: 'consensus',
        consensus: toConsensus({
          version: num(p.version),
          points: strArray(p.points),
          disputes: strArray(p.disputes),
          based_on_seq: num(p.based_on_seq),
        }),
      }
    case 'ended':
      return {
        seq,
        type: 'ended',
        reason: asEndedReason(p.reason) ?? 'degraded',
        turns: num(p.turns),
      }
    case 'error':
      // 引擎级故障（ENGINE_CRASH）没有 panelist_id，用空串表示"不是某个人"，
      // 不能填一个假 id —— 界面会去高亮一个不存在的专家
      return {
        seq,
        type: 'error',
        panelistId: str(p.panelist_id),
        code: str(p.code, 'UNKNOWN'),
      }
    default:
      return null
  }
}
