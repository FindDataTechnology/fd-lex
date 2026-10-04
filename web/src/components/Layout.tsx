import { useTranslation } from 'react-i18next'
import { Link, NavLink } from 'react-router-dom'
import type { ReactNode } from 'react'

function Layout({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation()
  const nextLanguage = i18n.language === 'zh' ? 'en' : 'zh'

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-800">
      <header className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <Link to="/" className="text-lg font-semibold tracking-wide">
          {t('brand')}
        </Link>
        <nav className="flex items-center gap-6 text-sm">
          <NavLink to="/" className="hover:text-blue-700">
            {t('nav.home')}
          </NavLink>
          <NavLink to="/console" className="hover:text-blue-700">
            {t('nav.console')}
          </NavLink>
          <NavLink to="/admin" className="hover:text-blue-700">
            {t('nav.admin')}
          </NavLink>
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-1 text-xs hover:border-blue-500 hover:text-blue-700"
            onClick={() => void i18n.changeLanguage(nextLanguage)}
          >
            {nextLanguage === 'zh' ? '中文' : 'English'}
          </button>
        </nav>
      </header>
      <main className="flex flex-1 flex-col">{children}</main>
      <footer className="border-t border-slate-200 px-6 py-3 text-xs text-slate-400">
        {t('footer')}
      </footer>
    </div>
  )
}

export default Layout
