import type { DiscussionStatus, EndedReason } from '../lib/types'

const STATUS_TEXT: Record<DiscussionStatus, string> = {
  GENERATING: '生成阵容中',
  AWAITING_CONFIRM: '待确认阵容',
  LIVE: '讨论进行中',
  FINISHED: '已结束',
  FAILED: '已失败',
}

const REASON_TEXT: Record<EndedReason, string> = {
  converged: '已收敛',
  max_turns: '达到轮次上限',
  degraded: '模型异常，降级收尾',
}

export function StatusBadge({
  status,
  endedReason,
}: {
  status: DiscussionStatus
  endedReason: EndedReason | null
}) {
  const tone =
    status === 'LIVE' ? 'live'
    : status === 'AWAITING_CONFIRM' ? 'pending'
    : status === 'FAILED' ? 'fail'
    : 'done'

  return (
    <span className={`badge badge-${tone}`}>
      {status === 'LIVE' && <span className="badge-dot" aria-hidden="true" />}
      {STATUS_TEXT[status]}
      {status === 'FINISHED' && endedReason && (
        <span className="badge-sub">· {REASON_TEXT[endedReason]}</span>
      )}
    </span>
  )
}

/** 阶段指示。四个阶段名是代码判定的，不是模型自报的，所以可以放心直接显示。 */
const PHASE_TEXT = {
  OPENING: '开场',
  EXPLORE: '各方陈述',
  CLASH: '交锋',
  CLOSING: '收尾',
} as const

export function PhaseBadge({ phase }: { phase: keyof typeof PHASE_TEXT | null }) {
  if (!phase) return null
  return <span className="badge badge-phase">{PHASE_TEXT[phase]}</span>
}
