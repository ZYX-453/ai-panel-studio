import { useEffect, useRef } from 'react'
import { formatClock } from '../lib/format'
import type { Panelist, Phase, Utterance } from '../lib/types'

const PHASE_LABEL: Record<Phase, string> = {
  OPENING: '开场',
  EXPLORE: '各方陈述',
  CLASH: '交锋',
  CLOSING: '收尾',
}

interface TranscriptProps {
  transcript: Utterance[]
  panel: Panelist[]
  /** 讨论进行中时给一句"正在等下一句"的提示，而不是留白 */
  waiting?: string | null
  /** 是否跟随最新发言自动滚到底；回看一场已结束的讨论时应为 false */
  follow?: boolean
  /** 讨论是否已经结束 —— 空态的措辞要靠它区分「还没开始」和「一句都没有」 */
  ended?: boolean
}

/**
 * 现场 transcript。
 *
 * 这里只渲染 utterance —— 需求要求"不显示举手等内部事件"，
 * 而这里连拿都拿不到：内部状态在 panelist_state 里，另一条渲染路径。
 *
 * aria 用 role="log" + aria-relevant="additions"：
 * 只念新增的那一条，而不是每次状态变化把整屏重念一遍。
 */
export function Transcript({ transcript, panel, waiting, follow = true, ended = false }: TranscriptProps) {
  const boxRef = useRef<HTMLDivElement>(null)
  const mountedLen = useRef(transcript.length)

  // 只滚动容器自身，不动 window —— 需求要求各区域在自身容器里独立滚动。
  // 注意：打开一场已结束的讨论时**不**自动跳到底部，
  // 因为那是回看，应该从第一句开始读；只有新发言进来才跟随。
  useEffect(() => {
    if (!follow) {
      mountedLen.current = transcript.length
      return
    }
    if (transcript.length <= mountedLen.current) {
      mountedLen.current = transcript.length
      return
    }
    mountedLen.current = transcript.length
    const el = boxRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [transcript.length, follow])

  const byId = new Map(panel.map((p) => [p.id, p]))
  const bySeq = new Map(transcript.map((u) => [u.seq, u]))
  const seenPhase = new Set<Phase>()

  return (
    <div className="tr" ref={boxRef} role="log" aria-live="polite" aria-relevant="additions">
      {transcript.length === 0 && (
        <p className="tr-empty">
          {/* 降级收尾的场次是「已结束且一句都没有」，写「还没开始」会自相矛盾：
              顶部状态条同一屏写着「已结束 · 模型异常，降级收尾」 */}
          {ended
            ? '本场没有产生任何发言，原因见下方收尾说明。'
            : '讨论还没开始，第一条发言出现后这里会实时滚入。'}
        </p>
      )}

      {transcript.map((u) => {
        const p = byId.get(u.panelistId)
        if (!p) return null
        const isHost = p.role === 'host'
        const replyTo = u.replyToSeq ? bySeq.get(u.replyToSeq) : undefined
        const replySpeaker = replyTo ? byId.get(replyTo.panelistId) : undefined
        const showPhase = !seenPhase.has(u.phase)
        if (showPhase) seenPhase.add(u.phase)

        return (
          <div key={u.id}>
            {showPhase && (
              <div className="tr-phase">
                <span>{PHASE_LABEL[u.phase]}</span>
              </div>
            )}

            <article className={`say${isHost ? ' say-host' : ''}`} style={{ ['--seat' as string]: p.color }}>
              <div className="say-head">
                <span className="say-name">{p.name}</span>
                <span className="say-title">{p.title}</span>
                {u.intent && u.intent !== '抛论点' && <span className="say-intent">{u.intent}</span>}
                {/* 时间来自后端事件，回看时看到的是当时那一刻，不是打开页面的时间 */}
                {formatClock(u.createdAt) && (
                  <span className="say-time">{formatClock(u.createdAt)}</span>
                )}
              </div>
              {replySpeaker && (
                <p className="say-reply">回应 {replySpeaker.name} 第 {u.replyToSeq} 句</p>
              )}
              <p className="say-text">{u.content}</p>
            </article>
          </div>
        )
      })}

      {waiting && (
        <p className="tr-waiting">
          <span className="tr-waiting-dot" aria-hidden="true" />
          {waiting}
        </p>
      )}
    </div>
  )
}
