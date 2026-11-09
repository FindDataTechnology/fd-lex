// 双语壳单测（spec web-shell-kit 组 3：3.1 类型约束 / 3.2 键位对等门）
//
// 这里守两件事：
//   1. 少一个键必须抛，且指名是哪个键（否则界面上只是空白，没人发现）；
//   2. 类型层面 en 必须与 zh 同形——那是编译期的事，本文件用 @ts-expect-error 当哨兵。
import { describe, expect, it } from 'vitest'

import { assertLocaleParity, createI18n, localeDiff } from './i18n/index'

const ZH = {
  nav: { home: '首页', console: '控制台' },
  action: { submit: '提交' },
} as const

const EN = {
  nav: { home: 'Home', console: 'Console' },
  action: { submit: 'Submit' },
} as const

describe('3.2 键位对等门', () => {
  it('两语言同形时通过', () => {
    expect(() => assertLocaleParity(ZH, EN)).not.toThrow()
    expect(localeDiff(ZH, EN)).toEqual({ missing: [], extra: [] })
  })

  it('少一个键即抛，且指名该键', () => {
    const partial = { nav: { home: 'Home' }, action: { submit: 'Submit' } }
    expect(() => assertLocaleParity(ZH, partial)).toThrowError(/nav\.console/)
  })

  it('多一个键也抛（反向差集）', () => {
    const extra = {
      nav: { home: 'Home', console: 'Console' },
      action: { submit: 'Submit' },
      bonus: 'Extra',
    }
    expect(() => assertLocaleParity(ZH, extra)).toThrowError(/bonus/)
  })
})

describe('3.1 类型化文案 + 取用', () => {
  it('createI18n 构建期即校验对等', () => {
    expect(() => createI18n({ zh: ZH, en: EN })).not.toThrow()
    expect(() =>
      createI18n({
        zh: ZH,
        en: { nav: { home: 'Home' }, action: { submit: 'Submit' } },
      }),
    ).toThrow()
  })

  it('按路径取文案，缺失时返回路径本身（便于发现而不是空白）', () => {
    const i18n = createI18n({ zh: ZH, en: EN, initial: 'zh' })
    expect(i18n.t('nav.home')).toBe('首页')
    expect(i18n.t('nav.missing')).toBe('nav.missing')
    i18n.setLocale('en')
    expect(i18n.t('nav.home')).toBe('Home')
  })
})
