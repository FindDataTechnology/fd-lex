// 同租户静默单点登录（web-shell-kit「同租户静默单点登录」）。
//
// 真实 IdP 形状已用 curl 核实（2026-11-18，auth.finddatatech.cloud / Logto）：
//   · 无会话时 `prompt=none` → **303 到 redirect_uri?error=login_required**（不是 400，不渲染页面）
//   · auth 域带 `X-Frame-Options: SAMEORIGIN` → **iframe 静默不可行**，只能整页跳
// 所以「静默」的语义是：**发起一次整页跳转，多数情况下带 code 直接回来、用户无感；
// 无会话则带 error 回来并被无副作用地降级成登录入口**。不发请求、不做 iframe。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AuthError, createAuth, type AuthConfig } from './auth/index'

const baseConfig: AuthConfig = {
  configured: true,
  issuer: 'https://auth.test',
  client_id: 'cid',
  scopes: ['openid', 'offline_access'],
  missing: [],
}

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const idToken = (claims: Record<string, unknown> = {}) =>
  `${b64({ alg: 'none' })}.${b64({ sub: 'u-1', username: 'alice', exp: 4_000_000_000, ...claims })}.sig`

const PKCE = 'test.pkce'

/** 壳内部读 `window.location`，happy-dom 下 vi.stubGlobal 不总生效——直接定义属性。 */
function setLocation(href: string) {
  const url = new URL(href)
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { href, search: url.search, pathname: url.pathname, hash: '', origin: url.origin },
  })
  Object.defineProperty(window, 'history', {
    configurable: true,
    value: { replaceState: () => {} },
  })
}



const REAL_LOCATION = Object.getOwnPropertyDescriptor(window, 'location')
const REAL_HISTORY = Object.getOwnPropertyDescriptor(window, 'history')

afterEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  // defineProperty 覆写不归 vitest 管：手工还原，否则后续用例继承上一个用例的地址。
  if (REAL_LOCATION) Object.defineProperty(window, 'location', REAL_LOCATION)
  if (REAL_HISTORY) Object.defineProperty(window, 'history', REAL_HISTORY)
})

function makeAuth(over = {}) {
  const assigned: string[] = []
  const auth = createAuth({
    fetchConfig: async () => baseConfig,
    redirectUri: 'https://app.test/cb',
    storagePrefix: 'test',
    fetchImpl: (async () => {
      throw new Error('no network in this suite')
    }) as unknown as typeof fetch,
    assign: (u: string) => assigned.push(u),
    ...over,
  })
  return { auth, assigned }
}

describe('静默 SSO：发起', () => {
  it('beginSilentLogin 带 prompt=none，并使用与交互式相同的 PKCE 键', async () => {
    const { auth, assigned } = makeAuth()
    await auth.beginSilentLogin()
    expect(assigned).toHaveLength(1)
    expect(assigned[0]).toContain('prompt=none')
    expect(assigned[0]).toContain('code_challenge_method=S256')
    const pkce = JSON.parse(sessionStorage.getItem(PKCE)!)
    expect(pkce.silent).toBe(true)
    expect(typeof pkce.verifier).toBe('string')
  })

  it('beginLogin（交互式）不带 prompt，且 PKCE 标记 silent=false', async () => {
    const { auth, assigned } = makeAuth()
    await auth.beginLogin()
    expect(assigned[0]).not.toContain('prompt=')
    expect(JSON.parse(sessionStorage.getItem(PKCE)!).silent).toBe(false)
  })
})

describe('静默 SSO：回跳收口', () => {
  it('无会话（error=login_required）→ interactive-required，且无副作用', async () => {
    const { auth } = makeAuth()
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's', silent: true }))
    vi.stubGlobal('location', {
      href: 'https://app.test/cb?error=login_required&error_description=End-User+authentication+is+required&state=s',
    })
    vi.stubGlobal('history', { replaceState: () => {} })

    await expect(auth.trySilentLogin()).resolves.toBe('interactive-required')
    // 无副作用：会话不建、PKCE 清干净（下一次交互式授权才能干净开始）
    expect(auth.loadSession()).toBeNull()
    expect(sessionStorage.getItem(PKCE)).toBeNull()
  })

  it('interaction_required 同样降级', async () => {
    const { auth } = makeAuth()
    vi.stubGlobal('location', { href: 'https://app.test/cb?error=interaction_required' })
    vi.stubGlobal('history', { replaceState: () => {} })
    await expect(auth.trySilentLogin()).resolves.toBe('interactive-required')
  })

  it('真实故障（其他 error=）不被吞成「未登录」', async () => {
    const { auth } = makeAuth()
    vi.stubGlobal('location', { href: 'https://app.test/cb?error=unauthorized_client' })
    vi.stubGlobal('history', { replaceState: () => {} })
    await expect(auth.trySilentLogin()).rejects.toBeInstanceOf(AuthError)
  })

  it('有 code → 走同一条交换路径并落会话', async () => {
    const calls: string[] = []
    const { auth } = makeAuth({
      fetchImpl: (async (u: string) => {
        calls.push(String(u))
        return new Response(JSON.stringify({ id_token: idToken(), access_token: 'a' }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }) as unknown as typeof fetch,
    })
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's', silent: true }))
    vi.stubGlobal('location', { href: 'https://app.test/cb?code=c&state=s' })
    vi.stubGlobal('history', { replaceState: () => {} })

    await expect(auth.trySilentLogin()).resolves.toBe('logged-in')
    expect(calls.some((c) => c.includes('/oidc/token'))).toBe(true)
    expect(auth.loadSession()?.identity).toBe('alice')
  })

  it('code 但 state 不符 → 降级（不建会话）', async () => {
    const { auth } = makeAuth()
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 'other', silent: true }))
    vi.stubGlobal('location', { href: 'https://app.test/cb?code=c&state=s' })
    vi.stubGlobal('history', { replaceState: () => {} })
    await expect(auth.trySilentLogin()).resolves.toBe('interactive-required')
    expect(auth.loadSession()).toBeNull()
  })

  it('干脆没有回跳参数 → 降级（首次访问的正常路径）', async () => {
    const { auth } = makeAuth()
    vi.stubGlobal('location', { href: 'https://app.test/' })
    vi.stubGlobal('history', { replaceState: () => {} })
    await expect(auth.trySilentLogin()).resolves.toBe('interactive-required')
  })
})

// ── 3.6 真实故障不被吞成「未登录」 ──────────────────────────────────────────
// 三条路径各自把故障如实上报成 outcome:'error'，而不是 interactive-required：
//   ① 有 code 但交换失败（凭据被拒 / 上游 5xx）
//   ② 有回跳但 error= 是真实错误（如 unauthorized_client）
//   ③ 发起静默跳转本身失败（配置缺失 / 网络拒绝）

describe('静默 SSO：真实故障如实上报', () => {
  it('有 code 但交换被拒 → outcome=error，不是「未登录」', async () => {
    const { runSsoOnMount } = await import('./auth/sso-mount')
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: (async () =>
        new Response(JSON.stringify({ error: 'invalid_grant' }), {
          status: 400,
          headers: { 'content-type': 'application/json' },
        })) as unknown as typeof fetch,
      assign: () => {},
    })
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's', silent: true }))
    setLocation('https://app.test/cb?code=c&state=s')
    const out = await runSsoOnMount({
      auth,
      setToken: () => {},
      hasToken: () => false,
      location: { href: 'https://app.test/cb?code=c&state=s', search: '?code=c&state=s' },
      history: { replaceState: () => {} },
    })
    expect(out.outcome).toBe('error')
  })

  it('回跳带真实 error（非 login_required）→ outcome=error', async () => {
    const { runSsoOnMount } = await import('./auth/sso-mount')
    const { auth } = makeAuth()
    setLocation('https://app.test/cb?error=unauthorized_client')
    const out = await runSsoOnMount({
      auth,
      setToken: () => {},
      hasToken: () => false,
      location: { href: 'https://app.test/cb?error=unauthorized_client', search: '?error=unauthorized_client' },
      history: { replaceState: () => {} },
    })
    expect(out.outcome).toBe('error')
  })

  it('发起静默跳转失败（配置缺失）→ outcome=error', async () => {
    const { runSsoOnMount } = await import('./auth/sso-mount')
    const auth = createAuth({
      fetchConfig: async () => ({ ...baseConfig, configured: false, missing: ['issuer'] }),
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      assign: () => {},
    })
    // 提示 cookie 存在（否则根本不会发起，见 sso-hint 的用途说明）
    document.cookie = 'fd_sso_hint=1; path=/'
    const out = await runSsoOnMount({
      auth,
      setToken: () => {},
      hasToken: () => false,
      location: { href: 'https://app.test/', search: '' },
      history: { replaceState: () => {} },
    })
    expect(out.outcome).toBe('error')
  })

  it('无会话的 login_required 仍是交互式降级，不是错误', async () => {
    const { runSsoOnMount } = await import('./auth/sso-mount')
    const { auth } = makeAuth()
    const out = await runSsoOnMount({
      auth,
      setToken: () => {},
      hasToken: () => false,
      location: { href: 'https://app.test/cb?error=login_required', search: '?error=login_required' },
      history: { replaceState: () => {} },
    })
    expect(out.outcome).toBe('interactive-required')
  })
})
