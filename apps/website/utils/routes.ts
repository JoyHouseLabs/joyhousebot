export type Locale = 'zh' | 'en'

export const localPath = (locale: Locale, path = '/') => {
  const normalized = path === '/' ? '' : path
  return locale === 'en' ? `/en${normalized}` || '/en' : normalized || '/'
}

export const alternatePath = (locale: Locale, path = '/') => localPath(locale === 'zh' ? 'en' : 'zh', path)
