// 萬星副驾宿主（unify-cross-line-copilot 4.x）：本文件现在只做三件事——
//   ① 声明宿主钩子（身份 / 页面上下文 / 页面动作白名单）
//   ② 注入 loader 脚本（data-line="lex"），协议全在 loader 里
//   ③ 卸载时清掉 widget 与钩子，让 StrictMode 双挂载 / 布局重挂不会留两份
//
// 旧实现（124 行手写 postMessage 协议 + 硬编码 WIDGET_SLUG）已删除：副驾是哪个
// agent 由萬星控制台的「线副驾」绑定解析，换部署不再改宿主源码。
//
// 挂在 ConsoleLayout（每页一份）。意图白名单仍然归宿主：navigate 只接受控制台
// 页面路由（CONSOLE_TABS，无查询串、无 /admin），refresh 整页重载。
import { useEffect } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { CONSOLE_TABS } from './ConsoleLayout'
import { loadSession } from './auth'
import { WANXING_ORIGIN } from './config'

const LINE = 'lex'

// 动词表在钩子里声明给 loader——widget 只渲染已声明的动词。
// CONSOLE_TABS 惰性求值：本组件与 ConsoleLayout 互为循环引用，模块顶层取值会
// 踩 TDZ；意图只会在页面跑起来之后到达，届时早已初始化。
let navigateTargets: Set<string> | null = null
const navTargets = () => (navigateTargets ??= new Set(CONSOLE_TABS.map((t) => t.to)))

type CopilotApi = {
  identify?: () => { token: string | null }
  context?: () => Record<string, unknown>
  intents?: Record<string, (arg: unknown) => unknown>
  pushContext?: () => void
  onEvent?: (type: string, detail: Record<string, unknown>) => void
  __loaderMounted?: boolean
}

export function WanxingCopilot() {
  const navigate = useNavigate()
  const location = useLocation()

  // 钩子每次渲染刷新：navigate 闭包要跟住路由器实例，路径要跟住当前路由。
  useEffect(() => {
    const api: CopilotApi = ((window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot ??= {})
    api.identify = () => ({ token: loadSession()?.idToken ?? null })
    api.context = () => ({ line: '识律', page: location.pathname, intent: '控制台操作伴随' })
    api.intents = {
      navigate: (arg: unknown) => {
        const to = String(arg ?? '')
        if (!navTargets().has(to)) {
          throw new Error(`not allowed: navigate 只接受控制台页面路由（${[...navTargets()].join(' ')}）`)
        }
        navigate(to)
        return { message: `已导航到 ${to}` }
      },
      refresh: () => {
        // 当前路由整页重载——最朴素且必然正确的「重拉本页数据」。
        setTimeout(() => window.location.reload(), 120)
        return { message: '刷新中' }
      },
    }
    // SPA 路由变化不会触发 hashchange/popstate，手动推一次上下文。
    api.pushContext?.()
  }, [navigate, location.pathname])

  // 挂载一次，卸载时清干净。
  useEffect(() => {
    const api = ((window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot ??= {})
    const existing = document.querySelector('iframe[data-wanxing-copilot]')
    if (existing) return // 已挂（布局重渲染）

    const script = document.createElement('script')
    script.src = `${WANXING_ORIGIN}/copilot.js`
    script.async = true
    script.setAttribute('data-line', LINE)
    script.setAttribute('data-open', '1')
    document.head.appendChild(script)

    return () => {
      document.querySelector('iframe[data-wanxing-copilot]')?.remove()
      script.remove()
      // loader 的一次性守卫与宿主钩子一起清掉，下次挂载才会重新解析绑定。
      if (typeof api === 'object') api.__loaderMounted = false
      delete (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot
    }
  }, [])

  return null // widget 由 loader 注入，React 不渲染任何节点
}
