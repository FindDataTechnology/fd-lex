// 传输层单测（spec web-shell-kit 组 4：4.2 续期重放 / 4.3 不落凭据）
//
// 401 的语义是本层唯一复杂点，规则只有一条：**只重放一次**。
// 无限重试会把一次登出变成一串打空的请求；不重试则用户一过期就掉线。
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TransportError, createTransport, redact } from './transport/index'

type Call = { url: string; init?: RequestInit }

function fakeFetch(handler: (call: Call, n: number) => Response | Promise<Response>) {
  const calls: Call[] = []
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const call = { url: String(input), init }
    calls.push(call)
    return handler(call, calls.length)
  }) as unknown as typeof fetch
  return { impl, calls }
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => {
  vi.restoreAllMocks()
})

describe('4.2 统一 401 处理', () => {
  it('401 → 续期一次 → 用新令牌重放，最终成功', async () => {
    let token = 'old'
    const { impl, calls } = fakeFetch((_call, n) =>
      n === 1 ? new Response('expired', { status: 401 }) : json({ ok: true }),
    )
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => token,
      onUnauthorized: async () => {
        token = 'new'
        return true
      },
      fetchImpl: impl,
    })
    const out = await transport.get('/me')
    expect(out).toEqual({ ok: true })
    expect(calls).toHaveLength(2)
    const auth = new Headers(calls[1].init?.headers).get('Authorization')
    expect(auth).toBe('Bearer new')
  })

  it('二次 401 立即上抛，不无限重试', async () => {
    const { impl, calls } = fakeFetch(() => new Response('nope', { status: 401 }))
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => 't',
      onUnauthorized: async () => true,
      fetchImpl: impl,
    })
    await expect(transport.get('/me')).rejects.toMatchObject({ status: 401 })
    expect(calls).toHaveLength(2)
  })

  it('未提供 onUnauthorized 时 401 直接上抛（不猜怎么续）', async () => {
    const { impl, calls } = fakeFetch(() => new Response('nope', { status: 401 }))
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => 't',
      fetchImpl: impl,
    })
    await expect(transport.get('/me')).rejects.toMatchObject({ status: 401 })
    expect(calls).toHaveLength(1)
  })

  it('续期返回 false（续不上）时不重放', async () => {
    const { impl, calls } = fakeFetch(() => new Response('nope', { status: 401 }))
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => 't',
      onUnauthorized: async () => false,
      fetchImpl: impl,
    })
    await expect(transport.get('/me')).rejects.toBeInstanceOf(TransportError)
    expect(calls).toHaveLength(1)
  })

  it('非 401 错误带上状态码，不触发续期', async () => {
    const { impl, calls } = fakeFetch(() => new Response('boom', { status: 500 }))
    const renewed = vi.fn(async () => true)
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => 't',
      onUnauthorized: renewed,
      fetchImpl: impl,
    })
    await expect(transport.get('/me')).rejects.toMatchObject({ status: 500 })
    expect(renewed).not.toHaveBeenCalled()
    expect(calls).toHaveLength(1)
  })
})

describe('4.3 不落凭据', () => {
  it('错误对象里不含令牌明文', async () => {
    const secret = 'eyJhbGciOiJFUzM4NCJ9.payload.signature-value-here'
    const { impl } = fakeFetch(() => new Response(`bad token ${secret}`, { status: 400 }))
    const transport = createTransport({
      baseUrl: 'https://api.test',
      getToken: () => secret,
      fetchImpl: impl,
    })
    const err = await transport.get('/me').catch((e: unknown) => e)
    expect(String((err as Error).message)).not.toContain(secret)
    expect(String((err as TransportError).detail)).not.toContain(secret)
  })

  it('整个壳源码里没有 console 调用（结构上不可能打日志）', () => {
    const { readFileSync, readdirSync, statSync } = require('node:fs')
    const { join } = require('node:path')
    const KIT = join(__dirname, '..', '..', '..', 'web-kit')
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const full = join(dir, name)
        if (statSync(full).isDirectory()) walk(full, out)
        else out.push(full)
      }
      return out
    }
    const offenders = walk(KIT)
      .filter((f: string) => /\.(ts|tsx)$/.test(f))
      .filter((f: string) => /\bconsole\.(log|info|warn|error|debug)\s*\(/.test(readFileSync(f, 'utf-8')))
    expect(offenders).toEqual([])
  })

  it('redact 剥掉疑似令牌与敏感字段', () => {
    const out = redact({
      token: 'abc',
      Authorization: 'Bearer xyz',
      nested: { refreshToken: 'r', ok: 'keep' },
      text: 'prefix eytokenpart0123456789012345678901 suffix',
    }) as Record<string, unknown>
    expect(out.token).toBe('<redacted>')
    expect(out.Authorization).toBe('<redacted>')
    expect((out.nested as Record<string, unknown>).refreshToken).toBe('<redacted>')
    expect((out.nested as Record<string, unknown>).ok).toBe('keep')
    expect(String(out.text)).not.toContain('eytokenpart0123456789012345678901')
  })
})
