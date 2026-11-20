import { Avatar } from './Avatar'
import type { Panelist } from '../lib/types'

/**
 * 阵容确认页的大卡：重点展示"立场"，因为用户要判断的是
 * 这帮人凑在一起会不会真的吵起来，而不是他们头衔好不好看。
 */
export function PanelistCard({ panelist }: { panelist: Panelist }) {
  return (
    <article className="pcard" style={{ ['--seat' as string]: panelist.color }}>
      <div className="pcard-head">
        <Avatar name={panelist.name} color={panelist.color} size={44} />
        <div>
          <h3 className="pcard-name">
            {panelist.name}
            {panelist.role === 'host' && <span className="pcard-tag">主持</span>}
          </h3>
          <p className="pcard-title">{panelist.title}</p>
        </div>
      </div>
      <p className="pcard-stance">{panelist.stance}</p>
    </article>
  )
}
