import { useEffect, useState } from 'react'

/**
 * 极简 hash 路由。
 *
 * 不用 react-router：整个应用只有三个视图，为三个视图引一个几十 KB 的
 * 依赖，会直接拖累首屏 FCP —— 而 FCP 2 秒是需求里的硬指标。
 */
export type Route =
  | { name: 'lobby' }
  | { name: 'discussion'; id: string }

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#/, '')
  const m = /^\/d\/([\w-]+)$/.exec(raw)
  if (m && m[1]) return { name: 'discussion', id: m[1] }
  return { name: 'lobby' }
}

export function href(route: Route): string {
  return route.name === 'lobby' ? '#/' : `#/d/${route.id}`
}

export function navigate(route: Route): void {
  window.location.hash = href(route)
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}
