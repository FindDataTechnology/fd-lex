import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'

import Layout from '../components/Layout'

function Home() {
  const { t } = useTranslation()

  return (
    <Layout>
      <section className="flex flex-1 flex-col items-center justify-center gap-6 px-6 py-24 text-center">
        <h1 className="text-5xl font-bold tracking-tight text-slate-900">{t('brand')}</h1>
        <p className="text-xl text-slate-600">{t('tagline')}</p>
        <p className="max-w-2xl text-sm leading-6 text-slate-500">{t('home.intro')}</p>
        <div className="mt-4 flex gap-4">
          <Link
            to="/console"
            className="rounded-md bg-blue-700 px-5 py-2.5 text-sm font-medium text-white hover:bg-blue-800"
          >
            {t('home.enterConsole')}
          </Link>
          <Link
            to="/admin"
            className="rounded-md border border-slate-300 px-5 py-2.5 text-sm font-medium text-slate-700 hover:border-blue-500 hover:text-blue-700"
          >
            {t('home.enterAdmin')}
          </Link>
        </div>
        <p className="mt-8 text-xs text-slate-400">{t('home.skeletonNote')}</p>
      </section>
    </Layout>
  )
}

export default Home
