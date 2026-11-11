#!/usr/bin/env node
/**
 * 5.4b 的**页面级**暗色证据（补齐 verify_dark_tokens.js 只到机制层的缺口）。
 *
 * 缺口原文（change lex-web-shell-convergence，5.4b）：
 *   「StabilityBadge 已改为只传 tone（走变量），亮色 0 差异；但该徽标只在有数据的面板里
 *     渲染，当前环境无数据 → 真实页面里 [data-tone] 命中 0，故『暗色下徽标变色』只有
 *     机制证据、无页面证据。」
 *
 * 本脚本的做法：**不改生产代码、不加 mock 开关**，只在浏览器层用 playwright 路由
 * 拦截 `/api/v1/cards`，喂一份最小夹具（三张卡各一种 stability），让真实面板把
 * 真实组件渲染出来，然后：① 断言页面上真的存在 `[data-tone]`（>0）；
 * ② 逐个读 computed 前景/背景/边框色；③ 切 `data-theme=dark` 再读一遍 → 必须不同。
 *
 * 为什么用路由拦截而不是往仓里塞 mock 数据层：
 *   mock 数据层会成为生产代码的一部分（多一条只在测试时才走的分支，且要长期维护）；
 *   路由拦截只存在于验证时，生产构建一个字节都不变。两者证的东西一样：**真实组件**。
 *
 * 边界（别把它读成更多）：
 *   · 只证 fd-zhesi 的 StabilityBadge（唯一只传 tone 的宿主组件）。fd-shilun 的
 *     CitationBadge/WarnBadge/VerifyStatusBadge 仍传 `toneClassName`（宿主显式配色），
 *     **按设计不随主题变**——要它们变，得把 toneClassName 也删掉，那是产品决定，不在本轮。
 *   · 只证徽标变色，不证整页暗色版式（页面背景仍是宿主的亮色 bg-neutral-50）。
 *
 * 自证（别跳过）：`--self-test` 会在切暗色前注入一条把 `[data-tone]` 颜色钉死的
 * 内联样式（模拟"配色不受令牌驱动"），此时脚本**必须**判红，否则说明它无论怎样都会绿。
 *
 * 用法：node fd-lex/scripts/verify_dark_badges.js [--out <截图目录>] [--self-test]
 * 前置：fd-zhesi/web/dist 已构建。
 */
const path = require('node:path')
const fs = require('node:fs')
const http = require('node:http')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
const PW = path.join(REPO_ROOT, 'paas', 'node_modules', 'playwright')
const DIST = path.join(REPO_ROOT, 'fd-zhesi', 'web', 'dist')

const VIEWPORT = { width: 1440, height: 900 }

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`)
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : def
}

// 最小夹具：三张卡，覆盖三种 stability（会渲染成 positive / warn / neutral）
const NOW = '2026-11-11T00:00:00Z'
const CARDS = [
  {
    id: 'card-1', question: '举证责任在谁？', question_ref: null,
    position: '由主张方承担。', reasons: '谁主张谁举证。',
    strongest_objection: null, my_response: null,
    status: 'active', stability: 'stable', created_at: NOW, updated_at: NOW, sealed_at: null,
  },
  {
    id: 'card-2', question: '合同解除是否需催告？', question_ref: null,
    position: '原则需要，例外不需要。', reasons: '法定解除与约定解除不同。',
    strongest_objection: null, my_response: null,
    status: 'active', stability: 'wavering', created_at: NOW, updated_at: NOW, sealed_at: null,
  },
  {
    id: 'card-3', question: '违约金能否酌减？', question_ref: null,
    position: '可以。', reasons: '过高时法院可调。',
    strongest_objection: null, my_response: null,
    status: 'active', stability: 'shelved', created_at: NOW, updated_at: NOW, sealed_at: null,
  },
]

const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
  '.woff': 'font/woff', '.woff2': 'font/woff2', '.ico': 'image/x-icon',
}

/** SPA fallback 静态服务（同一台机器上 python http.server 会对 /workbench 直接 404）。 */
function serve(dir) {
  const srv = http.createServer((req, res) => {
    let file = path.join(dir, decodeURIComponent((req.url || '/').split('?')[0]))
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dir, 'index.html')
    try {
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' })
      res.end(fs.readFileSync(file))
    } catch {
      res.writeHead(404).end('not found')
    }
  })
  return new Promise((resolve) => {
    srv.listen(0, '127.0.0.1', () => {
      resolve({ url: `http://127.0.0.1:${srv.address().port}`, stop: () => srv.close() })
    })
  })
}

async function main() {
  let chromium
  try {
    ;({ chromium } = require(PW))
  } catch {
    console.error(`找不到 playwright：${PW}\n  装法：cd paas && npm i playwright`)
    return 1
  }
  if (!fs.existsSync(DIST)) {
    console.error(`缺 dist：${DIST} —— 先 cd fd-zhesi/web && npm run build（不跳过：'没构建' ≠ '无色差'）`)
    return 1
  }
  const selfTest = process.argv.includes('--self-test')
  const outDir = path.resolve(arg('out', path.join(REPO_ROOT, 'openspec', 'changes', 'archive',
    '2026-11-10-lex-web-shell-convergence', 'screenshots', '5.4b-dark')))
  fs.mkdirSync(outDir, { recursive: true })

  const srv = await serve(DIST)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 1 })

  // 路由拦截：cards 给夹具，其余 /api/** 快速 404（wired 端点会落到「接线中」降级，不阻塞加载）
  await page.route('**/api/**', async (route) => {
    const url = route.request().url()
    const cors = {
      'content-type': 'application/json; charset=utf-8',
      'access-control-allow-origin': '*',
      'access-control-allow-headers': '*',
      'access-control-allow-methods': '*',
    }
    if (route.request().method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: cors, body: '' })
    }
    if (/\/api\/v1\/cards(\?|$)/.test(url)) {
      return route.fulfill({ status: 200, headers: cors, body: JSON.stringify({ results: CARDS }) })
    }
    return route.fulfill({ status: 404, headers: cors, body: JSON.stringify({ detail: 'mock: 无此端点' }) })
  })
  // 外部 OIDC 一律拦掉：本脚本不验登录，别让外网拖慢或干扰计时
  await page.route('**auth.finddatatech.cloud/**', (route) => route.abort())

  let ok = true
  try {
    await page.goto(`${srv.url}/workbench`, { waitUntil: 'domcontentloaded', timeout: 30000 })
    // 切到「辩」tab（StabilityBadge 只在这个面板里）
    const debateTab = page.getByRole('tab', { name: /辩|debate/i })
    await debateTab.click({ timeout: 15000 })

    // 等徽标出现；不睡固定时长，等到就继续
    await page.waitForSelector('[data-tone]', { timeout: 15000 })

    if (selfTest) {
      // 负例：把徽标颜色钉死（!important 胜过变量），模拟"配色不由令牌驱动"。
      // **必须在亮色探测之前注入**——注在之后会让亮色保持真值、暗色变成钉死值，
      // 两者当然不同，于是负例反而判绿（2026-11-11 实测踩到，这正是"负例自己也要验证"）。
      await page.addStyleTag({
        content: '[data-tone]{color:rgb(1,2,3)!important;background-color:rgb(1,2,3)!important;border-color:rgb(1,2,3)!important}',
      })
      console.log('  [self-test] 已注入钉色样式（两侧同源），下面应判红')
    }

    const probe = () =>
      page.evaluate(() => {
        const els = Array.from(document.querySelectorAll('[data-tone]'))
        const uniq = []
        const seen = new Set()
        for (const el of els) {
          const tone = el.getAttribute('data-tone')
          if (seen.has(tone)) continue
          seen.add(tone)
          const cs = getComputedStyle(el)
          uniq.push({
            tone,
            text: (el.textContent || '').trim(),
            color: cs.color,
            background: cs.backgroundColor,
            border: cs.borderTopColor,
          })
        }
        return { count: els.length, uniq }
      })

    const light = await probe()
    await page.screenshot({ path: path.join(outDir, 'zhesi-workbench-debate-light.png'), fullPage: true })

    // 切暗色：走真实机制（data-theme），不注入任何 CSS
    await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
    await page.waitForTimeout(120) // 让样式重算（不能省：computed 读的是重算后的值）
    const dark = await probe()
    await page.screenshot({ path: path.join(outDir, 'zhesi-workbench-debate-dark.png'), fullPage: true })

    console.log(`  页面 [data-tone] 命中：${light.count} 个，去重后 ${light.uniq.length} 种 tone`)
    if (light.count === 0) {
      console.log('  ✗ 徽标没渲染出来 —— 本页无暗色页面证据（夹具或 tab 选择器失效）')
      ok = false
    }

    const darkByTone = new Map(dark.uniq.map((u) => [u.tone, u]))
    console.log(`  ${'tone'.padEnd(10)}${'light bg'.padEnd(22)}${'dark bg'.padEnd(22)}结论`)
    for (const l of light.uniq) {
      const d = darkByTone.get(l.tone)
      // 只要前景/背景/边框任一不同，就算"暗色生效"；三者全同就是没生效
      const differs = d && (d.color !== l.color || d.background !== l.background || d.border !== l.border)
      if (!differs) ok = false
      console.log(
        `  ${l.tone.padEnd(10)}${(l.background + ' ' + l.color).padEnd(22)}` +
          `${d ? d.background + ' ' + d.color : '（未取到）'}`.padEnd(22) +
          `${differs ? '✓ 变色' : '✗ 未变（仍是亮色值）'}`,
      )
    }

    const onlyNeutralBgTransparent = light.uniq.every(
      (l) => l.tone !== 'neutral' || l.background === 'rgba(0, 0, 0, 0)' || l.background === 'transparent',
    )
    if (!onlyNeutralBgTransparent) {
      // neutral 的 bg 在令牌里是 transparent，若这里不透明说明走的不是变量路径
      console.log('  ? neutral 徽标背景非 transparent —— 可能仍在走宿主类名而非变量')
    }
  } finally {
    await browser.close()
    srv.stop()
  }

  console.log('')
  if (selfTest) {
    // 自证模式：判红才是对的
    console.log(!ok
      ? '✓ 负例被正确判红（钉住颜色后脚本不再声称暗色生效）—— 说明上面的绿是有分辨力的'
      : '✗ 负例竟然判绿：本脚本无论配色是否受令牌驱动都会通过，不能作为证据')
    return !ok ? 0 : 1
  }
  console.log(ok
    ? `✓ 页面级暗色证据成立：真实面板渲染出 [data-tone] 徽标，切 data-theme 后配色随令牌变化（截图 ${outDir}）`
    : `✗ 页面级暗色证据不成立，见上（截图 ${outDir}）`)
  return ok ? 0 : 1
}

main().then((c) => process.exit(c)).catch((e) => {
  console.error(e)
  process.exit(1)
})
