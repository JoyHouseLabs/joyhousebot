export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: false },
  modules: ['@nuxtjs/tailwindcss', '@nuxtjs/sitemap'],
  css: ['~/assets/css/main.css'],

  app: {
    head: {
      htmlAttrs: { lang: 'zh-CN' },
      meta: [
        { charset: 'utf-8' },
        { name: 'viewport', content: 'width=device-width, initial-scale=1' },
        { name: 'theme-color', content: '#6b5cf6' },
      ],
      link: [{ rel: 'icon', type: 'image/png', href: '/icon128.png' }],
    },
  },

  site: { url: 'https://joyhousebot.com', name: 'JoyhouseBot' },
  routeRules: {
    '/**': { prerender: true },
  },
  nitro: {
    prerender: {
      crawlLinks: true,
      routes: [
        '/', '/extension', '/agent', '/hardware', '/docs', '/privacy', '/support', '/terms',
        '/en', '/en/extension', '/en/agent', '/en/hardware', '/en/docs', '/en/privacy', '/en/support', '/en/terms',
      ],
    },
  },

  runtimeConfig: {
    public: {
      appUrl: process.env.NUXT_PUBLIC_APP_URL || 'https://app.joyhouse.chat',
      chromeStoreUrl: process.env.NUXT_PUBLIC_CHROME_STORE_URL || '',
      extensionRepoUrl: process.env.NUXT_PUBLIC_EXTENSION_REPO_URL || 'https://github.com/JoyHouseLabs/ext-joyhousebot',
      extensionDownloadUrl: process.env.NUXT_PUBLIC_EXTENSION_DOWNLOAD_URL || 'https://github.com/JoyHouseLabs/ext-joyhousebot/releases/latest/download/joyhousebot-chrome-extension.zip',
      extensionReleasesUrl: process.env.NUXT_PUBLIC_EXTENSION_RELEASES_URL || 'https://github.com/JoyHouseLabs/ext-joyhousebot/releases',
      agentUrl: process.env.NUXT_PUBLIC_AGENT_URL || 'https://github.com/JoyHouseLabs/joyhousebot',
      agentDocsUrl: process.env.NUXT_PUBLIC_AGENT_DOCS_URL || 'https://github.com/JoyHouseLabs/joyhousebot/tree/main/docs',
      visionUrl: process.env.NUXT_PUBLIC_VISION_URL || 'https://joyhouse.chat/vision',
      supportEmail: process.env.NUXT_PUBLIC_SUPPORT_EMAIL || 'han@joyhouse.chat',
    },
  },
})
