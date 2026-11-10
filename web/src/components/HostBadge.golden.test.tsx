// 7.3 视觉等价的机器可验部分：HostBadge 类名 golden（fd-lex）
//
// HostBadge 是本次唯一在 fd-lex 被替换的控件，且有 **host × variant = 4 种组合**
// （both/private × light/dark），每种对应一条不同的调色板类名串——所以四种都要单独验，
// 只验一种会漏掉另外三种的调色板丢失。
//
// golden = 改前 `HostBadge.tsx` 的类名字面量（`git show` 可复核）。
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

// HostBadge 是**默认导出**（`export default HostBadge`），不是命名导出 ——
// 命名导入会拿到 undefined，报 "Element type is invalid"（本轮实际踩到过）。
import HostBadge from './HostBadge'

function classesOf(html: string): Set<string> {
  const m = html.match(/class="([^"]*)"/)
  if (!m) throw new Error(`渲染结果里没有 class：${html.slice(0, 160)}`)
  return new Set(m[1].split(/\s+/).filter(Boolean))
}

function expectSameClasses(html: string, golden: string[]) {
  const got = classesOf(html)
  const want = new Set(golden)
  const missing = [...want].filter((c) => !got.has(c))
  const extra = [...got].filter((c) => !want.has(c))
  expect({ missing, extra }).toEqual({ missing: [], extra: [] })
}

/** 改前的固定前缀（原实现写死在模板串里）。 */
const BASE = ['inline-flex', 'items-center', 'gap-1.5', 'rounded-full', 'border', 'px-2.5', 'py-0.5', 'text-xs', 'font-medium']

describe('7.3 golden：HostBadge 四种组合的 class 集合不变（fd-lex）', () => {
  it('both × light', () => {
    expectSameClasses(renderToStaticMarkup(<HostBadge host="both" />), [
      ...BASE,
      'border-line-strong',
      'text-ink-soft',
    ])
  })

  it('both × dark（hero / 页脚的深色场地）', () => {
    expectSameClasses(renderToStaticMarkup(<HostBadge host="both" variant="dark" />), [
      ...BASE,
      'border-white/30',
      'text-white/80',
    ])
  })

  it('private × light', () => {
    expectSameClasses(renderToStaticMarkup(<HostBadge host="private" />), [
      ...BASE,
      'border-seal/40',
      'bg-seal-soft',
      'text-seal',
    ])
  })

  it('private × dark', () => {
    expectSameClasses(renderToStaticMarkup(<HostBadge host="private" variant="dark" />), [
      ...BASE,
      'border-[#d98a76]/40',
      'bg-seal/25',
      'text-[#f0c3b6]',
    ])
  })

  it('外部传入的 className 仍被追加（调用点定制不丢）', () => {
    const got = classesOf(renderToStaticMarkup(<HostBadge host="both" className="ml-2" />))
    expect(got.has('ml-2')).toBe(true)
    expect(got.has('text-ink-soft')).toBe(true) // 调色板仍在，没被 className 顶掉
  })
})
