// 萬星副驾宿主片段（add-embed-copilot-lane 试点接入）：
// 控制台右下角嵌入萬星 embed widget（#/embed/<slug>），三件事——
//   ① init 握手 + 页面上下文推送（wx-copilot-context，随路由更新）
//   ② 身份递送（wx-copilot-identity-request → 把控制台 ID token 按回合递给
//      widget，widget 不持久化；未登录回 null，widget 自动落匿名档）
//   ③ iframe 透明 + 固定右下，收展由 widget 内部自管（wx-copilot-state）
// 挂在 ConsoleLayout（每页一份）。换正式识律 copilot agent 时只改 WIDGET_SLUG。
import { useEffect, useRef, useState } from 'react'
import { loadSession } from './auth'

const WANXING_ORIGIN = 'https://wanxing.finddatatech.cloud'
const WIDGET_SLUG = 'packs-vtdpsnmtw6oq9i99mbhbdw-pack-demo-fingpt'

export function WanxingCopilot() {
  const frameRef = useRef<HTMLIFrameElement | null>(null)
  const [open, setOpen] = useState(true)

  const src = `${WANXING_ORIGIN}/#/embed/${WIDGET_SLUG}?accent=%231d4ed8&open=1`

  const post = (msg: Record<string, unknown>) => {
    frameRef.current?.contentWindow?.postMessage({ slug: WIDGET_SLUG, ...msg }, WANXING_ORIGIN)
  }

  useEffect(() => {
    const onMessage = (ev: MessageEvent) => {
      if (ev.origin !== WANXING_ORIGIN) return
      const d = ev.data as { type?: string; slug?: string; open?: boolean }
      if (d?.slug !== WIDGET_SLUG) return
      if (d.type === 'wx-copilot-ready' || d.type === 'wx-copilot-state') {
        // ready/state 只是姿态同步；收展态用于宿主侧高度适配（当前固定框足够）
        if (typeof d.open === 'boolean') setOpen(d.open)
        if (d.type === 'wx-copilot-ready') post({ type: 'wx-copilot-init' })
      }
      if (d.type === 'wx-copilot-identity-request') {
        const session = loadSession()
        post({ type: 'wx-copilot-identity', token: session?.idToken ?? null })
      }
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 页面上下文随路由推送（首帧后与每次 pathname 变化）
  useEffect(() => {
    post({
      type: 'wx-copilot-context',
      line: '识律',
      page: window.location.pathname,
      intent: '控制台操作伴随',
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [window.location.pathname])

  return (
    <iframe
      ref={frameRef}
      src={src}
      title="助手"
      data-testid="wanxing-copilot"
      allow="clipboard-write"
      style={{
        position: 'fixed',
        bottom: 0,
        right: 0,
        width: '420px',
        height: open ? '640px' : '80px',
        border: 0,
        background: 'transparent',
        zIndex: 2147483000,
        transition: 'height 0.2s ease',
      }}
    />
  )
}
