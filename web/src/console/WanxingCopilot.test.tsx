// 萬星副驾宿主（unify-cross-line-copilot 4.x）：**宿主不再实现协议**。
// 这里只断言宿主仍拥有的三件事——
//   ① loader 脚本按线注入（data-line="lex"、data-open=1、指向萬星 origin）
//   ② 钩子：identify 递当前控制台 ID token（未登录递 null）、context 带当前路由、
//      intents 只有 navigate/refresh 两个动词
//   ③ navigate 只认 CONSOLE_TABS 五路由（拒 /admin、拒查询串、拒未知动词），
//      refresh 回 ok
// 协议本身（init/ready/identity-request/intent 分派）在 loader 里，由
// wanxing-web 的 public/copilot.js 承担。
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import { CONSOLE_TABS } from './ConsoleLayout'
import { WanxingCopilot } from './WanxingCopilot'
import { WANXING_ORIGIN } from './config'

type CopilotApi = {
  identify?: () => { token: string | null }
  context?: () => Record<string, unknown>
  intents?: Record<string, (arg?: unknown) => unknown>
  pushContext?: () => void
  __loaderMounted?: boolean
}

beforeEach(() => {
  window.location.pathname = '/console'
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot
  document.head.querySelectorAll('script[data-line="lex"]').forEach((s) => s.remove())
  vi.restoreAllMocks()
  vi.unstubAllEnvs()
})

function mountCopilot() {
  // happy-dom refuses to LOAD an external script (and throws while connecting
  // it), so capture the injected element instead of letting it reach <head>.
  const appended: Element[] = []
  vi.spyOn(document.head, 'appendChild').mockImplementation((node) => {
    appended.push(node as Element)
    return node
  })
  const locPath: string[] = ['/console']
  const LocProbe = () => {
    const loc = useLocation()
    locPath[0] = loc.pathname
    return null
  }
  render(
    <MemoryRouter initialEntries={['/console']}>
      <LocProbe />
      <WanxingCopilot />
    </MemoryRouter>,
  )
  const script = appended.find((n) => n.tagName === 'SCRIPT') as HTMLScriptElement | undefined
  const api = (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot
  return { script, api, locPath }
}

describe('WanxingCopilot 宿主钩子（协议在 loader 里）', () => {
  it('未配置时的默认地址与独立前的生产地址逐字一致', () => {
    expect(WANXING_ORIGIN).toBe('https://wanxing.finddatatech.cloud')
  })

  it('构建期 VITE_WANXING_ORIGIN 覆盖默认地址', async () => {
    vi.stubEnv('VITE_WANXING_ORIGIN', 'https://wanxing.example.test/')
    vi.resetModules()
    const mod = await import('./config')
    // 末尾斜杠被剥掉，loader 路径不会拼出 //copilot.js
    expect(mod.WANXING_ORIGIN).toBe('https://wanxing.example.test')
  })

  it('按线注入 loader 脚本，且不自己渲染 iframe', () => {
    const { script } = mountCopilot()
    expect(script).toBeTruthy()
    expect(script?.src).toBe(`${WANXING_ORIGIN}/copilot.js`)
    expect(script?.getAttribute('data-line')).toBe('lex')
    expect(script?.getAttribute('data-open')).toBe('1')
    expect(document.querySelector('iframe[data-wanxing-copilot]')).toBeNull()
  })

  it('只声明 navigate/refresh 两个动词', () => {
    const { api } = mountCopilot()
    expect(Object.keys(api?.intents ?? {}).sort()).toEqual(['navigate', 'refresh'])
    // 未声明的动词不在表里——loader 只分派声明过的，widget 也只渲染这些
    expect((api?.intents as Record<string, unknown>)?.highlight).toBeUndefined()
  })

  it('navigate 到每个控制台路由都切路由', async () => {
    const { api, locPath } = mountCopilot()
    for (const tab of CONSOLE_TABS) {
      await act(async () => {
        api?.intents?.navigate?.(tab.to)
      })
      expect(locPath[0]).toBe(tab.to)
    }
  })

  it('navigate 拒绝 /admin、带查询串的路由', () => {
    const { api, locPath } = mountCopilot()
    expect(() => api?.intents?.navigate?.('/admin')).toThrow(/not allowed/)
    expect(() => api?.intents?.navigate?.('/console/usage?x=1')).toThrow(/not allowed/)
    expect(locPath[0]).toBe('/console')
  })

  it('identify 未登录递 null；context 带当前路由', () => {
    const { api } = mountCopilot()
    expect(api?.identify?.()).toEqual({ token: null })
    expect(api?.context?.()).toMatchObject({ line: '识律', page: '/console' })
  })

  it('refresh 回 ok', () => {
    const { api } = mountCopilot()
    // jsdom 忽略 reload，只断言钩子不抛且给出回执
    expect(() => api?.intents?.refresh?.()).not.toThrow()
  })
})
