// 5.4b 端到端验证：在真实浏览器里证明"令牌路径"可用——
// 放一个用 var(--fd-status-warn-fg) 的探针元素，切换 data-theme，读 computed color。
// 不改任何生产控件，因此零视觉风险，但能证明宿主接入的令牌真的能驱动渲染。
const path = require('node:path'), fs = require('node:fs'), http = require('node:http')
const { chromium } = require('/Users/chengsishi/finddata/paas/node_modules/playwright')

const dir = process.argv[2]
const MIME = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.woff':'font/woff','.woff2':'font/woff2','.ico':'image/x-icon'}
const srv = http.createServer((req,res)=>{
  let f = path.join(dir, decodeURIComponent((req.url||'/').split('?')[0]))
  if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(dir,'index.html')
  res.writeHead(200, {'Content-Type': MIME[path.extname(f)]||'application/octet-stream'})
  res.end(fs.readFileSync(f))
})
srv.listen(0,'127.0.0.1', async ()=>{
  const port = srv.address().port
  const b = await chromium.launch()
  const page = await b.newPage({ viewport:{width:1440,height:900} })
  const read = async (theme) => {
    await page.goto(`http://127.0.0.1:${port}/`, { waitUntil:'networkidle' })
    return page.evaluate((t) => {
      document.documentElement.setAttribute('data-theme', t)
      const mk = (cssVar) => {
        const el = document.createElement('span')
        el.style.color = `var(${cssVar})`
        document.body.appendChild(el)
        const c = getComputedStyle(el).color
        el.remove()
        return c
      }
      return {
        warnFg: mk('--fd-status-warn-fg'),
        posBg: mk('--fd-status-positive-bg'),
        errBorder: mk('--fd-error-border'),
      }
    }, theme)
  }
  const light = await read('light')
  const dark = await read('dark')
  await b.close(); srv.close()
  const names = { warnFg:'warn-fg', posBg:'positive-bg', errBorder:'error-border' }
  let ok = true
  for (const k of Object.keys(names)) {
    const differs = light[k] !== dark[k]
    if (!differs) ok = false
    const row = [names[k].padEnd(14), 'light=' + light[k].padEnd(22), 'dark=' + dark[k].padEnd(22),
                 differs ? '✓ 不同' : '✗ 相同（暗色未生效）'].join(' ')
    console.log('  ' + row)
  }
  console.log(`  → 令牌路径可用: ${ok}`)
  process.exit(ok ? 0 : 1)
})
