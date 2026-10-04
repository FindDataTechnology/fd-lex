import { useTranslation } from 'react-i18next'

import Layout from '../components/Layout'

function Console() {
  const { t } = useTranslation()

  return (
    <Layout>
      <section className="flex flex-1 flex-col items-center justify-center gap-4 px-6 py-24 text-center">
        <h1 className="text-3xl font-semibold text-slate-900">{t('console.title')}</h1>
        <p className="max-w-xl text-sm text-slate-500">{t('console.placeholder')}</p>
      </section>
    </Layout>
  )
}

export default Console
