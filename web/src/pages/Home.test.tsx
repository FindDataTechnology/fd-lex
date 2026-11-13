// 能力目录：两段（能力 / 交付）如实呈现。
// - 电子签保持内测态（中性虚线徽标、不占用宿主点、不把未开通说成可用）；
// - 已交付条目按真实宿主显示；
// - 案件（matter）是 2026-11 落地的跨环节能力，必须出现在能力段——它此前不在目录里；
// - 未交付条目（模型）不得出现量化产物随包的承诺（spec lex-product-lineup）。
// 中英两语结构一致，行集与真源 fd-lex/docs/product-lineup.json 一致（门会另判）。
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

import i18n from '../i18n'
import Home from './Home'

beforeEach(() => {
  void i18n.changeLanguage('zh')
})

afterEach(() => {
  cleanup()
})

function bodyText(): string {
  return document.body.textContent ?? ''
}

describe('Home capability catalog', () => {
  it('lists e-signature as in-preview with the honest note (zh)', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    )
    const text = bodyText()
    expect(text).toContain('合同电子签署')
    expect(text).toContain('内测中 · 即将上线')
    expect(text).toContain('沙箱验证通过后开放云端签署')
    // 预览行无 host 字段：渲染面不得漏出未解析的 i18n key
    expect(text).not.toContain('capabilities.items.esign')
  })

  it('lists e-signature as in-preview (en)', async () => {
    await i18n.changeLanguage('en')
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    )
    const text = bodyText()
    expect(text).toContain('Contract e-signature')
    expect(text).toContain('In preview · coming soon')
    expect(text).toContain('licensed e-signature provider')
    expect(text).not.toContain('capabilities.items.esign')
  })

  it('keeps shipping capabilities with their real host badges', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    )
    const text = bodyText()
    expect(text).toContain('双宿主') // 检索/合同/案件/接入等已上线能力
    expect(text).toContain('仅私有化') // 自有模型
  })

  it('renders both segments and the matter row', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    )
    const text = bodyText()
    // 两段标题都在（能力 / 交付）
    expect(text).toContain('可以单独开通、单独计量、单独停用的能力')
    expect(text).toContain('不是能力，是拿到能力的方式')
    // 案件：此前完全不在目录里的跨环节能力
    expect(text).toContain('案件')
    expect(text).toContain('案件级成本可合计')
    // 交付段的两行
    expect(text).toContain('私有化交付包')
    expect(text).toContain('API / MCP 接入')
  })

  it('does not promise undelivered model artifacts (zh)', () => {
    render(
      <MemoryRouter>
        <Home />
      </MemoryRouter>,
    )
    const text = bodyText()
    expect(text).not.toContain('量化产物与对照评测报告随交付包')
    expect(text).toContain('不随交付包提供')
    expect(text).toContain('不单独提供模型 API')
  })
})
