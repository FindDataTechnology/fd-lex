// 导出面与"无路由依赖"的结构性守卫（spec web-shell-kit）。
//
// 读源码而不是靠运行时：一条依赖可以躺在永不执行的分支里，运行时抓不到。
// 注释与字符串先剥掉——判据是代码，不是散文。
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const KIT = join(__dirname, '..', '..', '..', 'web-kit')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      if (name === 'node_modules' || name === '__pycache__') continue
      walk(full, out)
    } else {
      out.push(full)
    }
  }
  return out
}

/** 剥掉注释与字符串再扫——注释里提到 useNavigate 不算违规。 */
function codeOnly(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ')
    .replace(/'(?:[^'\\]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\]|\\.)*"/g, '""')
    .replace(/`(?:[^`\\]|\\.)*`/g, '``')
}

const ROUTER_IMPORT = /from\s+['"](react-router(-dom)?|@tanstack\/router|wouter|next\/router)['"]/

describe('共享壳', () => {
  it('源码不引用任何路由库', () => {
    const offenders: string[] = []
    for (const file of walk(KIT)) {
      if (!/\.(ts|tsx)$/.test(file)) continue
      if (ROUTER_IMPORT.test(codeOnly(readFileSync(file, 'utf-8')))) offenders.push(relative(KIT, file))
    }
    expect(offenders).toEqual([])
  })

  it('也不直接调用 useNavigate/useRouter', () => {
    const offenders: string[] = []
    for (const file of walk(KIT)) {
      if (!/\.(ts|tsx)$/.test(file)) continue
      if (/\buse(Navigate|Router|Location)\s*\(/.test(codeOnly(readFileSync(file, 'utf-8')))) {
        offenders.push(relative(KIT, file))
      }
    }
    expect(offenders).toEqual([])
  })

  it('导出面只暴露已登记的子模块符号（新增模块须显式登记）', () => {
    const text = readFileSync(join(KIT, 'index.ts'), 'utf-8')
    const re = /export\s+(?:type\s+)?\{[^}]+\}\s+from\s+'([^']+)'/g
    const sources = new Set<string>()
    for (const m of text.matchAll(re)) sources.add(m[1])
    expect([...sources].sort()).toEqual([
      './auth/index',
      './i18n/index',
      './i18n/react',
      './shell/Layout',
      './transport/index',
    ])
  })
})
