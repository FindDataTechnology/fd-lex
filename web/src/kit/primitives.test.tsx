// 设计原语哨兵（change lex-web-shell-convergence 5.2 的降级实现）
//
// 这组测试守的不是"渲染出来好看"，而是抽取时最容易失守的四条：
//   1. **零外部依赖**：原语层不许 import clsx / tailwind-merge / radix ——
//      宿主没装它们，一旦引进来，替换就变成"先加依赖"，会被宿主合理拒绝；
//   2. **不接 i18n**：文案必须经 props 传入（5.1 纪律二）。原语里出现
//      `useTranslation` / `t(` 就说明它偷偷绑定了某套文案系统；
//   3. **调色板不内置**：宿主给了 toneClassName 就必须完全采用宿主的类名，
//      不再叠自己的颜色——否则"同一份实现服务三套调色板"是假的；
//   4. **a11y 不退化**：ErrorState 要有 role=alert，LoadingState 要有
//      role=status（宿主原来的自造实现多数没有，抽取时顺带补上）。
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { renderToStaticMarkup } from 'react-dom/server'

import { ErrorState, EmptyState, LoadingState, StatusPill, cn } from './primitives/index'

const KIT_DIR = join(__dirname, '..', 'kit')

describe('cn：零依赖类名拼接', () => {
  it('过滤假值并拼接', () => {
    expect(cn('a', false, null, undefined, 'b', '' as unknown as string)).toBe('a b')
  })

  it('不引 clsx / tailwind-merge', () => {
    // 同样剥注释：cn.ts 的注释正是在解释"为什么不装 tailwind-merge"
    const code = readFileSync(join(KIT_DIR, 'primitives', 'cn.ts'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/from ['"]clsx['"]/)
    expect(code).not.toMatch(/from ['"]tailwind-merge['"]/)
  })
})

describe('原语层零外部依赖、零 i18n', () => {
  const files = ['status-pill.tsx', 'states-kit.tsx', 'cn.ts', 'index.ts']

  /** 剥掉注释后再查：注释里写「本层不接 i18n」不该被判违规。 */
  const stripComments = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  for (const file of files) {
    it(`${file} 不引第三方 UI/类名库，不接 i18n`, () => {
      const code = stripComments(readFileSync(join(KIT_DIR, 'primitives', file), 'utf8'))
      // 只允许相对导入与 react 类型
      const imports = [...code.matchAll(/from\s+['"]([^'"]+)['"]/g)].map((m) => m[1])
      for (const spec of imports) {
        const ok = spec.startsWith('./') || spec === 'react'
        expect(ok, `${file} 引入了非白名单模块：${spec}`).toBe(true)
      }
      expect(code).not.toMatch(/useTranslation\s*\(/)
      expect(code).not.toMatch(/from\s+['"][^'"]*i18n/)
    })
  }
})

describe('StatusPill', () => {
  it('宿主注入的调色板类名被完整采用，不再叠自己的颜色', () => {
    const html = renderToStaticMarkup(
      <StatusPill tone="warn" toneClassName={{ warn: 'border-amber-300 bg-amber-50 text-amber-800' }}>
        需补证
      </StatusPill>,
    )
    expect(html).toContain('border-amber-300')
    expect(html).toContain('bg-amber-50')
    // 注入后不该再出现兜底的内联颜色
    expect(html).not.toContain('style=')
    expect(html).toContain('data-tone="warn"')
  })

  it('未注入时退化为 CSS 变量兜底（宿主零配置也能显示）', () => {
    const html = renderToStaticMarkup(<StatusPill tone="danger">失败</StatusPill>)
    expect(html).toContain('--fd-status-danger-border')
    expect(html).toContain('data-tone="danger"')
  })

  it('文案来自 props，图标由宿主给', () => {
    const html = renderToStaticMarkup(
      <StatusPill icon={<svg data-testid="dot" />}>双宿主</StatusPill>,
    )
    expect(html).toContain('双宿主')
    expect(html).toContain('data-testid="dot"')
  })
})

describe('StatesKit', () => {
  it('LoadingState 有 role=status（屏幕阅读器会播报）', () => {
    const html = renderToStaticMarkup(<LoadingState label="加载中…" />)
    expect(html).toContain('role="status"')
    expect(html).toContain('加载中…')
  })

  it('ErrorState 有 role=alert，并把 detail 附在文案后', () => {
    const html = renderToStaticMarkup(
      <ErrorState label="出了点问题" detail="500" hint="稍后重试" />,
    )
    expect(html).toContain('role="alert"')
    expect(html).toContain('出了点问题：500')
    expect(html).toContain('稍后重试')
  })

  it('ErrorState 给了 toneClassName 就不再用兜底色', () => {
    const html = renderToStaticMarkup(
      <ErrorState label="x" toneClassName="border-red-200 bg-red-50 text-red-800" />,
    )
    expect(html).toContain('bg-red-50')
    expect(html).not.toContain('--fd-error-bg')
  })

  it('EmptyState 不带 alert 角色（空态不是错误）', () => {
    const html = renderToStaticMarkup(<EmptyState>暂无数据</EmptyState>)
    expect(html).not.toContain('role="alert"')
    expect(html).toContain('暂无数据')
  })
})
