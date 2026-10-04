import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

import en from './locales/en/translation.json'
import zh from './locales/zh/translation.json'

// i18next 双语骨架：zh/en 各一个 namespace（translation）。
// 语言探测顺序：localStorage -> 浏览器语言 -> 中文兜底。
export const SUPPORTED_LANGUAGES = ['zh', 'en'] as const

void i18n.use(initReactI18next).init({
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  lng: 'zh',
  fallbackLng: 'zh',
  supportedLngs: [...SUPPORTED_LANGUAGES],
  interpolation: { escapeValue: false },
})

export default i18n
