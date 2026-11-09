// 控制台登录（change lex-web-shell-convergence 6.1：内核换成共享壳）。
//
// 这个文件现在只是**适配层**：对外 API 与语义保持原样（ConsoleApp 与既有测试不动），
// 真正的 PKCE / 续期 / 错误分流由 `src/kit/auth` 提供——全家族只有一份实现。
//
// 为什么保留适配层而不让页面直接调壳：
// - 宿主的契约是「config 逐次传入 + state 不符抛 ConsoleAuthError」；
//   壳的契约是「构造时带 config + state 不符返回 {ok:false}」。两者都合理，
//   硬统一要改 12 个调用点与既有测试，收益不抵风险。
// - 于是壳额外提供 `exchangeCode(code, verifier)` 低层原语：只做令牌交换，
//   不动 window、不落存储，让宿主保留自己的 state 校验与错误语义。
//
// 跨域凭据方案（lex-platform-v1 4.3 的原决策，未变）：
// - lex 域页面拿不到 mcp 域的会话 cookie，也没有共享密钥可用；因此控制台自带
//   Logto 登录，拿到 ID token（证明身份）与 access token（aud=registry 接受的
//   API resource，作凭据透传给 registry patch-keys）。
// - 服务端（/api/console/*）只做 ID token 本地校验；access token 仅用于
//   「铸造/吊销凭据」代理，随 X-Registry-Token 头传递，不落库、不写日志。
// - 未配置 LOGTO_*（服务端 /api/console/auth/config 报 configured=false）时
//   前端如实显示阻塞态与缺失项，绝不假登录。

import { apiUrl } from '../api/config'
import {
  AuthError,
  createAuth,
  type Auth,
  type AuthConfig,
  type AuthSession,
} from '../kit/auth/index'

export interface ConsoleAuthConfig {
  configured: boolean
  issuer: string
  client_id: string
  resource: string
  scopes: string[]
  organization_required: boolean
  missing: string[]
}

export interface ConsoleSession {
  idToken: string
  accessToken: string
  refreshToken?: string
  /** 过期时刻（ms epoch，取 ID/access 较早者） */
  expiresAt: number
  /** 展示身份（来自 ID token claims，仅供显示） */
  identity: string
  email?: string
  name?: string
  roles: string[]
  organizations: string[]
}

export class ConsoleAuthError extends Error {
  readonly status: number | undefined
  readonly detail: string | undefined

  constructor(message: string, status?: number, detail?: string) {
    super(message)
    this.name = 'ConsoleAuthError'
    this.status = status
    this.detail = detail
  }
}

export const SESSION_STORAGE_KEY = 'lex.console.session'
export const PKCE_STORAGE_KEY = 'lex.console.pkce'

/** 登录回跳地址（必须与 Logto 应用登记的 redirect URI 完全一致）。 */
export function consoleCallbackUrl(): string {
  return `${window.location.origin}/console/callback`
}

/** 把宿主 config 适配成壳实例（每次新建；除 sessionStorage 外无状态）。 */
function authFor(config: ConsoleAuthConfig, returnTo?: string): Auth {
  const kitConfig: AuthConfig = {
    configured: config.configured,
    issuer: config.issuer,
    client_id: config.client_id,
    resource: config.resource || undefined,
    scopes: config.scopes,
    organization_required: config.organization_required,
    missing: config.missing,
  }
  return createAuth({
    fetchConfig: async () => kitConfig,
    redirectUri: consoleCallbackUrl(),
    storagePrefix: 'lex.console',
    tokenStorage: 'session',
    defaultReturnTo: returnTo,
  })
}

/** 壳的 AuthError → 宿主的 ConsoleAuthError（语义一一对应）。 */
function asConsoleError(err: unknown): never {
  if (err instanceof AuthError) {
    throw new ConsoleAuthError(err.message, err.status, err.detail)
  }
  throw err
}

function toConsoleSession(session: AuthSession): ConsoleSession {
  return {
    idToken: session.idToken,
    accessToken: session.accessToken ?? '',
    refreshToken: session.refreshToken,
    expiresAt: session.expiresAt,
    identity: session.identity,
    email: session.email,
    name: session.name,
    roles: session.roles ?? [],
    organizations: session.organizations ?? [],
  }
}

// ---------------------------------------------------------------------------
// 配置
// ---------------------------------------------------------------------------

export async function fetchAuthConfig(): Promise<ConsoleAuthConfig> {
  let response: Response
  try {
    response = await fetch(apiUrl('/console/auth/config'))
  } catch (err) {
    throw new ConsoleAuthError(
      'console.config_unreadable',
      undefined,
      err instanceof Error ? err.message : String(err),
    )
  }
  if (!response.ok) {
    throw new ConsoleAuthError('console.config_unreadable', response.status)
  }
  return (await response.json()) as ConsoleAuthConfig
}

// ---------------------------------------------------------------------------
// 会话存取（沿用宿主校验：缺 idToken/accessToken 视为无会话）
// ---------------------------------------------------------------------------

export function loadSession(): ConsoleSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as ConsoleSession
    if (!parsed.idToken || !parsed.accessToken) return null
    return parsed
  } catch {
    return null
  }
}

export function saveSession(session: ConsoleSession): void {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(session))
  } catch {
    /* 存储不可用时仅本次会话生效 */
  }
}

export function clearSession(): void {
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

export function isExpired(session: ConsoleSession, skewMs = 60_000): boolean {
  return session.expiresAt <= Date.now() + skewMs
}

/** base64url 解码 JWT payload（仅展示用；校验在服务端）。实现来自壳。 */
export function decodeJwtClaims(token: string): Record<string, unknown> {
  return createAuth({
    redirectUri: consoleCallbackUrl(),
    storagePrefix: 'lex.console',
    fetchConfig: async () => ({
      configured: false,
      issuer: '',
      client_id: '',
      scopes: [],
      missing: [],
    }),
  }).decodeClaims(token)
}

// ---------------------------------------------------------------------------
// PKCE 工具
// ---------------------------------------------------------------------------

interface PkceState {
  state: string
  verifier: string
  nonce: string
  returnTo: string
}

function loadPkce(): PkceState | null {
  try {
    const raw = sessionStorage.getItem(PKCE_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PkceState) : null
  } catch {
    return null
  }
}

function dropPkce(): void {
  try {
    sessionStorage.removeItem(PKCE_STORAGE_KEY)
  } catch {
    /* ignore */
  }
}

/** 发起登录：委托壳生成 PKCE 参数并跳转（secret 生成、nonce、returnTo 都在壳里）。 */
export async function beginLogin(config: ConsoleAuthConfig, returnTo = '/console'): Promise<void> {
  if (!config.configured) {
    // 保持宿主既有语义：未配置时报 503 而不是壳的机器码
    throw new ConsoleAuthError('login not configured on server', 503)
  }
  try {
    await authFor(config, returnTo).beginLogin()
  } catch (err) {
    asConsoleError(err)
  }
}

/**
 * 处理回跳：校验 state → 用壳换令牌 → 落会话。
 * state 不符或兑换失败抛 ConsoleAuthError（宿主与既有测试依赖该语义）。
 */
export async function completeLogin(
  config: ConsoleAuthConfig,
  query: URLSearchParams,
): Promise<{ returnTo: string }> {
  const code = query.get('code')
  const state = query.get('state')
  const pkce = loadPkce()
  if (!code || !state || !pkce || state !== pkce.state) {
    dropPkce()
    throw new ConsoleAuthError('state mismatch', 400)
  }
  let session: ConsoleSession
  try {
    // 壳只做交换：不动 window、不落存储——宿主保留自己的 state 语义
    session = toConsoleSession(await authFor(config).exchangeCode(code, pkce.verifier))
  } catch (err) {
    dropPkce()
    asConsoleError(err)
  }
  dropPkce()
  saveSession(session)
  return { returnTo: pkce.returnTo || '/console' }
}

/** 续期：凭据失效返回 null（宿主按「需重新登录」处理）。 */
export async function refreshSession(
  config: ConsoleAuthConfig,
  session: ConsoleSession,
): Promise<ConsoleSession | null> {
  if (!session.refreshToken) return null
  try {
    const next = await authFor(config).refresh({
      idToken: session.idToken,
      accessToken: session.accessToken,
      refreshToken: session.refreshToken,
      expiresAt: session.expiresAt,
      identity: session.identity,
    })
    const converted = toConsoleSession(next)
    saveSession(converted)
    return converted
  } catch {
    return null
  }
}

export function logoutLocal(): void {
  clearSession()
}
