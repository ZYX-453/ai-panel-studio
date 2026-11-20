import { useMemo, useState } from 'react'
import { Lobby } from './pages/Lobby'
import { Confirm } from './pages/Confirm'
import { Studio } from './pages/Studio'
import { buildMockPanel, mockDetails, mockList } from './mock/data'
import { navigate, useRoute } from './lib/router'
import { emptyConsensus, type DiscussionDetail, type DiscussionSummary } from './lib/types'

function nowLabel(): string {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

export function App() {
  const route = useRoute()
  const [list, setList] = useState<DiscussionSummary[]>(mockList)
  const [details, setDetails] = useState<Record<string, DiscussionDetail>>(mockDetails)
  const [newSeq, setNewSeq] = useState(1)
  const [regenerateAt, setRegenerateAt] = useState<Record<string, number>>({})

  const detail = route.name === 'discussion' ? details[route.id] : undefined

  const notice = useMemo(() => {
    if (!detail) return null
    if (detail.status === 'LIVE' && detail.transcript.length === 0) {
      return '设计预览：尚未接入后端引擎，这一场不会有新事件。真实推进由后端 SSE 驱动。'
    }
    return null
  }, [detail])

  function create(topic: string, expertCount: number) {
    const id = `d_n${newSeq}`
    setNewSeq((n) => n + 1)
    const panel = buildMockPanel(id, expertCount)
    const summary: DiscussionSummary = {
      id,
      topic,
      status: 'AWAITING_CONFIRM',
      endedReason: null,
      turnCount: 0,
      expertCount,
      utteranceCount: 0,
      consensusVersion: 0,
      createdAt: nowLabel(),
    }
    const full: DiscussionDetail = {
      ...summary,
      panel,
      transcript: [],
      consensus: emptyConsensus,
      finalSummary: null,
      maxTurns: 24,
    }
    setList((prev) => [summary, ...prev])
    setDetails((prev) => ({ ...prev, [id]: full }))
    navigate({ name: 'discussion', id })
  }

  function start(id: string) {
    setList((prev) => prev.map((d) => (d.id === id ? { ...d, status: 'LIVE' } : d)))
    setDetails((prev) => {
      const cur = prev[id]
      if (!cur) return prev
      return { ...prev, [id]: { ...cur, status: 'LIVE' } }
    })
  }

  function regenerate(id: string) {
    const turn = (regenerateAt[id] ?? 0) + 1
    setRegenerateAt((prev) => ({ ...prev, [id]: turn }))
    setDetails((prev) => {
      const cur = prev[id]
      if (!cur) return prev
      // 预览期只是把候选池换个起点，制造"换了一批人"的效果
      const rotated = buildMockPanel(id, cur.expertCount).map((p, i) => ({ ...p, seat: i }))
      const shift = turn % Math.max(1, rotated.length - 1)
      const head = rotated[0]!
      const rest = rotated.slice(1)
      const mixed = [...rest.slice(shift), ...rest.slice(0, shift)]
      return { ...prev, [id]: { ...cur, panel: [head, ...mixed.map((p, i) => ({ ...p, seat: i + 1 }))] } }
    })
  }

  if (route.name === 'discussion') {
    if (!detail) {
      return (
        <div className="notfound">
          <h1>找不到这场讨论</h1>
          <p>它可能已经被删除，也可能链接里的编号不对。</p>
          <button className="btn btn-primary" onClick={() => navigate({ name: 'lobby' })}>
            回到列表
          </button>
        </div>
      )
    }

    if (detail.status === 'AWAITING_CONFIRM') {
      return (
        <Confirm
          detail={detail}
          onStart={() => start(detail.id)}
          onRegenerate={() => regenerate(detail.id)}
        />
      )
    }

    return <Studio detail={detail} notice={notice} />
  }

  return <Lobby discussions={list} onCreate={create} />
}
