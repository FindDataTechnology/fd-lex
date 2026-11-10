// 主题双态哨兵（change lex-web-shell-convergence 5.4）
//
// 这组测试刻意**不测"看起来好看"**，只测三件能被机器判死的事：
//   1. **令牌全量**：`primitives/*.tsx` 里引用的每个 `--fd-*` 变量，在 tokens.css 的
//      亮色组与暗色组里都必须有值。少一个 → 暗色下静默回退到内联兜底（亮色），
//      表现为"暗色下这一块还是白的"，人眼很难归因。
//   2. **暗色 ≠ 亮色**：把亮色值原样复制到暗色组（最常见的偷懒）会让双态形同虚设。
//   3. **不偷偷跟随系统**：tokens.css 不得出现 `prefers-color-scheme`。
//      主题是产品决定，不能让用户的 OS 设置悄悄改站点配色。
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

import { applyTheme, clearTheme, readTheme, type ThemeRoot } from './primitives/index'

const KIT = join(__dirname, '..', 'kit', 'primitives')
const TOKENS = readFileSync(join(KIT, 'tokens.css'), 'utf8')

/** 从 tokens.css 里抠出某个选择器块内的自定义属性。 */
function varsIn(selector: string): Record<string, string> {
  const idx = TOKENS.indexOf(selector)
  if (idx < 0) return {}
  const open = TOKENS.indexOf('{', idx)
  const close = TOKENS.indexOf('}', open)
  const body = TOKENS.slice(open + 1, close)
  const out: Record<string, string> = {}
  for (const m of body.matchAll(/(--fd-[a-z-]+)\s*:\s*([^;]+);/g)) {
    out[m[1]] = m[2].trim()
  }
  return out
}

/** 原语源码里引用的全部 `--fd-*` 变量（排除 `--fd-status-` 这种截断片段）。 */
function referencedVars(): string[] {
  const out = new Set<string>()
  for (const file of ['status-pill.tsx', 'states-kit.tsx']) {
    const src = readFileSync(join(KIT, file), 'utf8')
    for (const m of src.matchAll(/var\((--fd-[a-z-]+)/g)) out.add(m[1])
  }
  return [...out].sort()
}

const LIGHT = varsIn(':root,\n[data-theme=\'light\']') || varsIn('[data-theme=\'light\']')
const DARK = varsIn('[data-theme=\'dark\']')

describe('令牌全量', () => {
  it('原语引用的变量都不是截断片段', () => {
    const refs = referencedVars()
    expect(refs.length).toBeGreaterThan(10)
    for (const v of refs) {
      expect(v.endsWith('-'), `疑似截断的变量名：${v}`).toBe(false)
    }
  })

  it('每个被引用的变量在亮色组与暗色组里都有定义', () => {
    const refs = referencedVars()
    const missingLight = refs.filter((v) => !(v in LIGHT))
    const missingDark = refs.filter((v) => !(v in DARK))
    expect(missingLight, `亮色组缺：${missingLight.join(', ')}`).toEqual([])
    expect(missingDark, `暗色组缺：${missingDark.join(', ')}`).toEqual([])
  })

  it('五档 tone × 三属性 = 15 个状态变量齐备', () => {
    for (const tone of ['neutral', 'positive', 'warn', 'danger', 'info']) {
      for (const part of ['fg', 'border', 'bg']) {
        const name = `--fd-status-${tone}-${part}`
        expect(LIGHT, `亮色缺 ${name}`).toHaveProperty([name])
        expect(DARK, `暗色缺 ${name}`).toHaveProperty([name])
      }
    }
    expect(Object.keys(LIGHT).filter((k) => k.startsWith('--fd-status-')).length).toBe(15)
    expect(Object.keys(DARK).filter((k) => k.startsWith('--fd-status-')).length).toBe(15)
  })
})

describe('暗色不是抄亮色', () => {
  it('每个前景/边框色在亮暗两套里取值不同', () => {
    const same: string[] = []
    for (const key of Object.keys(LIGHT)) {
      if (!key.endsWith('-fg') && !key.endsWith('-border')) continue
      if (LIGHT[key] === DARK[key]) same.push(key)
    }
    expect(same, `这些变量亮暗同值（假双态）：${same.join(', ')}`).toEqual([])
  })

  it('透明底允许相同（neutral bg 就是 transparent，不是抄）', () => {
    expect(LIGHT['--fd-status-neutral-bg']).toBe(DARK['--fd-status-neutral-bg'])
  })
})

describe('主题由宿主决定，不跟随系统', () => {
  it('tokens.css 不含媒体查询式的自动跟随', () => {
    // 必须剥注释：本文件的注释正是在解释"为什么不用它"，直接查原文会自撞。
    // （同一个坑在 primitives.test.tsx 的 i18n 检查上踩过一次。）
    const code = TOKENS.replace(/\/\*[\s\S]*?\*\//g, '')
    expect(code).not.toMatch(/prefers-color-scheme/)
    expect(code).not.toMatch(/@media\s*\([^)]*color-scheme/)
  })

  it('applyTheme / readTheme / clearTheme 行为', () => {
    const attrs: Record<string, string> = {}
    const root: ThemeRoot = {
      setAttribute: (k, v) => {
        attrs[k] = v
      },
      getAttribute: (k) => attrs[k] ?? null,
      removeAttribute: (k) => {
        delete attrs[k]
      },
    }

    expect(readTheme(root)).toBeNull() // 未表态 != light
    expect(applyTheme(root, 'dark')).toBeNull()
    expect(readTheme(root)).toBe('dark')
    expect(applyTheme(root, 'light')).toBe('dark') // 返回旧值，便于回滚
    expect(readTheme(root)).toBe('light')
    clearTheme(root)
    expect(readTheme(root)).toBeNull()
  })

  it('非法属性值不被读成主题', () => {
    const root: ThemeRoot = {
      setAttribute: (k, v) => Object.assign(attrs, { [k]: v }),
      getAttribute: (k) => (attrs as Record<string, string>)[k] ?? null,
    }
    const attrs: Record<string, string> = {}
    root.setAttribute('data-theme', 'sepia')
    expect(readTheme(root)).toBeNull()
  })
})
