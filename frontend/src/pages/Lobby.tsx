import { useMemo, useState } from 'react'
import { StatusBadge } from '../components/StatusBadge'
import { navigate } from '../lib/router'
import type { DiscussionSummary } from '../lib/types'

type Filter = 'all' | 'live' | 'done'

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'live', label: '可加入' },
  { key: 'done', label: '已结束' },
]

export function Lobby({
  discussions,
  onCreate,
}: {
  discussions: DiscussionSummary[]
  onCreate: (topic: string, expertCount: number) => void
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [topic, setTopic] = useState('')
  const [count, setCount] = useState(4)

  const visible = useMemo(() => {
    if (filter === 'live') return discussions.filter((d) => d.status === 'LIVE' || d.status === 'AWAITING_CONFIRM')
    if (filter === 'done') return discussions.filter((d) => d.status === 'FINISHED')
    return discussions
  }, [discussions, filter])

  const canSubmit = topic.trim().length >= 2

  return (
    <div className="lobby">
      <section className="lobby-new">
        <h1 className="lobby-h1">发起一场圆桌</h1>
        <p className="lobby-sub">
          给一个议题，系统现场组一支虚拟专家局 —— 一位主持人加几位立场互不相同的专家，
          他们自己抢发言权，你负责围观。
        </p>

        <form
          className="newform"
          onSubmit={(e) => {
            e.preventDefault()
            if (!canSubmit) return
            onCreate(topic.trim(), count)
            setTopic('')
          }}
        >
          <label className="newform-label" htmlFor="topic">
            议题
          </label>
          <input
            id="topic"
            className="newform-input"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="例如：远程办公是否应该成为公司默认工作方式"
            maxLength={200}
          />

          <div className="newform-row">
            <div className="newform-count">
              <span id="count-label">专家人数</span>
              <div className="stepper" role="group" aria-labelledby="count-label">
                <button
                  type="button"
                  onClick={() => setCount((c) => Math.max(2, c - 1))}
                  disabled={count <= 2}
                  aria-label="减少一位专家"
                >
                  −
                </button>
                <output aria-live="polite">{count}</output>
                <button
                  type="button"
                  onClick={() => setCount((c) => Math.min(6, c + 1))}
                  disabled={count >= 6}
                  aria-label="增加一位专家"
                >
                  +
                </button>
              </div>
              <span className="newform-hint">含主持人共 {count + 1} 人</span>
            </div>

            <button className="btn btn-primary" type="submit" disabled={!canSubmit}>
              生成阵容
            </button>
          </div>
        </form>
      </section>

      <section className="lobby-list">
        <header className="lobby-listhead">
          <h2 className="lobby-h2">讨论列表</h2>
          <div className="seg" role="tablist" aria-label="筛选讨论">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                role="tab"
                aria-selected={filter === f.key}
                className={`seg-btn${filter === f.key ? ' is-on' : ''}`}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>
        </header>

        {visible.length === 0 ? (
          <p className="lobby-empty">这个筛选下没有讨论。换个筛选，或者在上面发起一场。</p>
        ) : (
          <ul className="dlist">
            {visible.map((d) => (
              <li key={d.id}>
                <button className="drow" onClick={() => navigate({ name: 'discussion', id: d.id })}>
                  <span className="drow-color" data-status={d.status} aria-hidden="true" />
                  <span className="drow-main">
                    <span className="drow-topic">{d.topic}</span>
                    <span className="drow-meta">
                      {d.expertCount} 位专家
                      {d.utteranceCount > 0 && <> · {d.utteranceCount} 条发言</>}
                      {d.consensusVersion > 0 && <> · 共识 v{d.consensusVersion}</>}
                      <span className="drow-time"> · {d.createdAt}</span>
                    </span>
                  </span>
                  <StatusBadge status={d.status} endedReason={d.endedReason} />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
