import { useEffect, useMemo, useRef, useState } from 'react'
import { ConsensusPanel } from '../components/ConsensusPanel'
import { ExpertWindow } from '../components/ExpertWindow'
import { PhaseBadge, StatusBadge } from '../components/StatusBadge'
import { Transcript } from '../components/Transcript'
import { navigate } from '../lib/router'
import { exportMarkdown, openStream, type StreamStatus } from '../lib/api'
import { replay, type DiscussionDetail, type PanelistState, type StudioEvent } from '../lib/types'

type Pane = 'experts' | 'stage' | 'consensus'

const IDLE: PanelistState = '待机'

/** 回放步长。只用于"重看已发生的事"，不产生任何新内容。 */
const REPLAY_STEP_MS = 220

/**
 * 演播厅。
 *
 * 三区各自独立滚动，整页不滚 —— 需求明确要求。
 * 窄屏下三区变成页签，一次只看一个，同样不产生整页滚动。
 *
 * 关于"不能前端做仿真"：这里的每一句话都来自后端 SSE 事件，
 * 组件自身没有任何"假装在讨论"的计时器。
 * 「从头回放」只是把**已经收到**的事件从头重新喂给同一个 applyEvent，
 * 慢放给人看；停掉回放，画面就停住，不会凭空长出新发言。
 */
export function Studio({ detail, onEnded }: { detail: DiscussionDetail; onEnded: () => void }) {
  const [events, setEvents] = useState<StudioEvent[]>([])
  const [replayAt, setReplayAt] = useState<number | null>(null)
  const [pane, setPane] = useState<Pane>('stage')
  const [status, setStatus] = useState<StreamStatus>('connecting')
  const lastSeq = useRef(-1)
  const timer = useRef<number | null>(null)
  const endedRef = useRef(false)

  // ---- 事件流：直播、断线补发、结束后回放，共用这一个连接
  useEffect(() => {
    lastSeq.current = -1
    endedRef.current = false
    setEvents([])
    const handle = openStream(detail.id, {
      onEvent: (ev) => {
        // 重连时服务端可能从更早的 seq 重发，重复帧必须挡住，
        // 否则同一句话会在屏幕上出现两次
        if (ev.seq <= lastSeq.current) return
        lastSeq.current = ev.seq
        setEvents((prev) => [...prev, ev])
        if (ev.type === 'ended' && !endedRef.current) {
          endedRef.current = true
          onEnded()
        }
      },
      onStatus: setStatus,
    })
    return () => handle.close()
  }, [detail.id, onEnded])

  // ---- 回放：只推进游标，不制造事件
  useEffect(() => {
    if (replayAt === null) return
    if (replayAt >= events.length) {
      setReplayAt(null)
      return
    }
    timer.current = window.setTimeout(() => setReplayAt((n) => (n === null ? null : n + 1)), REPLAY_STEP_MS)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [replayAt, events.length])

  const limit = replayAt ?? events.length
  const state = useMemo(() => replay(events.slice(0, limit)), [events, limit])

  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const u of state.transcript) m.set(u.panelistId, (m.get(u.panelistId) ?? 0) + 1)
    return m
  }, [state.transcript])

  const statusNow = state.ended ? 'FINISHED' : detail.status
  const endedReason = state.ended?.reason ?? detail.endedReason
  const errors = state.errors.length
  const waiting =
    statusNow === 'LIVE' && !state.ended && limit >= events.length ? '正在组织下一位发言者…' : null
  const replaying = replayAt !== null

  async function download() {
    try {
      const md = await exportMarkdown(detail.id)
      const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `${detail.id}.md`
      a.click()
      URL.revokeObjectURL(url)
    } catch {
      // 导出失败不影响围观，静默即可 —— 正文就在屏幕上
    }
  }

  return (
    <div className="studio" data-pane={pane}>
      <header className="studio-bar">
        <button className="btn btn-ghost" onClick={() => navigate({ name: 'lobby' })} aria-label="返回讨论列表">
          ←
        </button>
        <div className="studio-topic">
          <h1>{detail.topic}</h1>
          <div className="studio-meta">
            <StatusBadge status={statusNow} endedReason={endedReason} />
            <PhaseBadge phase={state.phase} />
            <span className="studio-turns">
              {state.transcript.length} / {detail.maxTurns} 句
            </span>
            {status === 'reconnecting' && <span className="studio-link-down">连接中断，正在重连…</span>}
          </div>
        </div>

        <div className="studio-tools">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setReplayAt(0)}
            disabled={events.length === 0 || replaying}
          >
            从头回放
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setReplayAt(null)}
            disabled={!replaying}
          >
            跳到结尾
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => void download()}
            disabled={events.length === 0}
          >
            导出
          </button>
        </div>
      </header>

      <nav className="studio-tabs" role="tablist" aria-label="演播厅区域">
        {([
          ['experts', '专家'],
          ['stage', '发言'],
          ['consensus', '共识'],
        ] as Array<[Pane, string]>).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={pane === key}
            className={`studio-tab${pane === key ? ' is-on' : ''}`}
            onClick={() => setPane(key)}
          >
            {label}
          </button>
        ))}
      </nav>

      <div className="studio-body">
        <aside className="pane pane-rail" aria-label="专家状态">
          <h2 className="pane-title">专家席</h2>
          <div className="pane-scroll xw-list">
            {detail.panel.map((p) => (
              <ExpertWindow
                key={p.id}
                panelist={p}
                state={state.states[p.id] ?? IDLE}
                utterances={counts.get(p.id) ?? 0}
              />
            ))}
          </div>
        </aside>

        <main className="pane pane-stage" aria-label="现场发言">
          {replaying && (
            <p className="studio-notice">正在回放已记录的事件流，不回放、不虚构任何未发生的内容。</p>
          )}
          {errors > 0 && (
            <p className="studio-notice">有 {errors} 句生成失败，已跳过并继续推进，讨论没有被卡住。</p>
          )}
          {/* 直播才跟随最新一句；回看已结束的讨论时停在顶部，从头读 */}
          <Transcript
            transcript={state.transcript}
            panel={detail.panel}
            waiting={waiting}
            follow={statusNow === 'LIVE'}
          />
          {state.ended && detail.finalSummary && (
            <section className="final">
              <h2 className="final-title">主持人收尾</h2>
              <p className="final-text">{detail.finalSummary}</p>
            </section>
          )}
        </main>

        <aside className="pane pane-consensus" aria-label="共识与分歧">
          <div className="pane-scroll">
            <ConsensusPanel consensus={state.consensus} />
          </div>
        </aside>
      </div>
    </div>
  )
}
