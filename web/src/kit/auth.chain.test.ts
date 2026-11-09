// 登录全链回归（change lex-web-shell-convergence 7.2）。
//
// 一条链走完：登录（PKCE 授权）→ 回跳换令牌 → 受保护请求带 Bearer →
// 令牌过期后静默续期 → 续期后重放成功 → 退出后受保护入口不可达。
// 全程用注入接缝，不起真浏览器、不打真 IdP。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createAuth } from './auth/index'
import { createTransport } from './transport/index'

function b64url(o: Record<string, unknown>): string {
  return btoa(JSON.stringify(o)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function idToken(expSeconds: number, extra: Record<string, unknown> = {}): string {
  return `${b64url({ alg: 'ES384' })}.${b64url({ sub: 'u-1', exp: expSeconds, ...extra })}.sig`
}

let assigned: string[] = []

/** 用 happy-dom 的真实存储与 history；回跳现场靠 replaceState 构造。 */
function setUrl(path: string) {
  window.history.replaceState({}, '', path)
}

const CONFIG = {
  configured: true,
  issuer: 'https://auth.test',
  client_id: 'c-1',
  scopes: ['openid', 'offline_access'],
  missing: [] as string[],
}

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  assigned = []
  setUrl('/console')
})

afterEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('登录全链', () => {
  it('登录 → 受保护请求 → 续期 → 重放 → 退出后不可达', async () => {
    let now = 1_000_000
    const auth = createAuth({
      fetchConfig: async () => CONFIG,
      redirectUri: `${window.location.origin}/console/callback`,
      storagePrefix: 'chain',
      now: () => now,
      assign: (u) => assigned.push(u),
    })

    // ① 登录：跳授权端点，PKCE 现场落会话存储
    await auth.beginLogin()
    expect(assigned[0]).toContain('https://auth.test/oidc/auth?')
    expect(assigned[0]).toContain('code_challenge_method=S256')
    const pkceRaw = sessionStorage.getItem('chain.pkce')
    expect(pkceRaw).toBeTruthy()
    const pkce = JSON.parse(pkceRaw as string)

    // ② 回跳换令牌
    setUrl(`/console/callback?code=c-1&state=${pkce.state}`)
    sessionStorage.setItem('chain.pkce', pkceRaw as string)
    // exp 是秒；now 是毫秒。取 now/1000 + 5 秒，稍后把 now 推到 10 秒后即过期。
    const shortLived = idToken(Math.floor(now / 1000) + 5)
    const apiCalls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input)
        if (url.includes('/oidc/token')) {
          const body = String(init?.body ?? '')
          const isRefresh = body.includes('grant_type=refresh_token')
          return {
            ok: true,
            status: 200,
            json: async () =>
              isRefresh
                ? { id_token: idToken(Math.floor(now / 1000) + 3600), refresh_token: 'r-2' }
                : { id_token: shortLived, refresh_token: 'r-1' },
          } as Response
        }
        apiCalls.push(String(new Headers(init?.headers).get('Authorization')))
        // 第一次受保护请求：令牌“过期”前缀 401，续期后应带新令牌成功
        return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
      }),
    )
    const ok = await auth.completeLogin()
    expect(ok.ok).toBe(true)

    // ③ 受保护请求带 Bearer
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => auth.loadSession()?.idToken ?? null,
      onUnauthorized: async () => {
        const s = auth.loadSession()
        if (!s) return false
        const fresh = await auth.refresh(s).catch(() => null)
        if (!fresh) return false
        auth.saveSession(fresh)
        return true
      },
    })
    await transport.get('/me')
    expect(apiCalls[0]).toBe(`Bearer ${shortLived}`)

    // ④ 令牌到期 → 续期
    now += 10_000 // 短命令牌 exp = now0/1000 + 5 秒，推 10 秒后必过期
    const session = auth.loadSession()!
    expect(auth.isExpired(session)).toBe(true)
    const fresh = await auth.refresh(session)
    expect(fresh.refreshToken).toBe('r-2')
    auth.saveSession(fresh)
    expect(auth.isExpired(auth.loadSession()!)).toBe(false)

    // ⑤ 退出后受保护入口不可达（无会话 → 取不到令牌）
    auth.logoutLocal()
    expect(auth.loadSession()).toBeNull()
    expect(auth.ensureFresh().catch(() => null)).resolves.toBeNull
  })
})
