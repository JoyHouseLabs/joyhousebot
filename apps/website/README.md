# JoyhouseBot Website

JoyhouseBot 产品官网，覆盖 Extension、Agent 与 Hardware 三种产品形态，并提供中英双语、隐私政策和支持页面。

```bash
pnpm install
pnpm dev
pnpm generate
```

公开配置：

- `NUXT_PUBLIC_APP_URL`：JoyHouse 应用地址，默认 `https://app.joyhouse.chat`
- `NUXT_PUBLIC_CHROME_STORE_URL`：Chrome 商店地址；为空时安装按钮指向支持页
- `NUXT_PUBLIC_EXTENSION_REPO_URL`：JoyhouseBot 浏览器扩展开源仓库
- `NUXT_PUBLIC_EXTENSION_DOWNLOAD_URL`：浏览器扩展发布包直链，默认指向最新 GitHub Release
- `NUXT_PUBLIC_EXTENSION_RELEASES_URL`：浏览器扩展 GitHub Releases 页面
- `NUXT_PUBLIC_AGENT_URL`：JoyhouseBot Agent 项目地址
- `NUXT_PUBLIC_AGENT_DOCS_URL`：JoyhouseBot Agent 文档入口，默认指向 GitHub `docs/` 目录
- `NUXT_PUBLIC_VISION_URL`：JoyHouse「数据与智能」愿景页
- `NUXT_PUBLIC_SUPPORT_EMAIL`：支持邮箱，默认 `han@joyhouse.chat`
