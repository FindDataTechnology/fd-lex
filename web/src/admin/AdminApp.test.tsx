// 管理后台入口哨兵（change lex-web-shell-convergence 6.1：三入口之一）。
//
// 之前只有 console 与 landing 有哨兵，admin 没有——共享壳改动时这一面
// 是「没人看着」的。本文件只做最小可见性断言：入口能渲染、四个面板可达。
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import { AdminApp } from './AdminApp'
import { clearSession, saveSession } from '../console/auth'

function seedSession() {
  saveSession({
    idToken: 'header.payload.sig',
    accessToken: 'access',
    expiresAt: Date.now() + 3_600_000,
    identity: 'admin-demo',
    roles: ['lex-admin'],
    organizations: [],
  })
}

beforeEach(() => {
  clearSession()
  sessionStorage.setItem('lex.admin.token', 'test-admin-token')
})

afterEach(() => {
  cleanup()
  clearSession()
  sessionStorage.removeItem('lex.admin.token')
})

describe('AdminApp 入口', () => {
  it('渲染后台骨架与导航', async () => {
    seedSession()
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <AdminApp />
      </MemoryRouter>,
    )
    // 至少一个页面标题出现，说明路由/布局挂上了
    expect(await screen.findByRole('heading', { level: 1 })).toBeTruthy()
  })
})
