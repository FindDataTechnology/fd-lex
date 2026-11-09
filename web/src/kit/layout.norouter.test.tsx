// 无 router 宿主可渲染（spec web-shell-kit「布局壳不假定宿主路由」）。
//
// 这是 PackMarketView 白屏事故的哨兵：在**没有 Router / 没有 Provider** 的树里
// 渲染 Layout 必须正常出内容。若有人在壳里加了 useNavigate()，React 会抛
// "useNavigate() may be used only in the context of a <Router>"，本文件实红。
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { Layout } from './shell/Layout'

afterEach(cleanup)

const NAV = [
  { key: 'home', label: '首页', href: '/', active: true },
  { key: 'console', label: '控制台', href: '/console' },
  { key: 'noop', label: '无动作' },
]

describe('Layout 在无路由上下文下', () => {
  it('可渲染品牌、导航、正文与页脚', () => {
    render(
      <Layout brand={<span>识律</span>} nav={NAV} footer={<small>备案</small>}>
        <p>正文</p>
      </Layout>,
    )
    expect(screen.getByText('识律')).toBeTruthy()
    expect(screen.getByText('首页')).toBeTruthy()
    expect(screen.getByText('控制台')).toBeTruthy()
    expect(screen.getByText('正文')).toBeTruthy()
    expect(screen.getByText('备案')).toBeTruthy()
  })

  it('宿主不注入 renderLink 时用原生 <a>，不抛错', () => {
    render(<Layout brand={<span>b</span>} nav={NAV}>{null}</Layout>)
    expect(screen.getByText('控制台').closest('a')?.getAttribute('href')).toBe('/console')
  })

  it('既无 href 也无 onClick 的项渲染为纯文本（不渲染成可点动作）', () => {
    render(<Layout brand={<span>b</span>} nav={NAV}>{null}</Layout>)
    const noop = screen.getByText('无动作')
    expect(noop.closest('a')).toBeNull()
    expect(noop.closest('button')).toBeNull()
  })

  it('宿主不提供 nav 时不渲染 nav 容器（不抛错、不留空壳）', () => {
    const { container } = render(<Layout brand={<span>b</span>}>{null}</Layout>)
    expect(container.querySelector('.shell__nav')).toBeNull()
    expect(container.querySelector('.shell__header')).toBeTruthy()
  })

  it('宿主不提供 footer/actions 时相应区域不渲染', () => {
    const { container } = render(<Layout brand={<span>b</span>}>{null}</Layout>)
    expect(container.querySelector('.shell__footer')).toBeNull()
    expect(container.querySelector('.shell__actions')).toBeNull()
  })

  it('注入 renderLink 时完全交给宿主（壳不猜路由）', () => {
    render(
      <Layout
        brand={<span>b</span>}
        nav={NAV.slice(0, 1)}
        renderLink={(item, content) => <em data-to={item.href}>{content}</em>}
      >
        {null}
      </Layout>,
    )
    const el = screen.getByText('首页')
    expect(el.tagName).toBe('EM')
    expect(el.getAttribute('data-to')).toBe('/')
  })
})
