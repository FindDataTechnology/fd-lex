// 共享壳认证的行为单测（spec web-shell-kit 组 2：2.1–2.5）
//
// 全部走注入接缝（fetchImpl / assign / now），不起真浏览器、不连真 IdP。
// 覆盖：
//   2.1 同一份实现两种配置（带 resource / 不带 resource）
//   2.2 到期前静默续期；过期令牌不发出
//   2.3 续期被拒（invalid）与续期故障（transient）分流，后者不清会话
//   2.4 未配 audience/resource 时不强校验
//   2.5 未配置时如实阻塞、不跳转
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AuthError, createAuth, type AuthConfig } from './auth/index'

const ISSUER = 'https://auth.test.example'
const PKCE = 'test.pkce'

function b64url(obj: Record<string, unknown>): string {
  return btoa(JSON.stringify(obj)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function idToken(claims: Record<string, unknown> = {}): string {
  const header = b64url({ alg: 'ES384', typ: 'JWT' })
  const payload = b64url({
    sub: 'u-1',
    email: 'a@example.com',
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...claims,
  })
  return `${header}.${payload}.sig`
}

const baseConfig: AuthConfig = {
  configured: true,
  issuer: ISSUER,
  client_id: 'c-1',
  scopes: ['openid', 'offline_access'],
  missing: [],
}

type Call = { url: string; init?: RequestInit }

function fakeFetch(handler: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = []
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init }
    calls.push(call)
    return handler(call)
  }) as unknown as typeof fetch
  return { impl, calls }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

describe('2.1 一份实现、两种配置', () => {
  it('带 resource：token 请求携带 resource 参数', async () => {
    const { impl, calls } = fakeFetch(() => json({ id_token: idToken(), access_token: 'a' }))
    const auth = createAuth({
      configUrl: '/api/console/auth/config',
      fetchConfig: async () => ({ ...baseConfig, resource: 'https://mcp.test/api' }),
      redirectUri: 'https://app.test/console/callback',
      storagePrefix: 'test',
      fetchImpl: impl,
      assign: () => {},
    })
    // 造一个回跳现场
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's' }))
    const url = new URL('https://app.test/console/callback?code=c&state=s')
    vi.stubGlobal('location', { href: url.toString() })
    vi.stubGlobal('history', { replaceState: () => {} })
    await auth.completeLogin()
    const tokenCall = calls.find((c) => c.url.includes('/oidc/token'))
    expect(tokenCall).toBeTruthy()
    expect(String(tokenCall!.init?.body)).toContain('resource=')
    vi.unstubAllGlobals()
  })

  it('不带 resource：token 请求不含 resource 参数', async () => {
    const { impl, calls } = fakeFetch(() => json({ id_token: idToken() }))
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/workbench',
      storagePrefix: 'test',
      fetchImpl: impl,
      assign: () => {},
    })
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's' }))
    vi.stubGlobal('location', { href: 'https://app.test/workbench?code=c&state=s' })
    vi.stubGlobal('history', { replaceState: () => {} })
    await auth.completeLogin()
    const tokenCall = calls.find((c) => c.url.includes('/oidc/token'))
    expect(String(tokenCall!.init?.body)).not.toContain('resource=')
    vi.unstubAllGlobals()
  })
})

describe('2.2 静默续期', () => {
  it('未过期不发续期请求', async () => {
    const { impl, calls } = fakeFetch(() => json({}))
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      now: () => 1_000_000,
    })
    auth.saveSession({
      idToken: idToken(),
      refreshToken: 'r',
      expiresAt: 1_000_000 + 600_000,
      identity: 'a',
    })
    const fresh = await auth.ensureFresh()
    expect(fresh?.idToken).toBeTruthy()
    expect(calls).toEqual([])
  })

  it('临近过期时续期一次，且返回新令牌', async () => {
    const { impl, calls } = fakeFetch(() =>
      json({ id_token: idToken({ sub: 'u-2' }), refresh_token: 'r2' }),
    )
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      now: () => 1_000_000,
    })
    auth.saveSession({
      idToken: idToken(),
      refreshToken: 'r1',
      expiresAt: 1_000_000 + 1_000, // 落在 skew 窗口内
      identity: 'a',
    })
    const next = await auth.ensureFresh()
    expect(calls.filter((c) => c.url.includes('/oidc/token'))).toHaveLength(1)
    expect(next?.refreshToken).toBe('r2')
    expect(next!.expiresAt).toBeGreaterThan(1_000_000)
  })
})

describe('2.3 续期失败分流', () => {
  it('凭据被拒（401）→ invalid，且引导重登', async () => {
    const { impl } = fakeFetch(() => new Response('bad refresh', { status: 401 }))
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      now: () => 1_000_000,
    })
    auth.saveSession({ idToken: idToken(), refreshToken: 'r', expiresAt: 1, identity: 'a' })
    await expect(auth.ensureFresh()).rejects.toMatchObject({ kind: 'invalid' })
  })

  it('网络故障 → transient，且**不清会话**', async () => {
    const { impl } = fakeFetch(() => {
      throw new Error('ECONNRESET')
    })
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      now: () => 1_000_000,
    })
    auth.saveSession({ idToken: idToken(), refreshToken: 'r', expiresAt: 1, identity: 'a' })
    await expect(auth.ensureFresh()).rejects.toMatchObject({ kind: 'transient' })
    // 会话仍在——transient 不该把用户登出
    expect(auth.loadSession()).not.toBeNull()
  })

  it('上游 5xx 也算 transient', async () => {
    const { impl } = fakeFetch(() => new Response('boom', { status: 503 }))
    const auth = createAuth({
      fetchConfig: async () => baseConfig,
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      now: () => 1_000_000,
    })
    auth.saveSession({ idToken: idToken(), refreshToken: 'r', expiresAt: 1, identity: 'a' })
    await expect(auth.ensureFresh()).rejects.toMatchObject({ kind: 'transient' })
    expect(auth.loadSession()).not.toBeNull()
  })
})

describe('2.4 audience 兼容路径', () => {
  it('未配 resource 时，带 aud 的令牌不被拒（回归：曾因强校验 audience 线上报错）', async () => {
    const { impl } = fakeFetch(() => json({ id_token: idToken({ aud: 'some-aud' }) }))
    const auth = createAuth({
      fetchConfig: async () => baseConfig, // 无 resource 字段
      redirectUri: 'https://app.test/workbench',
      storagePrefix: 'test',
      fetchImpl: impl,
      assign: () => {},
    })
    sessionStorage.setItem(PKCE, JSON.stringify({ verifier: 'v', state: 's' }))
    vi.stubGlobal('location', { href: 'https://app.test/workbench?code=c&state=s' })
    vi.stubGlobal('history', { replaceState: () => {} })
    const result = await auth.completeLogin()
    expect(result.ok).toBe(true)
    vi.unstubAllGlobals()
  })
})

describe('2.5 未配置如实阻塞', () => {
  it('缺 issuer/client 时抛 config 错误，且不发起任何跳转或 fetch', async () => {
    const assigned: string[] = []
    const { impl, calls } = fakeFetch(() => json({}))
    const auth = createAuth({
      fetchConfig: async () => ({
        configured: false,
        issuer: '',
        client_id: '',
        scopes: [],
        missing: ['LOGTO_ISSUER', 'LOGTO_CLIENT_ID'],
      }),
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      fetchImpl: impl,
      assign: (url) => assigned.push(url),
    })
    await expect(auth.beginLogin()).rejects.toMatchObject({ kind: 'config' })
    expect(assigned).toEqual([])
    expect(calls).toEqual([])
  })

  it('AuthError 带机器码与缺失项（本地化交宿主，壳内不带语言文案）', async () => {
    const auth = createAuth({
      fetchConfig: async () => ({
        configured: false,
        issuer: '',
        client_id: '',
        scopes: [],
        missing: ['LOGTO_CLIENT_ID'],
      }),
      redirectUri: 'https://app.test/cb',
      storagePrefix: 'test',
      assign: () => {},
    })
    const err = await auth.beginLogin().catch((e: unknown) => e)
    expect(err).toBeInstanceOf(AuthError)
    expect((err as AuthError).kind).toBe('config')
    // 消息是稳定机器码；缺失项在 detail 里，由宿主映射成本地化文案
    expect((err as AuthError).message).toBe('auth.not_configured')
    expect((err as AuthError).detail).toContain('LOGTO_CLIENT_ID')
  })
})
