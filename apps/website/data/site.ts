import type { Locale } from '~/utils/routes'

export const siteCopy = {
  zh: {
    language: 'English',
    nav: { agent: 'AI-native 工作中心', extension: '浏览器外挂', hardware: 'Hardware', docs: '文档' },
    openHouse: '认识 JoyHouse',
    hero: {
      eyebrow: 'JOYHOUSEBOT · AI-NATIVE 智能工作中心',
      title: '让自然语言目标进入可治理的真实执行',
      copy: 'JoyhouseBot 是面向个人与企业的 AI-native 智能工作中心：把自然语言目标转化为可追踪、可协同、可治理的业务执行。从搜索、研究、富化到长任务、多 Agent 协作与人工反馈，过程可观测、可回放、可持续优化。它以智能外挂的方式接入你已在使用的浏览器、消息工具和业务系统。',
      install: '从浏览器接入',
      agent: '打开开源 Agent',
      coming: '下载 Chrome 扩展',
      proof: ['开源、可扩展的 Agent Runtime', '浏览器、消息与工具的智能接入层', '数据、成果与反馈回到 JoyHouse'],
    },
    products: {
      eyebrow: 'ONE RUNTIME, THREE ENTRY POINTS',
      title: '一个智能工作中心，三种接入方式',
      copy: '浏览器扩展看见和带回信息，Agent Runtime 把目标编排为可追踪的任务和协作，未来硬件让智能自然在场。载体会变化，但身份、权限、上下文、执行记录和成果链条保持连续。',
      items: [
        { key: 'agent', status: '开源 · 工作中心', name: 'AI-native 工作中心', summary: '连接渠道、模型、记忆、知识、插件和工具，让自然语言目标进入可追踪、多 Agent 协同与人工反馈的执行闭环。', action: '查看开源工作中心' },
        { key: 'extension', status: '开源 · 现可安装', name: 'Browser Extension', summary: '贴在阅读和研究现场：采集网页、翻译、朗读，并把值得留下的内容送进私人书房。', action: '查看浏览器外挂' },
        { key: 'hardware', status: '未来计划', name: 'Hardware', summary: '一台能放在桌上的 JOY：探索语音、状态、边缘计算与真实工作现场的智能外设。', action: '了解硬件方向' },
      ],
    },
    extension: {
      eyebrow: 'BROWSER OVERLAY',
      title: '浏览器上的第一块智能外挂',
      copy: '当你读到一篇文章、遇到一个陌生词或需要留下一个证据时，JOY 不要求你跳出当前页面。它在阅读现场采集、翻译、朗读和整理，再把经过你确认的资料带回 JoyHouse。',
      features: [
        ['抓取完整正文', '针对微信公众号、X 和通用网页使用站点适配器，保留结构、来源和正文图片。'],
        ['边读边理解', '划词翻译、整页双语、朗读原文与译文，让外语内容保持在原有阅读上下文中。'],
        ['进入成果链条', '收藏、生词、译文和阅读资料进入 JoyHouse，可继续形成判断、行动、作品与可分享的成果。'],
      ],
      caption: '选择文字后直接翻译、朗读、存生词或保存到资料库。',
    },
    flow: {
      eyebrow: 'AN INTELLIGENT LAYER, NOT ANOTHER SILO',
      title: '自然语言目标，如何变成可治理的真实执行',
      copy: 'JoyhouseBot 负责把分散的工作现场连接起来；JoyHouse 负责保存长期数据、成果与反馈。智能不只生成答案，而是在授权范围内把任务变成能被追踪、协同、复盘和持续优化的工作。',
      steps: [
        ['01', '接入现场', '浏览器、消息、文件、知识库和已有工具，仍按你熟悉的方式工作。'],
        ['02', '建立可追踪任务', '只连接明确授权的数据、知识和上下文，把模糊目标拆成可确认、可观察的执行路径。'],
        ['03', '协同完成长任务', 'Agent 结合记忆、检索、技能、插件和工具；需要时由多个 Agent 分工，并保留过程记录。'],
        ['04', '回收成果与人工反馈', '把结果、行动、作品、发布记录与反馈带回 JoyHouse，支持回放、复盘和下一次优化。'],
      ],
    },
    scenarios: {
      eyebrow: 'FOR PEOPLE AND ORGANIZATIONS',
      title: '一个人的智能外挂，也是一支团队的智能执行层',
      copy: '同一个工作中心，服务两种尺度：个人用它把积累变成作品和分享；团队用它把知识、渠道和工具接成可追踪、可协同、可治理的工作流。',
      personal: { label: 'PERSONAL', title: '贴在你的学习、研究与创作旁', copy: '从网页、对话和日常记录接住真实输入，持续理解你的目标，把想法推进为行动、作品和可以分享的价值。' },
      organization: { label: 'ORGANIZATION', title: '把自然语言目标接进团队的真实执行', copy: '连接多渠道、知识库、插件和工具；让搜索、研究、富化、长任务和多 Agent 协作进入可观察的执行链，并通过权限、确认和人工反馈持续治理。' },
    },
    agent: {
      eyebrow: 'OPEN-SOURCE AGENT RUNTIME',
      title: 'AI-native 智能工作中心，不止是 Agent Runtime',
      copy: 'JoyhouseBot Agent 不是又一个聊天壳，而是可部署、可配置、可扩展的工作中心：让 Agent 把自然语言目标推进为可追踪、多 Agent 协作、可回放并有人类反馈的真实执行。',
      bullets: ['多渠道、多 Agent 与长任务协作', '记忆、知识库与检索分层', '技能、插件、工具与执行记录', '配置、权限、确认与可选沙箱'],
      action: '查看开源项目',
    },
    house: {
      eyebrow: 'JOYHOUSE · THE DATA AND OUTCOME HOME',
      title: '智能向外工作，数据与成果回到家园',
      copy: 'JoyHouse 不是另一个收藏夹，而是个人数据、判断、行动、作品和真实反馈的长期归处。JoyhouseBot 在外部世界接入和执行，JoyHouse 让每一次完成都能沉淀、复用并在你确认后分享出去。',
      action: '查看数据与智能愿景',
    },
    hardware: {
      eyebrow: 'HARDWARE · FUTURE INTERFACE',
      title: '下一步：一台能放在桌上的 JOY',
      copy: '我们正在探索手办式智能工作外设：同一运行时在语音、状态、边缘计算与真实环境中的新入口。先验证明确唤醒、可见状态、授权边界和数据连续性，再定义具体设备形态。',
      action: '联系硬件合作',
    },
    privacy: {
      eyebrow: 'DATA BOUNDARIES BY DESIGN',
      title: '数据不是燃料，授权才是边界',
      copy: '个人资料默认私有；应用只读取本次明确选择的上下文；重要执行与对外发布必须确认。对组织而言，范围、权限和可观察性同样应是智能运行时的一部分。',
      action: '阅读隐私政策',
    },
    final: {
      title: '把智能接在真正发生工作的地方',
      copy: '从浏览器的一条资料开始，或从部署一个开源智能工作中心开始。让数据被理解，目标进入真实执行，成果被带回并走向分享。',
      install: '获取浏览器外挂',
      support: '查看开源 Agent',
    },
    footer: {
      summary: 'JoyhouseBot 是 JoyHouse 的智能外挂产品线：用浏览器扩展、开源 Agent Runtime 与未来硬件，把数据和智能带到真实的个人与组织工作现场。',
      product: '产品形态',
      company: '生态与愿景',
      contact: '支持与联系',
      rights: 'JoyHouse（橘室）· 向内生长，向外分享。',
    },
  },
  en: {
    language: '中文',
    nav: { agent: 'AI-native work center', extension: 'Browser overlay', hardware: 'Hardware', docs: 'Docs' },
    openHouse: 'Explore JoyHouse',
    hero: {
      eyebrow: 'JOYHOUSEBOT · AI-NATIVE INTELLIGENT WORK CENTER',
      title: 'Turn natural-language goals into governable execution',
      copy: 'JoyhouseBot is an AI-native intelligent work center for people and organizations. It turns natural-language goals into execution that can be tracked, coordinated and governed—from search, research and enrichment to long-running tasks, multi-agent collaboration and human feedback. It attaches as an intelligence overlay to the browsers, channels and systems you already use.',
      install: 'Connect in the browser',
      agent: 'Open the Agent Runtime',
      coming: 'Download Chrome extension',
      proof: ['Open, extensible Agent Runtime', 'An intelligent layer for browsers, channels and tools', 'Data, outcomes and feedback return to JoyHouse'],
    },
    products: {
      eyebrow: 'ONE RUNTIME, THREE ENTRY POINTS',
      title: 'One intelligent work center, three ways to connect',
      copy: 'The browser extension sees and brings back information. The Agent Runtime orchestrates goals into trackable tasks and collaboration. Future hardware makes intelligence naturally present. Interfaces change; identity, permissions, context, execution records and outcome lineage stay connected.',
      items: [
        { key: 'agent', status: 'Open source · work center', name: 'AI-native Work Center', summary: 'Connect channels, models, memory, knowledge, plugins and tools so natural-language goals enter a trackable loop of multi-agent collaboration and human feedback.', action: 'Explore the work center' },
        { key: 'extension', status: 'Open source · available', name: 'Browser Extension', summary: 'An overlay for the reading and research moment: capture, translate and listen, then bring what matters into your private library.', action: 'Explore the browser overlay' },
        { key: 'hardware', status: 'Future interface', name: 'Hardware', summary: 'A JOY that sits on your desk: exploring voice, state, edge computing and an intelligent peripheral for real work.', action: 'Our hardware direction' },
      ],
    },
    extension: {
      eyebrow: 'BROWSER OVERLAY',
      title: 'The first intelligence overlay: right in your browser',
      copy: 'When an article, unfamiliar word or piece of evidence matters, JOY does not make you leave the page. It captures, translates, reads aloud and organizes in context—then brings the material home when you choose.',
      features: [
        ['Capture complete articles', 'Site adapters for WeChat, X and the open web retain structure, sources and inline images.'],
        ['Understand as you read', 'Selection translation, bilingual pages and dual-language speech keep you in the original reading context.'],
        ['Enter an outcome chain', 'Pages, words and translations enter JoyHouse to become judgments, actions, work and shareable outcomes.'],
      ],
      caption: 'Translate a selection, listen, save a word or send it to your library.',
    },
    flow: {
      eyebrow: 'AN INTELLIGENT LAYER, NOT ANOTHER SILO',
      title: 'How a natural-language goal becomes governable execution',
      copy: 'JoyhouseBot connects distributed work contexts. JoyHouse keeps long-term data, outcomes and feedback. Intelligence does more than generate an answer: within permission, it turns work into something trackable, collaborative, reviewable and continuously improvable.',
      steps: [
        ['01', 'Attach to the work', 'Browsers, messages, files, knowledge bases and existing tools continue to work as you know them.'],
        ['02', 'Make the work trackable', 'Connect only explicitly authorized data, knowledge and context; turn a vague goal into a path that can be confirmed and observed.'],
        ['03', 'Coordinate long-running work', 'Combine memory, retrieval, skills, plugins and tools; bring multiple agents in when needed and retain the process record.'],
        ['04', 'Return outcomes and human feedback', 'Bring work, actions, publication records and feedback into JoyHouse for replay, review and better future decisions.'],
      ],
    },
    scenarios: {
      eyebrow: 'FOR PEOPLE AND ORGANIZATIONS',
      title: 'An intelligence overlay for one person—or an execution layer for a team',
      copy: 'The same work center works at two scales: people turn what they accumulate into work and sharing; teams connect knowledge, channels and tools into workflows that can be tracked, coordinated and governed.',
      personal: { label: 'PERSONAL', title: 'Alongside your learning, research and creation', copy: 'Meet real input from the web, conversation and daily life; understand your aims over time; turn ideas into action, work and value you choose to share.' },
      organization: { label: 'ORGANIZATION', title: 'Bring natural-language goals into real team execution', copy: 'Connect channels, knowledge, plugins and tools. Put search, research, enrichment, long-running work and multi-agent collaboration into an observable execution chain, then govern it through permissions, confirmation and human feedback.' },
    },
    agent: {
      eyebrow: 'OPEN-SOURCE AGENT RUNTIME',
      title: 'An AI-native work center, not merely an Agent Runtime',
      copy: 'JoyhouseBot Agent is not another chat shell. It is a deployable, configurable and extensible work center that moves natural-language goals into trackable, multi-agent, replayable execution with human feedback.',
      bullets: ['Multi-channel, multi-agent and long-running work', 'Layered memory, knowledge and retrieval', 'Skills, plugins, tools and execution records', 'Configuration, permissions, confirmation and optional sandboxing'],
      action: 'View open-source project',
    },
    house: {
      eyebrow: 'JOYHOUSE · THE DATA AND OUTCOME HOME',
      title: 'Intelligence works outward; data and outcomes return home',
      copy: 'JoyHouse is more than a bookmark store. It is the long-term home for personal data, judgment, action, work and real feedback. JoyhouseBot connects and operates in the outside world; JoyHouse lets every completion be retained, reused and shared with your confirmation.',
      action: 'Explore the data & intelligence vision',
    },
    hardware: {
      eyebrow: 'HARDWARE · FUTURE INTERFACE',
      title: 'Next: a JOY that can sit on your desk',
      copy: 'We are exploring a collectible intelligent-work peripheral: a new entry to the same runtime for voice, state, edge computation and real environments. We will validate explicit wake, visible state, permission boundaries and data continuity before defining the device.',
      action: 'Discuss hardware partnerships',
    },
    privacy: {
      eyebrow: 'DATA BOUNDARIES BY DESIGN',
      title: 'Data is not fuel. Authorization is the boundary.',
      copy: 'Personal material is private by default. Apps read only the context explicitly selected for a task. Important execution and external publishing require confirmation. For organizations, scope, permission and observability belong in the runtime too.',
      action: 'Read our privacy policy',
    },
    final: {
      title: 'Attach intelligence where work really happens',
      copy: 'Start with a single source in the browser, or deploy an open intelligent work center. Let data be understood, goals enter real execution, and outcomes return to a place where they can become something worth sharing.',
      install: 'Get the browser overlay',
      support: 'Explore the open Agent',
    },
    footer: {
      summary: 'JoyhouseBot is the intelligence-overlay product line from JoyHouse: browser extensions, an open Agent Runtime and future hardware that bring data and intelligence to real work for people and organizations.',
      product: 'Product forms',
      company: 'Ecosystem & vision',
      contact: 'Support & contact',
      rights: 'JoyHouse · Grow inward. Create and share outward.',
    },
  },
} as const

export type SiteCopy = typeof siteCopy[Locale]

export const productMeta = {
  extension: {
    zh: {
      eyebrow: 'JOYHOUSEBOT EXTENSION',
      title: '网页阅读入口，也是你的资料入口',
      intro: '在当前页面完成采集、翻译、朗读和保存，让有价值的内容从浏览器进入长期个人系统。',
      highlights: [
        ['完整采集', '提取正文、标题、来源和图片，支持微信公众号、X 与通用网页。'],
        ['双语阅读', '划词翻译、整页双语、朗读原文和译文，自动识别语言方向。'],
        ['知识沉淀', '存生词、存资料库、打开书房，继续做 AI 解读、备注和行动。'],
        ['常驻侧栏', 'Chrome Side Panel 可以靠右长期存在，切换网页后仍能继续使用。'],
      ],
    },
    en: {
      eyebrow: 'JOYHOUSEBOT EXTENSION',
      title: 'A reading interface and a doorway to your library',
      intro: 'Capture, translate, listen and save without leaving the page—then carry valuable material from the browser into your long-term personal system.',
      highlights: [
        ['Complete capture', 'Extract titles, sources, article structure and images from WeChat, X and the open web.'],
        ['Bilingual reading', 'Translate selections or full pages, listen in both languages and detect direction automatically.'],
        ['Knowledge that stays', 'Save words and sources to JoyHouse for AI interpretation, notes and action.'],
        ['Persistent side panel', 'Keep JoyhouseBot at the right side of Chrome while moving between pages.'],
      ],
    },
  },
  agent: {
    zh: {
      eyebrow: 'JOYHOUSEBOT AGENT',
      title: '开源 AI-native 智能工作中心',
      intro: '一个 Python 原生、可部署可扩展的 Agent Runtime：连接模型、渠道、工具、知识、记忆与自动化，在个人和组织已有系统旁持续运行，而不是另起一个孤立聊天窗口。',
      highlights: [
        ['可运行的智能层', '构建上下文、调用模型、执行工具并回写结果，让 Agent 能够持续推进任务。'],
        ['接入已有工作现场', '支持 Telegram、Discord、Slack、飞书、钉钉、邮件等渠道，并可通过插件扩展。'],
        ['数据与智能分层', '分层记忆、全文检索和向量检索维护长期上下文，区分个人经验与可复用知识。'],
        ['可配置的安全边界', '工具参数校验、权限控制、敏感信息过滤与可选 Docker 沙箱，让能力按边界运行。'],
      ],
    },
    en: {
      eyebrow: 'JOYHOUSEBOT AGENT',
      title: 'An open AI-native intelligent work center',
      intro: 'A Python-native, deployable and extensible Agent Runtime that connects models, channels, tools, knowledge, memory and automation alongside the systems people and organizations already use—not in another isolated chat window.',
      highlights: [
        ['An intelligence layer that runs', 'Build context, call models, use tools and write results back so agents can keep work moving.'],
        ['Attach to existing work', 'Connect Telegram, Discord, Slack, Feishu, DingTalk, email and more, then extend with plugins.'],
        ['Data and intelligence in layers', 'Layered memory, full-text search and vector retrieval distinguish long-term experience from reusable knowledge.'],
        ['Configurable boundaries', 'Schema validation, permissions, secret filtering and optional Docker sandboxes keep capabilities within their limits.'],
      ],
    },
  },
  hardware: {
    zh: {
      eyebrow: 'JOYHOUSEBOT HARDWARE',
      title: '一台能放在桌上的 JOY，未来的智能工作外设',
      intro: '我们正在探索手办式 JoyhouseBot 硬件：不是另一套孤立设备，而是同一 Runtime 在语音、边缘计算和日常环境中的新载体。当前处于探索与验证阶段。',
      highlights: [
        ['自然接入', '减少打开应用和切换界面的负担，让表达、记录和协作更顺手。'],
        ['连续的运行时', '在授权下连接同一身份、数据和技能体系，而不是制造新的数据孤岛。'],
        ['可见的边界', '明确的唤醒、状态反馈和隐私控制，避免不透明的常驻采集。'],
        ['开放验证', '欢迎围绕设备、语音、边缘计算和真实场景开展合作与验证。'],
      ],
    },
    en: {
      eyebrow: 'JOYHOUSEBOT HARDWARE',
      title: 'A JOY for your desk: a future intelligent-work peripheral',
      intro: 'We are exploring a collectible JoyhouseBot device—not another isolated gadget, but a new form for the same Runtime across voice, edge computation and everyday environments. It is currently in exploration and validation.',
      highlights: [
        ['Natural access', 'Reduce the friction of opening apps and switching screens when you want to express, remember or collaborate.'],
        ['A continuous runtime', 'Connect the same identity, data and skill system with permission instead of creating another silo.'],
        ['Visible boundaries', 'Clear wake states, feedback and privacy controls—never opaque ambient collection.'],
        ['Open validation', 'We welcome partnerships around devices, voice, edge computing and real-world scenarios.'],
      ],
    },
  },
} as const
