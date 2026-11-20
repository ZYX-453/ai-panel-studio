/**
 * 展示层的格式化。
 *
 * 后端存的是 ISO 字符串（`2026-11-19T18:30:00`），直接铺到界面上
 * 会出现「2026-11-19T18:30:00」这种既长又难扫的文本。
 * 这里只做截取，不做时区换算 —— 后端写的就是本地时间，
 * 再用 `new Date()` 解析反而会因为时区把日期挪一天。
 */

const STAMP = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/

/** 列表用：`11-19 18:30` */
export function formatStamp(iso: string): string {
  const m = STAMP.exec(iso)
  if (!m) return iso || '—'
  return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`
}

/** 单条发言用：`18:30` */
export function formatClock(iso: string): string {
  const m = STAMP.exec(iso)
  if (!m) return ''
  return `${m[4]}:${m[5]}`
}
