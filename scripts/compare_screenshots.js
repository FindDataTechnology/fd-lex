#!/usr/bin/env node
/**
 * 7.3 视觉等价的像素级核验（change lex-web-shell-convergence）
 *
 * 为什么要它：7.3 原文要求"截图对比无明显结构差异"，此前只能靠人眼，我在报告里
 * 也只能写"未做"。这台机器上有 playwright + chromium（paas/node_modules），
 * 于是把它做成机器可判：**改前构建 vs 改后构建**，同视口、同页面、同等待条件截整页图，
 * 逐像素比对，超过阈值即判红。
 *
 * 边界（必须说清，否则会当成"视觉已过"）：
 *   · 只覆盖**同一数据**下的渲染差异。工作台类页面依赖后端，若无后端则只截首屏外壳，
 *     这类页面的结论只能算"外壳一致"，不等于"交互后一致"。
 *   · 字体渲染差异会被计入差异像素；同一台机器同一 chromium 上应可忽略，跨机器不可比。
 *   · 它证明的是"没变"，**不证明"变好/好看"**——那仍然只有人眼能判。
 *
 * 用法：
 *   node scripts/compare_screenshots.js --host fd-lex --base <commit> --current <dir>
 *   node scripts/compare_screenshots.js --self-test      # 用同一目录自比，必须 0 差异
 */
const path = require('node:path')
const fs = require('node:fs')
const { execFileSync } = require('node:child_process')

const REPO_ROOT = path.resolve(__dirname, '..', '..')
// playwright 不在本仓依赖里，借 paas 的（同一台机器上唯一的可用副本）
const PW = path.join(REPO_ROOT, 'paas', 'node_modules', 'playwright')

const HOSTS = {
  'fd-lex': { repo: 'fd-lex', web: 'web', config: 'tsconfig.app.json' },
  'fd-shilun': { repo: 'fd-shilun', web: 'web', config: 'tsconfig.json' },
  'fd-zhesi': { repo: 'fd-zhesi', web: 'web', config: 'tsconfig.json' },
}

// 每宿主要截的页面：path + 说明（对应 tasks.md 7.3 的清单）
const PAGES = {
  'fd-lex': [{ path: '/', name: 'home-hero' }],
  'fd-shilun': [{ path: '/', name: 'landing' }, { path: '/workbench', name: 'workbench' }],
  'fd-zhesi': [{ path: '/', name: 'landing' }, { path: '/workbench', name: 'workbench' }],
}

const VIEWPORT = { width: 1440, height: 900 }
const THRESHOLD = 0.001 // 允许 0.1% 差异像素（抗锯齿/字体 hinting）

function arg(name, def) {
  const i = process.argv.indexOf(`--${name}`)
  return i > 0 && process.argv[i + 1] ? process.argv[i + 1] : def
}
const has = (name) => process.argv.includes(`--${name}`)

function log(msg) {
  process.stdout.write(`${msg}\n`)
}

/**
 * 起静态服务，返回 { url, stop }。
 *
 * **必须带 SPA fallback**：`python3 -m http.server` 对 `/workbench` 直接 404，
 * 于是"改前改后都拍同一个 404 页"，像素差为 0 —— 这是最典型的假绿
 * （2026-11-11 实测踩到：两张 20KB 的 404 截图被当成"一致"）。
 * 这里用一个极小的 node 服务器：命中文件就发文件，否则回 index.html。
 */
function serve(dir) {
  const http = require('node:http')
  const fsx = require('node:fs')
  const port = 41000 + Math.floor(Math.random() * 2000)
  const MIME = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.svg': 'image/svg+xml',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ico': 'image/x-icon',
  }
  const proc = http.createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || '/').split('?')[0])
    let file = path.join(dir, urlPath)
    // 目录或不存在 → 回落 index.html（SPA 路由）
    if (!fsx.existsSync(file) || fsx.statSync(file).isDirectory()) {
      file = path.join(dir, 'index.html')
    }
    try {
      const body = fsx.readFileSync(file)
      res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] || 'application/octet-stream' })
      res.end(body)
    } catch {
      res.writeHead(404).end('not found')
    }
  })
  proc.listen(port, '127.0.0.1')
  // 等服务就绪：轮询直到能连上（不睡固定时长，避免慢机误判）
  const deadline = Date.now() + 15000
  return new Promise((resolve, reject) => {
    const tryOnce = () => {
      const req = http.get({ host: '127.0.0.1', port, path: '/', timeout: 800 }, (res) => {
        res.resume()
        resolve({ url: `http://127.0.0.1:${port}`, stop: () => proc.close() })
      })
      req.on('error', () => {
        if (Date.now() > deadline) {
          proc.close()
          reject(new Error(`静态服务起不来（port ${port}）`))
        } else setTimeout(tryOnce, 150)
      })
      req.on('timeout', () => req.destroy())
    }
    tryOnce()
  })
}

/** 逐像素比较两张 PNG（用 playwright 自带 png 解码：通过 page 里做，省依赖） */
async function pixelDiff(page, bufA, bufB) {
  const a = bufA.toString('base64')
  const b = bufB.toString('base64')
  return page.evaluate(
    async ([a64, b64]) => {
      const load = (s) =>
        new Promise((res, rej) => {
          const img = new Image()
          img.onload = () => res(img)
          img.onerror = rej
          img.src = 'data:image/png;base64,' + s
        })
      const [ia, ib] = await Promise.all([load(a64), load(b64)])
      if (ia.width !== ib.width || ia.height !== ib.height) {
        return { sizeMismatch: true, wa: ia.width, ha: ia.height, wb: ib.width, hb: ib.height }
      }
      const c = document.createElement('canvas')
      c.width = ia.width
      c.height = ia.height
      const ctx = c.getContext('2d', { willReadFrequently: true })
      ctx.drawImage(ia, 0, 0)
      const da = ctx.getImageData(0, 0, c.width, c.height).data
      ctx.clearRect(0, 0, c.width, c.height)
      ctx.drawImage(ib, 0, 0)
      const db = ctx.getImageData(0, 0, c.width, c.height).data
      let diff = 0
      for (let i = 0; i < da.length; i += 4) {
        if (da[i] !== db[i] || da[i + 1] !== db[i + 1] || da[i + 2] !== db[i + 2]) diff++
      }
      const total = c.width * c.height
      return { sizeMismatch: false, diff, total, ratio: diff / total, w: c.width, h: c.height }
    },
    [a, b],
  )
}

async function main() {
  let chromium
  try {
    ;({ chromium } = require(PW))
  } catch (e) {
    console.error(`找不到 playwright：${PW}\n  装法：cd paas && npm i playwright`)
    return 1
  }

  const selfTest = has('self-test')
  const host = arg('host', selfTest ? 'fd-lex' : '')
  if (!HOSTS[host]) {
    console.error(`--host 必须是 ${Object.keys(HOSTS).join(' / ')}`)
    return 1
  }
  const spec = HOSTS[host]
  const repoDir = path.join(REPO_ROOT, spec.repo)
  const distDir = path.join(repoDir, spec.web, 'dist')
  if (!fs.existsSync(distDir)) {
    console.error(`缺 dist：${distDir} —— 先在该宿主 npm run build（不跳过：'没构建' ≠ '一致'）`)
    return 1
  }

  const outDir = path.join(REPO_ROOT, 'openspec', 'changes', 'archive',
    '2026-11-10-lex-web-shell-convergence', 'screenshots', '7.3')
  fs.mkdirSync(outDir, { recursive: true })

  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: VIEWPORT, deviceScaleFactor: 1 })
  const results = []

  // 两侧：--before-dir 给了就做改前/改后对比；否则（或 --self-test）同源自比
  const beforeDir = has('self-test') ? distDir : arg('before-dir', '')
  if (!beforeDir) {
    console.error('要么给 --before-dir <改前 dist 目录>，要么用 --self-test')
    return 1
  }
  if (!fs.existsSync(beforeDir)) {
    console.error(`改前 dist 不存在：${beforeDir}`)
    return 1
  }

  const shoot = async (baseUrl, p, tag) => {
    await page.goto(baseUrl + p.path, { waitUntil: 'networkidle', timeout: 30000 })
    // 关掉动画：脉冲骨架屏会让同一页面两次截图不同，那是伪差异
    await page.addStyleTag({ content: '*,*::before,*::after{animation:none!important;transition:none!important}' })
    await page.waitForTimeout(150)
    const buf = await page.screenshot({ fullPage: true })
    fs.writeFileSync(path.join(outDir, `${host}-${p.name}-${tag}.png`), buf)
    return buf
  }

  const srvBefore = await serve(beforeDir)
  const srvAfter = await serve(distDir)
  try {
    for (const p of PAGES[host]) {
      const a = await shoot(srvBefore.url, p, 'before')
      const b = await shoot(srvAfter.url, p, 'after')
      // 防"改前改后都拍到 404 所以 0 差异"：检查渲染后的正文不是 http.server 的 404 页
      const txt = await page.evaluate(() => document.body.innerText.slice(0, 200))
      if (/Error response|Nothing matches the given URI|File not found/i.test(txt)) {
        log(`  ${host} ${p.path}: ✗ 拍到的是 404 页（静态服务缺 SPA fallback），此页结论无效`)
        results.push({ page: p.path, name: p.name, notFound: true, same: false })
        continue
      }
      const d = await pixelDiff(page, a, b)
      const same = !d.sizeMismatch && d.ratio <= THRESHOLD
      results.push({ page: p.path, name: p.name, ...d, same })
      if (d.sizeMismatch) {
        log(`  ${host} ${p.path} (${p.name}): ✗ 截图尺寸不一致 ` +
            `${d.wa}x${d.ha} vs ${d.wb}x${d.hb}（结构变了）`)
      } else {
        log(`  ${host} ${p.path} (${p.name}): 差异像素 ${d.diff}/${d.total} = ` +
            `${(d.ratio * 100).toFixed(4)}%  ${same ? '✓' : '✗ 超阈值'}`)
      }
    }
  } finally {
    srvBefore.stop()
    srvAfter.stop()
    await browser.close()
  }

  const ok = results.every((r) => r.same)
  log('')
  log(ok
    ? `✓ ${host}: ${results.length} 页改前/改后差异均在 ${(THRESHOLD * 100).toFixed(1)}% 阈值内（截图存 ${outDir}）`
    : `✗ ${host}: 有页面超出阈值，人工看截图：${outDir}`)
  return ok ? 0 : 1
}

main().then((c) => process.exit(c)).catch((e) => {
  console.error(e)
  process.exit(1)
})
