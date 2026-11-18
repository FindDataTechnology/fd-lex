// 营销面副驾宿主（line-reception-teams 2.x）：与 `console/WanxingCopilot.tsx` 同一形状，
// 但绑的是**版面限定 id** `lex-marketing`——`lex` 那条绑定仍归控制台
// （那里的 navigate/refresh 动词是控制台操作面的一部分，不能被产品介绍人设顶掉）。
//
// 本文件只做三件事：声明宿主钩子（页面上下文 / 导航白名单）、按需注入 loader、
// 卸载时清干净。协议全在 loader 里，宿主不写 postMessage。
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { WANXING_ORIGIN } from '../console/config'

const LINE = 'lex-marketing'

// 营销页唯一有意义的动词是站内导航；白名单按路由前缀收敛到转化路径。
const NAV_TARGETS = ['/console', '/deploy', '/docs'] as const

function allowed(to: string): boolean {
  if (to.startsWith('#') && !to.startsWith('//')) return true
  if (!to.startsWith('/') || to.startsWith('//')) return false
  return NAV_TARGETS.some((t) => to === t || to.startsWith(`${t}/`) || to.startsWith(`${t}#`))
}

type CopilotApi = {
  context?: () => Record<string, unknown>
  intents?: Record<string, (arg: unknown) => unknown>
  pushContext?: () => void
  __loaderMounted?: boolean
}

export function MarketingCopilot() {
  const navigate = useNavigate()
  const location = useLocation()

  // 钩子每次渲染刷新：navigate 闭包要跟住路由器，路径要跟住当前路由。
  useEffect(() => {
    const api: CopilotApi = ((window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot ??= {})
    api.context = () => ({ line: '识律', page: location.pathname + location.hash, intent: '产品介绍' })
    api.intents = {
      navigate: (arg: unknown) => {
        const to = String(arg ?? '')
        if (!allowed(to)) {
          throw new Error(`not allowed: navigate 只接受站内路径或锚点（${NAV_TARGETS.join(' ')}）`)
        }
        if (to.startsWith('#')) {
          window.location.hash = to
          return { message: `已跳转到 ${to}` }
        }
        navigate(to)
        return { message: `已导航到 ${to}` }
      },
    }
    api.pushContext?.()
  }, [navigate, location.pathname, location.hash])

  useEffect(() => {
    // 挂载侧开关（line-reception-copilot「挂载与绑定分离」）：置 'off' 即撤下本站
    // 副驾，无需改绑定、无需改代码重发版。撤挂载与解绑因此是两条独立回滚路径。
    if (String(import.meta.env.VITE_COPILOT_MOUNT ?? '').toLowerCase() === 'off') return
    const api = ((window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot ??= {})
    if (document.querySelector('iframe[data-wanxing-copilot]')) return

    const script = document.createElement('script')
    script.src = `${WANXING_ORIGIN}/copilot.js`
    script.async = true
    script.setAttribute('data-line', LINE)
    // 首屏只有 48px 圆钮：营销页不该一进门就弹 420×640 面板。
    script.setAttribute('data-open', '0')
    document.head.appendChild(script)

    return () => {
      document.querySelector('iframe[data-wanxing-copilot]')?.remove()
      script.remove()
      api.__loaderMounted = false
      delete (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot
    }
  }, [])

  return null
}
