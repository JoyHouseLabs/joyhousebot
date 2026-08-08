<script setup lang="ts">
import { ArrowLeft, Mail } from 'lucide-vue-next'
import { localPath, type Locale } from '~/utils/routes'

type PageType = 'privacy' | 'support' | 'terms'
const props = defineProps<{ locale: Locale; type: PageType }>()
const config = useRuntimeConfig()

const pages = {
  zh: {
    privacy: {
      title: 'JoyhouseBot 隐私政策',
      intro: '我们重视你的个人数据与阅读隐私。本政策说明 JoyhouseBot 浏览器扩展和相关服务如何处理数据。',
      updated: '更新日期：2026年7月28日',
      sections: [
        ['1. 我们处理哪些数据', ['当你主动使用抓取、收藏、翻译、朗读、生词或登录功能时，我们会处理完成该功能所必需的网页标题、正文、图片、选中文字、页面地址、账号标识及功能设置。', '账号密码只会通过 HTTPS 发送给 JoyHouse 完成登录，扩展不会保存密码。登录令牌只保存在 Chrome 的内存会话存储中；浏览器本地存储仅保存账号显示名、同意记录和必要偏好。退出账号、浏览器会话结束、登录失效或撤回同意时会清除令牌。', '服务端可能生成必要的访问、错误和安全日志，例如请求时间、接口状态、IP 地址及设备或浏览器基本信息，用于运行、安全和故障排查。']],
        ['2. 数据如何使用', ['数据仅用于提供你主动请求的网页采集、翻译、语音、资料保存、同步与账号服务，不出售个人信息，不用于个性化、重定向或兴趣广告。', '保存到 JoyHouse 的资料默认是私有内容，除非你在产品中主动执行公开分享。我们不会在后台持续记录或出售你的浏览历史。']],
        ['3. 显著披露与用户控制', ['扩展首次连接账号或处理网页数据前，会在扩展界面说明所处理的数据及用途，并要求你主动同意。你可以在“设置 → 隐私与数据”查看政策或撤回同意。', '撤回同意会退出当前账号并停止翻译、采集、同步等需要数据处理的功能；再次同意前，扩展不会向 JoyHouse 发送网页或账号数据。数据处理实践发生实质变化时，我们会再次显著披露并按要求取得同意。']],
        ['4. 网页访问权限', ['扩展需要访问 HTTP 和 HTTPS 网页，以便在你主动操作时读取选中文字、提取正文和图片、插入双语译文并显示交互界面。扩展不会为了广告、画像或与核心功能无关的分析收集浏览活动。']],
        ['5. 服务提供方', ['部分智能、翻译或语音能力可能调用由 JoyHouse 配置的模型、翻译或语音服务。我们只发送完成当前请求所需的文字和参数；网页图片只在你主动采集或保存时处理。服务提供方只能按提供相应功能和其数据保护条款处理这些数据。']],
        ['6. Chrome Web Store Limited Use', ['JoyhouseBot 对 Chrome 权限和用户数据的使用遵守 Chrome Web Store User Data Policy 及 Limited Use 要求：只用于提供或改进扩展公开说明的单一用途和用户可见功能。', '除提供或改进功能、履行法律义务、保障安全，或在合并收购中依法转移外，我们不会向第三方转移用户数据。我们不会用用户数据投放个性化广告。', '除非得到你针对具体支持请求的明确许可，或为安全调查、履行法律义务，或在聚合且匿名化后用于内部运行，我们不允许工作人员读取你的私人内容。']],
        ['7. 数据保存与删除', ['登录令牌只在当前浏览器会话期间保存在内存中，并在你退出、撤回同意、令牌失效、浏览器会话结束或卸载扩展时清除。资料、生词、翻译历史和账号数据保留至你主动删除或申请删除账号，以维持你请求的长期书房功能。', '访问、错误和安全日志仅在提供服务、排查故障、防止滥用及满足法律要求所需期间保留，之后按运维策略轮换、匿名化或删除。你可以在 JoyHouse 删除资料、生词和相关记录，也可以通过支持邮箱申请删除账号及关联数据。']],
        ['8. 安全', ['扩展只连接固定的正式 HTTPS 服务。数据通过 HTTPS 传输，账号内容通过身份验证与访问控制保护。任何互联网服务都无法保证绝对安全，我们会持续最小化数据处理和权限范围。']],
        ['9. 联系我们', [`隐私问题、数据访问或删除请求请发送邮件至 ${config.public.supportEmail}。`]],
      ],
    },
    support: {
      title: 'JoyhouseBot 帮助与常见问题',
      intro: '了解如何安装扩展、连接 JoyHouse，并找到采集、翻译、朗读和同步的常见问题答案。',
      updated: '支持邮箱：' + config.public.supportEmail,
      sections: [
        ['常见问题', ['扩展无法读取网页时怎么办？请确认当前页面为普通 HTTP/HTTPS 页面，并重新打开侧栏后重试。部分登录页、受限页面或站点动态结构可能无法完整提取。', '资料是否会公开？不会。保存到 JoyHouse 的资料默认私有；只有你主动确认发布或分享时，内容才会对外可见。', '可以在多个设备使用吗？可以。使用同一个 JoyHouse 账号登录后，已保存的网页、生词和翻译记录会同步到该账号。']],
        ['安装浏览器扩展', ['Chrome 商店审核通过后，可从本站安装入口进入商店。内测阶段可下载发布包，在 chrome://extensions 打开开发者模式后选择“加载已解压的扩展程序”。']],
        ['登录与同步', ['扩展会优先检查已打开的 app.joyhouse.chat 登录状态。你也可以在扩展中使用账号密码或微信扫码登录。', '保存的网页、生词和翻译记录会同步到当前 JoyHouse 账号。']],
        ['抓取与收藏', ['“抓取正文”会提取当前文章并直接写入书房；“收藏本页”会保存正文，提取失败时退回为链接收藏。', '微信公众号和 X 页面使用专用适配器；其他站点使用通用正文提取。']],
        ['翻译和朗读', ['选中文字后点击“译”，可以查看译文、朗读原文、朗读翻译、存生词或存入资料库。整页双语翻译可从右键菜单启动。']],
        ['问题反馈', [`请将问题页面地址、操作步骤、扩展版本和截图发送到 ${config.public.supportEmail}。请勿在邮件中发送密码或访问令牌。`]],
      ],
    },
    terms: {
      title: 'JoyhouseBot 服务条款',
      intro: '使用 JoyhouseBot 即表示你同意以下服务规则。',
      updated: '生效日期：2026年7月28日',
      sections: [
        ['1. 服务范围', ['JoyhouseBot 提供网页采集、语言辅助、知识保存、Agent 和相关智能功能。部分功能可能处于测试阶段并持续调整。']],
        ['2. 账号责任', ['你应妥善保管账号凭据，并对账号下的操作负责。发现未经授权的使用时请及时联系我们。']],
        ['3. 内容与版权', ['你应确保有权处理和保存所提交的内容。JoyhouseBot 不因提供技术工具而取得你的内容所有权。']],
        ['4. 合理使用', ['不得利用服务侵犯他人权益、传播违法内容、攻击系统、规避安全限制或实施未经授权的自动化访问。']],
        ['5. 智能结果', ['模型生成、翻译和自动提取可能存在错误。涉及医疗、法律、金融或其他重要决定时，应由合格专业人士复核。']],
        ['6. 服务变化', ['我们可能为安全、合规和产品改进调整功能。重大变化会通过合理方式通知。']],
        ['7. 联系方式', [`服务相关问题请联系 ${config.public.supportEmail}。`]],
      ],
    },
  },
  en: {
    privacy: {
      title: 'JoyhouseBot Privacy Policy',
      intro: 'We respect your personal data and reading privacy. This policy explains how the JoyhouseBot extension and related services process data.',
      updated: 'Last updated: July 28, 2026',
      sections: [
        ['1. Data we process', ['When you actively use capture, save, translation, speech, vocabulary or sign-in features, we process the page title, article text, images, selected text, page URL, account identifier and settings necessary to complete that request.', 'Credentials are sent to JoyHouse over HTTPS only to sign you in; the extension does not store your password. The session token is kept only in Chrome in-memory session storage. Local browser storage keeps the account display name, consent record and necessary preferences. The token is cleared when you sign out, the browser session ends, the login expires or you revoke consent.', 'The service may create necessary access, error and security logs, such as request time, endpoint status, IP address and basic device or browser information, for operations, security and troubleshooting.']],
        ['2. How we use data', ['Data is used only for the web capture, translation, speech, library, synchronization and account features you request. We do not sell personal information or use it for personalized, retargeted or interest-based advertising.', 'Items saved to JoyHouse are private by default unless you explicitly publish or share them. We do not continuously record or sell your browsing history.']],
        ['3. Prominent disclosure and control', ['Before connecting an account or processing page data for the first time, the extension explains the data and purposes in its own interface and requires an affirmative action. You can review the policy or revoke consent under Settings → Privacy & data.', 'Revoking consent signs out the current account and disables translation, capture and synchronization that require data handling. The extension will not send page or account data to JoyHouse until you consent again. Material changes to data practices will be prominently disclosed and consented to as required.']],
        ['4. Website access permission', ['Access to HTTP and HTTPS pages lets the extension read a selection, extract article text and images, insert bilingual text and display its interface when you actively use a feature. The extension does not collect browsing activity for advertising, profiling or analytics unrelated to its core purpose.']],
        ['5. Service providers', ['Some AI, translation or speech features may use model, translation or voice providers configured by JoyHouse. We send only the text and parameters needed for the current request. Page images are processed only when you actively capture or save them. Providers may process this data only to deliver the requested capability and under their data protection terms.']],
        ['6. Chrome Web Store Limited Use', ['JoyhouseBot uses Chrome permissions and user data in accordance with the Chrome Web Store User Data Policy and Limited Use requirements, solely to provide or improve the disclosed single purpose and user-facing features.', 'We do not transfer user data except as necessary to provide or improve those features, comply with law, protect security, or complete a lawful merger or acquisition. We do not use user data for personalized advertising.', 'Humans are not permitted to read private content except with your specific consent for support, for security investigations, to comply with law, or after aggregation and anonymization for internal operations.']],
        ['7. Retention and deletion', ['The session token remains only in memory for the current browser session and is cleared when you sign out, revoke consent, the token expires, the browser session ends or you uninstall the extension. Saved sources, words, translation history and account data remain until you delete them or request account deletion, supporting the long-term library you requested.', 'Access, error and security logs are retained only as long as needed to operate the service, troubleshoot issues, prevent abuse and meet legal requirements, then rotated, anonymized or deleted under operational policy. You can delete saved items in JoyHouse or request deletion of your account and associated data by email.']],
        ['8. Security', ['The store extension connects only to fixed production HTTPS services. Data is transmitted over HTTPS and account content is protected with authentication and access controls. No internet service can guarantee absolute security, and we continue to minimize data handling and permission scope.']],
        ['9. Contact', [`For privacy questions, data access or deletion requests, email ${config.public.supportEmail}.`]],
      ],
    },
    support: {
      title: 'JoyhouseBot Help & FAQ',
      intro: 'Learn how to install the extension, connect JoyHouse and find answers about capture, translation, speech and sync.',
      updated: 'Support: ' + config.public.supportEmail,
      sections: [
        ['Frequently asked questions', ['What if the extension cannot read a page? Confirm that it is a standard HTTP/HTTPS page, reopen the side panel and try again. Some sign-in pages, restricted pages or dynamic site structures cannot be extracted completely.', 'Are saved items public? No. Items saved to JoyHouse are private by default. Content becomes public only when you explicitly confirm a publication or share action.', 'Can I use it on more than one device? Yes. When you sign in with the same JoyHouse account, saved pages, words and translation records sync to that account.']],
        ['Install the extension', ['After Chrome Web Store approval, use the installation link on this site. During private testing, download the release, enable Developer mode at chrome://extensions and choose “Load unpacked.”']],
        ['Sign in and sync', ['The extension first checks an existing app.joyhouse.chat session. You can also sign in with account credentials or WeChat QR login.', 'Saved pages, words and translation history sync to the active JoyHouse account.']],
        ['Capture and save', ['“Capture article” extracts the page and writes it to your library. “Save this page” saves the content and falls back to a link when extraction is unavailable.', 'WeChat and X use dedicated site adapters; other sites use general article extraction.']],
        ['Translation and speech', ['Select text and click the translate control to view a translation, listen to either language, save a word or save the selection. Full-page bilingual mode is available from the context menu.']],
        ['Report an issue', [`Email the page URL, steps, extension version and a screenshot to ${config.public.supportEmail}. Never include a password or access token.`]],
      ],
    },
    terms: {
      title: 'JoyhouseBot Terms of Service',
      intro: 'By using JoyhouseBot, you agree to the following service terms.',
      updated: 'Effective: July 28, 2026',
      sections: [
        ['1. Service scope', ['JoyhouseBot provides web capture, language assistance, knowledge storage, agent and related intelligent features. Some features may be experimental and evolve over time.']],
        ['2. Account responsibility', ['Keep your credentials secure and take responsibility for activity under your account. Contact us promptly if you discover unauthorized use.']],
        ['3. Content and copyright', ['You must have the right to process and save submitted content. JoyhouseBot does not obtain ownership of your content by providing technical tools.']],
        ['4. Acceptable use', ['Do not use the service to violate rights, distribute illegal content, attack systems, bypass security limits or perform unauthorized automated access.']],
        ['5. Intelligent output', ['Generated, translated or extracted output may contain errors. Medical, legal, financial and other important decisions should be reviewed by qualified professionals.']],
        ['6. Service changes', ['We may adjust features for security, compliance and product improvement, with reasonable notice for material changes.']],
        ['7. Contact', [`For service questions, contact ${config.public.supportEmail}.`]],
      ],
    },
  },
} as const

const page = computed(() => pages[props.locale][props.type])
</script>

<template>
  <div>
    <SiteHeader :locale="locale" :path="`/${type}`" />
    <main class="container-x py-16 sm:py-24">
      <NuxtLink :to="localPath(locale)" class="inline-flex items-center gap-2 text-sm font-bold text-bot-muted hover:text-bot"><ArrowLeft :size="16" />{{ locale === 'zh' ? '返回首页' : 'Back home' }}</NuxtLink>
      <div class="mt-12 grid gap-10 lg:grid-cols-[1fr_18rem]">
        <article class="surface-card p-7 sm:p-10 lg:p-12">
          <p class="eyebrow">JOYHOUSEBOT</p>
          <h1 class="mt-4 text-4xl font-bold tracking-tight sm:text-5xl">{{ page.title }}</h1>
          <p class="mt-5 max-w-3xl text-lg leading-8 text-bot-muted">{{ page.intro }}</p>
          <p class="mt-3 text-sm text-bot-faint">{{ page.updated }}</p>
          <div class="legal-copy mt-10 border-t border-black/5 pt-2">
            <section v-for="section in page.sections" :key="section[0]">
              <h2>{{ section[0] }}</h2>
              <p v-for="paragraph in section[1]" :key="paragraph">{{ paragraph }}</p>
            </section>
          </div>
        </article>
        <aside class="h-fit rounded-card bg-bot-soft p-6 lg:sticky lg:top-24">
          <div class="grid h-11 w-11 place-items-center rounded-2xl bg-white text-bot"><Mail :size="21" /></div>
          <h2 class="mt-5 font-bold">{{ locale === 'zh' ? '需要帮助？' : 'Need help?' }}</h2>
          <p class="mt-2 text-sm leading-6 text-bot-muted">{{ locale === 'zh' ? '联系我们的支持邮箱，我们会尽快回复。' : 'Email support and we will get back to you as soon as possible.' }}</p>
          <a :href="`mailto:${config.public.supportEmail}`" class="mt-5 block break-all text-sm font-bold text-bot">{{ config.public.supportEmail }}</a>
        </aside>
      </div>
    </main>
    <SiteFooter :locale="locale" />
  </div>
</template>
