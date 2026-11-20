import { Avatar } from './Avatar'
import type { Panelist, PanelistState } from '../lib/types'

interface ExpertWindowProps {
  panelist: Panelist
  state: PanelistState
  /** 公开关注点摘要 —— 是给人看的一句话，不是隐藏思维链 */
  focus?: string
  utterances: number
}

const STATE_TONE: Record<PanelistState, string> = {
  待机: 'idle',
  准备发言: 'ready',
  发言中: 'speaking',
}

/**
 * 专家状态小窗。
 *
 * 状态完全来自 panelist_state 事件，组件自身不持有任何计时器 ——
 * 这是需求「不能前端做仿真」在组件层面的落点：把这个组件单独拿出来，
 * 不给事件它就一直不动，不会自己"演"。
 */
export function ExpertWindow({ panelist, state, focus, utterances }: ExpertWindowProps) {
  return (
    <article className={`xw xw-${STATE_TONE[state]}`} style={{ ['--seat' as string]: panelist.color }}>
      <div className="xw-top">
        <Avatar name={panelist.name} color={panelist.color} size={34} speaking={state === '发言中'} />
        <div className="xw-id">
          <div className="xw-name">
            {panelist.name}
            {panelist.role === 'host' && <span className="xw-host">主持</span>}
          </div>
          <div className="xw-title" title={panelist.title}>{panelist.title}</div>
        </div>
        <span className={`xw-state xw-state-${STATE_TONE[state]}`}>
          <span className="xw-dot" aria-hidden="true" />
          {state}
        </span>
      </div>

      <p className="xw-focus">
        {focus ? `关注：${focus}` : `立场：${panelist.stance}`}
      </p>

      <div className="xw-foot">
        <span className="xw-count">已发言 {utterances} 次</span>
      </div>
    </article>
  )
}
