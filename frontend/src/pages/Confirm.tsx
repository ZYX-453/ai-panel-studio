import { PanelistCard } from '../components/PanelistCard'
import { navigate } from '../lib/router'
import type { DiscussionDetail } from '../lib/types'

export function Confirm({
  detail,
  onStart,
  onRegenerate,
}: {
  detail: DiscussionDetail
  onStart: () => void
  onRegenerate: () => void
}) {
  const host = detail.panel.find((p) => p.role === 'host')
  const experts = detail.panel.filter((p) => p.role === 'expert')

  return (
    <div className="confirm">
      <header className="confirm-head">
        <button className="btn btn-ghost" onClick={() => navigate({ name: 'lobby' })}>
          ← 返回列表
        </button>
        <p className="confirm-kicker">阵容已生成，确认后开演</p>
        <h1 className="confirm-topic">{detail.topic}</h1>
        <p className="confirm-sub">
          一位主持人 + {experts.length} 位立场互不相同的专家。
          确认之前不会消耗任何模型调用 —— 不满意可以重掷。
        </p>
      </header>

      <section className="confirm-host">
        {host && <PanelistCard panelist={host} />}
      </section>

      <section className="confirm-grid">
        {experts.map((p) => (
          <PanelistCard key={p.id} panelist={p} />
        ))}
      </section>

      <footer className="confirm-foot">
        <button className="btn btn-ghost" onClick={onRegenerate}>
          重掷阵容
        </button>
        <button className="btn btn-primary btn-lg" onClick={onStart}>
          进入演播厅
        </button>
      </footer>
    </div>
  )
}
