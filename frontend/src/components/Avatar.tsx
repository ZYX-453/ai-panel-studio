interface AvatarProps {
  name: string
  color: string
  size?: number
  /** 发言中时加一圈呼吸光晕，纯 CSS，不占用布局 */
  speaking?: boolean
}

/**
 * 头像用姓名首字 + 专属色，不依赖图片资源。
 * 首屏要 2 秒内出来，多一个请求就多一份风险，所以不引任何图标/图片库。
 */
export function Avatar({ name, color, size = 36, speaking = false }: AvatarProps) {
  return (
    <span
      className={`avatar${speaking ? ' is-speaking' : ''}`}
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * 0.42),
        background: `color-mix(in srgb, ${color} 14%, #fff)`,
        color,
        borderColor: `color-mix(in srgb, ${color} 38%, #fff)`,
      }}
      aria-hidden="true"
    >
      {name.slice(0, 1)}
    </span>
  )
}
