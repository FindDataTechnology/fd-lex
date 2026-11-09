import { createI18n } from './kit/i18n/index'
import en from './locales/en'
import zh from './locales/zh'

// 双语骨架（change lex-web-shell-convergence 3.3）：从 i18next 迁到共享壳。
// 对外 API 保持不变（SUPPORTED_LANGUAGES / LANGUAGE_STORAGE_KEY / persistLanguage），
// 语言探测顺序仍是 localStorage -> 默认中文，切换时同步 <html lang>。
export const SUPPORTED_LANGUAGES = ['zh', 'en'] as const
export const LANGUAGE_STORAGE_KEY = 'lex.lang'

type Lang = (typeof SUPPORTED_LANGUAGES)[number]

function initialLanguage(): Lang {
  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY)
    if (stored === 'zh' || stored === 'en') return stored
  } catch {
    // localStorage 不可用（如隐私模式）时回退中文
  }
  return 'zh'
}

function syncDocumentLanguage(lng: string) {
  document.documentElement.lang = lng === 'zh' ? 'zh-CN' : 'en'
}

export const i18n = createI18n({ zh, en, initial: initialLanguage() })

i18n.subscribe(() => syncDocumentLanguage(i18n.locale()))
syncDocumentLanguage(i18n.locale())

export function persistLanguage(lng: string) {
  try {
    localStorage.setItem(LANGUAGE_STORAGE_KEY, lng)
  } catch {
    // 存储不可用时静默：本次会话内切换仍生效
  }
}

export default i18n
