import type { Locale } from '~/utils/routes'

type SeoEntity = 'extension' | 'agent'

interface PageSeoOptions {
  keywords?: readonly string[]
  entity?: SeoEntity
}

const BASE_KEYWORDS = {
  zh: ['JoyhouseBot', 'JOY书童', 'JoyHouse', '橘室', 'AI助手', '个人知识库', '个人智能系统'],
  en: ['JoyhouseBot', 'JOY companion', 'JoyHouse', 'AI assistant', 'personal knowledge base', 'personal intelligence system'],
} as const

export const usePageSeo = (
  locale: Locale,
  title: string,
  description: string,
  path = '/',
  options: PageSeoOptions = {},
) => {
  const localePath = locale === 'en' ? `/en${path === '/' ? '' : path}` : path
  const alternatePath = locale === 'en' ? path : `/en${path === '/' ? '' : path}`
  const canonical = `https://joyhousebot.com${localePath}`
  const language = locale === 'zh' ? 'zh-CN' : 'en-US'
  const keywords = [...new Set([...BASE_KEYWORDS[locale], ...(options.keywords || [])])]
  const organizationId = 'https://joyhousebot.com/#organization'
  const websiteId = 'https://joyhousebot.com/#website'

  const schemaGraph: Record<string, unknown>[] = [
    {
      '@type': 'Organization',
      '@id': organizationId,
      name: 'JoyHouse',
      alternateName: ['JoyhouseBot', '橘室', 'JOY书童'],
      url: 'https://joyhousebot.com/',
      logo: 'https://joyhousebot.com/icon128.png',
      email: 'han@joyhouse.chat',
      slogan: locale === 'zh' ? '向外借智，向内生长。' : 'Grow within. Borrow intelligence outward.',
    },
    {
      '@type': 'WebSite',
      '@id': websiteId,
      name: 'JoyhouseBot',
      url: 'https://joyhousebot.com/',
      inLanguage: ['zh-CN', 'en-US'],
      publisher: { '@id': organizationId },
    },
    {
      '@type': 'WebPage',
      '@id': `${canonical}#webpage`,
      url: canonical,
      name: title,
      description,
      inLanguage: language,
      isPartOf: { '@id': websiteId },
      about: { '@id': organizationId },
    },
  ]

  if (options.entity === 'extension') {
    schemaGraph.push({
      '@type': 'SoftwareApplication',
      '@id': 'https://joyhousebot.com/extension#software',
      name: 'JoyhouseBot Extension',
      alternateName: 'JOY书童浏览器扩展',
      url: 'https://joyhousebot.com/extension',
      description,
      applicationCategory: 'ProductivityApplication',
      operatingSystem: 'Google Chrome 114+',
      browserRequirements: 'Requires Google Chrome 114 or later',
      featureList: locale === 'zh'
        ? ['网页正文与图片采集', '划词翻译', '整页双语阅读', '双语朗读', '生词本', '保存到私人书房']
        : ['Article and image capture', 'Selection translation', 'Bilingual reading', 'Bilingual text to speech', 'Vocabulary collection', 'Private JoyHouse library'],
      publisher: { '@id': organizationId },
    })
  }

  if (options.entity === 'agent') {
    schemaGraph.push({
      '@type': 'SoftwareSourceCode',
      '@id': 'https://joyhousebot.com/agent#source',
      name: 'JoyhouseBot Agent',
      url: 'https://joyhousebot.com/agent',
      description,
      codeRepository: 'https://github.com/JoyHouseLabs/joyhousebot',
      programmingLanguage: 'Python',
      runtimePlatform: 'Python and Docker',
      publisher: { '@id': organizationId },
    })
  }

  useSeoMeta({
    title,
    description,
    robots: 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1',
    ogTitle: title,
    ogDescription: description,
    ogType: 'website',
    ogUrl: canonical,
    ogImage: 'https://joyhousebot.com/og.png',
    ogLocale: locale === 'zh' ? 'zh_CN' : 'en_US',
    twitterCard: 'summary_large_image',
    twitterTitle: title,
    twitterDescription: description,
    twitterImage: 'https://joyhousebot.com/og.png',
  })

  useHead({
    htmlAttrs: { lang: locale === 'zh' ? 'zh-CN' : 'en' },
    meta: [
      { name: 'keywords', content: keywords.join(', ') },
      { name: 'author', content: 'JoyHouse' },
      { name: 'application-name', content: 'JoyhouseBot' },
    ],
    link: [
      { rel: 'canonical', href: canonical },
      { rel: 'alternate', hreflang: locale === 'zh' ? 'en' : 'zh-CN', href: `https://joyhousebot.com${alternatePath}` },
      { rel: 'alternate', hreflang: locale === 'zh' ? 'zh-CN' : 'en', href: canonical },
      { rel: 'alternate', hreflang: 'x-default', href: 'https://joyhousebot.com/' },
    ],
    script: [
      {
        key: `joyhousebot-schema-${localePath}`,
        type: 'application/ld+json',
        innerHTML: JSON.stringify({ '@context': 'https://schema.org', '@graph': schemaGraph }),
      },
    ],
  })
}
