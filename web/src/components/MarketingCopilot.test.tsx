// 营销面副驾宿主（line-reception-teams 2.1/2.2）：宿主侧只有三件事可断言——
//   ① loader 按版面 id `lex-marketing` 注入，且**默认收起**（data-open="0"）
//   ② context 带当前路径
//   ③ navigate 白名单：站内三个转化路由与站内锚点放行；站外 URL 与 `javascript:`
//      必须被拒（导航是特权动作）
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { MarketingCopilot } from './MarketingCopilot'

type CopilotApi = {
  context?: () => Record<string, unknown>
  intents?: Record<string, (arg?: unknown) => unknown>
  pushContext?: () => void
  __loaderMounted?: boolean
}

const api = () => (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot

let appended: Element[]

beforeEach(() => {
  appended = []
  vi.spyOn(document.head, 'appendChild').mockImplementation((node) => {
    appended.push(node as Element)
    return node
  })
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { WanxingCopilot?: CopilotApi }).WanxingCopilot
  vi.restoreAllMocks()
})

function mount(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MarketingCopilot />
    </MemoryRouter>,
  )
}

const loader = () => appended.find((n) => (n as HTMLScriptElement).src?.endsWith('/copilot.js')) as HTMLScriptElement | undefined

describe('MarketingCopilot 宿主钩子', () => {
  it('按版面 id 注入 loader 且默认收起', () => {
    mount()
    const s = loader()
    expect(s).toBeTruthy()
    expect(s!.getAttribute('data-line')).toBe('lex-marketing')
    expect(s!.getAttribute('data-open')).toBe('0')
  })

  it('context 递当前路径', () => {
    mount('/deploy')
    expect(api()?.context?.()).toMatchObject({ page: '/deploy' })
  })

  it('navigate 放行站内转化路由与锚点', () => {
    mount()
    const navigate = api()?.intents?.navigate!
    expect(() => navigate('/console')).not.toThrow()
    expect(() => navigate('/docs/quickstart')).not.toThrow()
    expect(() => navigate('/deploy')).not.toThrow()
    expect(() => navigate('#pricing')).not.toThrow()
  })

  it('navigate 拒绝站外 URL 与 javascript: 协议', () => {
    mount()
    const navigate = api()?.intents?.navigate!
    expect(() => navigate('https://evil.example/x')).toThrow()
    expect(() => navigate('//evil.example')).toThrow()
    expect(() => navigate('javascript:alert(1)')).toThrow()
    // 站内但不在白名单（避免把副驾变成任意路由跳板）
    expect(() => navigate('/admin')).toThrow()
  })
})

describe('挂载可独立回滚（line-reception-copilot「挂载与绑定分离」）', () => {
  it('VITE_COPILOT_MOUNT=off 时不注入 loader，绑定侧不受影响', () => {
    vi.stubEnv('VITE_COPILOT_MOUNT', 'off')
    mount()
    expect(loader()).toBeUndefined()
  })

  it('默认（未设开关）仍然注入', () => {
    vi.stubEnv('VITE_COPILOT_MOUNT', '')
    mount()
    expect(loader()).toBeTruthy()
  })
})
