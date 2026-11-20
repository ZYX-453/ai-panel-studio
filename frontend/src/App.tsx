import { useCallback, useEffect, useState } from 'react'
import { Lobby } from './pages/Lobby'
import { Confirm } from './pages/Confirm'
import { Studio } from './pages/Studio'
import { navigate, useRoute } from './lib/router'
import {
  createDiscussion,
  describeError,
  getDiscussion,
  listDiscussions,
  regeneratePanel,
  startDiscussion,
} from './lib/api'
import type { DiscussionDetail, DiscussionSummary } from './lib/types'

/**
 * 路由壳。
 *
 * 这里只做「取数 + 把变更写回状态」，不渲染任何业务区块 ——
 * 三个页面各自负责自己的呈现。所有请求都走 lib/api，组件里没有一处 fetch。
 */
export function App() {
  const route = useRoute()
  const routeId = route.name === 'discussion' ? route.id : null

  const [list, setList] = useState<DiscussionSummary[]>([])
  const [listError, setListError] = useState<string | null>(null)
  const [detail, setDetail] = useState<DiscussionDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)
  const [pending, setPending] = useState<string | null>(null)

  const refreshList = useCallback(async () => {
    try {
      setList(await listDiscussions())
      setListError(null)
    } catch (err) {
      setListError(describeError(err))
    }
  }, [])

  useEffect(() => {
    void refreshList()
  }, [refreshList])

  // 换讨论就重新取快照。alive 守卫防的是"点了 A 又马上点 B，A 的响应后到"
  useEffect(() => {
    if (!routeId) {
      setDetail(null)
      setDetailError(null)
      return
    }
    let alive = true
    setLoadingDetail(true)
    getDiscussion(routeId)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        setDetailError(null)
      })
      .catch((err) => {
        if (!alive) return
        setDetail(null)
        setDetailError(describeError(err))
      })
      .finally(() => {
        if (alive) setLoadingDetail(false)
      })
    return () => {
      alive = false
    }
  }, [routeId])

  async function create(topic: string, expertCount: number) {
    setPending('create')
    try {
      const d = await createDiscussion(topic, expertCount)
      setDetail(d)
      setDetailError(null)
      navigate({ name: 'discussion', id: d.id })
      void refreshList()
    } catch (err) {
      setListError(describeError(err))
    } finally {
      setPending(null)
    }
  }

  async function start(id: string) {
    setPending('start')
    try {
      await startDiscussion(id)
      setDetail((cur) => (cur ? { ...cur, status: 'LIVE' } : cur))
      void refreshList()
    } catch (err) {
      setDetailError(describeError(err))
    } finally {
      setPending(null)
    }
  }

  async function regenerate(id: string) {
    setPending('regenerate')
    try {
      setDetail(await regeneratePanel(id))
    } catch (err) {
      setDetailError(describeError(err))
    } finally {
      setPending(null)
    }
  }

  /**
   * 流里出现 ended 时把状态收口，列表页的角标才不会停在"直播中"。
   *
   * 这里要重新取一次快照：`final_summary` 与 `ended_reason` 是引擎在收尾时
   * 才写库的，内存里的 detail 还停在开演那一刻。不重取，收尾段落永远是空的。
   */
  const onEnded = useCallback(async () => {
    setDetail((cur) => (cur ? { ...cur, status: 'FINISHED' } : cur))
    void refreshList()
    if (!routeId) return
    try {
      const fresh = await getDiscussion(routeId)
      setDetail((cur) => (cur && cur.id === fresh.id ? fresh : cur))
    } catch {
      // 快照取不到不影响已经播完的内容，界面维持现状
    }
  }, [refreshList, routeId])

  if (route.name === 'discussion') {
    if (detailError) {
      return (
        <div className="notfound">
          <h1>这场讨论打不开</h1>
          <p>{detailError}</p>
          <button className="btn btn-primary" onClick={() => navigate({ name: 'lobby' })}>
            回到列表
          </button>
        </div>
      )
    }

    if (!detail || (loadingDetail && detail.id !== routeId)) {
      return (
        <div className="notfound">
          <p className="loading">正在读取讨论…</p>
        </div>
      )
    }

    if (detail.status === 'AWAITING_CONFIRM' || detail.status === 'GENERATING') {
      return (
        <Confirm
          detail={detail}
          busy={pending}
          onStart={() => void start(detail.id)}
          onRegenerate={() => void regenerate(detail.id)}
        />
      )
    }

    return <Studio detail={detail} onEnded={onEnded} />
  }

  return (
    <Lobby
      discussions={list}
      error={listError}
      busy={pending === 'create'}
      onCreate={(topic, count) => void create(topic, count)}
    />
  )
}
