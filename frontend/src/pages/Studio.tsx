import { useEffect, useMemo, useRef, useState } from 'react'
import { ConsensusPanel } from '../components/ConsensusPanel'
import { ExpertWindow } from '../components/ExpertWindow'
import { PhaseBadge, StatusBadge } from '../components/StatusBadge'
import { Transcript } from '../components/Transcript'
import { projectEvents } from '../mock/data'
import { navigate } from '../lib/router'
import { replay, type DiscussionDetail, type PanelistState } from '../lib/types'

type Pane = 'experts' | 'stage' | 'consensus'

const IDLE: PanelistState = '待机'

/**
 * 演播厅。
 *
 * 三区各自独立滚动，整页不滚 —— 需求明确要求。
 * 窄屏下三区变成页签，一次只看一个，同样不产生整页滚动。
 *
 * 关于"不能前端做仿真"：这里的「回放」播放的是**已记录的事件流**，
 * 走的是同一个 replay() 和同一套事件类型，与真实 SSE 到货后的渲染完全一致。
 * 组件自身没有"假装在讨论"的计时器 —— 停掉回放，画面就停住。
 */
export function Studio({ detail, notice }: { detail: DiscussionDetail; notice?: string | null }) {
  const events = useMemo(
    () => (detail.endedReason || detail.transcript.length > 0 ? projectEvents(detail) : []),
    [detail],
  )

  const [cursor, setCursor] = useState(events.length)
  const [playing, setPlaying] = useState(false)
  const [pane, setPane] = useState<Pane>('stage')
  const timer = useRef<number | null>(null)

  const state = useMemo(() => replay(events.slice(0, cursor)), [events, cursor])

  useEffect(() => {
    if (!playing) return
    if (cursor >= events.length) {
      setPlaying(false)
      return
    }
    timer.current = window.setTimeout(() => setCursor((c) => c + 1), 220)
    return () => {
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [playing, cursor, events.length])

  const counts = useMemo(() => {
    const m = new Map<string, number>()
    for (const u of state.transcript) m.set(u.panelistId, (m.get(u.panelistId) ?? 0) + 1)
    return m
  }, [state.transcript])

  const isLive = detail.status === 'LIVE'
  const waiting = isLive && !state.ended ? '正在组织下一位发言者…' : null

  return (
    <div className="studio" data-pane={pane}>
      <header className="studio-bar">
        <button className="btn btn-ghost" onClick={() => navigate({ name: 'lobby' })} aria-label="返回讨论列表">
          ←
        </button>
        <div className="studio-topic">
          <h1>{detail.topic}</h1>
          <div className="studio-meta">
            <StatusBadge status={detail.status} endedReason={detail.endedReason} />
            <PhaseBadge phase={state.phase} />
            <span className="studio-turns">
              {state.transcript.length} / {detail.maxTurns} 句
            </span>
          </div>
        </div>

        <div className="studio-tools">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setCursor(0)
              setPlaying(true)
            }}
            disabled={events.length === 0}
          >
            从头回放
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setCursor(events.length)}
            disabled={cursor >= events.length}
          >
            跳到结尾
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
          {notice && <p className="studio-notice">{notice}</p>}
          <Transcript transcript={state.transcript} panel={detail.panel} waiting={waiting} />
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
