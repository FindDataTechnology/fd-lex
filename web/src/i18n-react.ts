// react-i18next 的同形垫片（change lex-web-shell-convergence 3.3）。
//
// 宿主的 24 个组件用 `const { t, i18n } = useTranslation()`，303 处 `t('...')`。
// 迁移到共享壳时只换这个 import，调用点一个不动——把「迁移本身引入 bug」的面
// 压到最小。返回形状与 react-i18next 一致：`{ t, i18n: { language, changeLanguage } }`。
import { useI18n } from './kit/i18n/react'
import { i18n as store } from './i18n'

export function useTranslation() {
  const { t, i18n, raw } = useI18n(store)
  return { t, i18n, raw }
}

export { store as i18next }
