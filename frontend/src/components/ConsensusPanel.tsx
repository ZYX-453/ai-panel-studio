import type { Consensus } from '../lib/types'

/**
 * 实时共识 / 分歧。
 *
 * 两条硬规则：
 * 1. 空值要说话。「还没有共识」和「组件坏了」在界面上长一样，
 *    所以 version=0 时渲染一句明确的空态，而不是留白。
 * 2. 这里显示的一切都来自同一个 Consensus 对象（单一真相源），
 *    最终总结也从这个对象写，不允许另起一套判断。
 */
export function ConsensusPanel({ consensus, ended = false }: { consensus: Consensus; ended?: boolean }) {
  const hasAny = consensus.points.length > 0 || consensus.disputes.length > 0

  return (
    <div className="cp">
      <header className="cp-head">
        <h2 className="cp-title">共识与分歧</h2>
        <span className={`cp-ver${consensus.version === 0 ? ' cp-ver-empty' : ''}`}>
          {consensus.version === 0 ? '尚未形成' : `v${consensus.version}`}
        </span>
      </header>

      {consensus.version > 0 && (
        <p className="cp-based">基于前 {consensus.basedOnSeq} 句发言</p>
      )}

      {!hasAny && (
        <p className="cp-empty">
          {ended ? (
            '本场没有形成可记录的共识或分歧。'
          ) : (
            <>
              讨论刚开始，暂时提炼不出共识或分歧。
              <br />
              随着发言累积，这里会自动更新。
            </>
          )}
        </p>
      )}

      {consensus.points.length > 0 && (
        <section className="cp-group">
          <h3 className="cp-group-title">
            已达成共识 <span className="cp-num">{consensus.points.length}</span>
          </h3>
          <ul className="cp-list">
            {consensus.points.map((t) => (
              <li key={t} className="cp-item cp-item-point">
                <span className="cp-mark cp-mark-point" aria-hidden="true" />
                {t}
              </li>
            ))}
          </ul>
        </section>
      )}

      {consensus.disputes.length > 0 && (
        <section className="cp-group">
          <h3 className="cp-group-title">
            仍存分歧 <span className="cp-num cp-num-warn">{consensus.disputes.length}</span>
          </h3>
          <ul className="cp-list">
            {consensus.disputes.map((t) => (
              <li key={t} className="cp-item cp-item-dispute">
                <span className="cp-mark cp-mark-dispute" aria-hidden="true" />
                {t}
              </li>
            ))}
          </ul>
        </section>
      )}

      {consensus.version > 0 && consensus.disputes.length === 0 && (
        <p className="cp-note">分歧已清空 —— 这一场讨论到了可以收尾的时候。</p>
      )}
    </div>
  )
}
