# 浏览器工具使用指南

joyhousebot 内置基于 Playwright 的浏览器控制服务，支持页面快照、截图、自动化操作等功能。

## 架构概览

### 核心组件

| 文件 | 作用 |
|------|------|
| `joyhousebot/browser/server.py` | FastAPI 服务，提供 HTTP API 控制浏览器 |
| `joyhousebot/browser/state.py` | 全局浏览器状态管理（单例模式） |
| `joyhousebot/agent/tools/browser.py` | Agent 工具封装，将 HTTP API 暴露给 LLM |

### 工作流程

```
Agent (LLM) --> BrowserTool --> HTTP API (/__browser__) --> Playwright --> Chromium
```

## 配置

在 `~/.joyhousebot/config.json` 中配置：

```json
{
  "browser": {
    "enabled": true,
    "headless": false,
    "executable_path": "",
    "default_profile": "default"
  }
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用浏览器控制服务 |
| `headless` | bool | `false` | 是否无头模式（`true`=后台运行不可见） |
| `executable_path` | string | `""` | Chromium 可执行文件路径，空则自动检测 |
| `default_profile` | string | `"default"` | 默认浏览器配置名 |

## 安装依赖

```bash
# 安装 Playwright Python 包（已在 pyproject.toml 中声明）
pip install playwright

# 安装 Chromium 浏览器（首次使用必须执行）
playwright install chromium
```

## 启动方式

### 方式一：随 joyhousebot 服务启动（推荐）

```bash
cd /path/to/joyhousebot
python -m joyhousebot
```

浏览器服务会自动挂载到：
- URL: `http://127.0.0.1:18790/__browser__`

### 方式二：独立启动浏览器服务

```python
from joyhousebot.browser import create_browser_app
import uvicorn

app = create_browser_app(
    executable_path="",    # 留空自动检测
    headless=False,        # 可见窗口
    default_profile="default"
)

uvicorn.run(app, host="127.0.0.1", port=8080)
```

## API 接口

### 基础端点

所有请求发送到 `http://127.0.0.1:18790/__browser__`

### 支持的操作

| Action | Method | Path | 说明 |
|--------|--------|------|------|
| `status` | GET | `/` | 获取浏览器状态 |
| `start` | POST | `/start` | 启动浏览器 |
| `stop` | POST | `/stop` | 停止浏览器 |
| `profiles` | GET | `/profiles` | 列出配置文件 |
| `tabs` | GET | `/tabs` | 列出所有标签页 |
| `open` | POST | `/tabs/open` | 打开新标签页 |
| `focus` | POST | `/tabs/focus` | 切换到指定标签页 |
| `close` | DELETE | `/tabs/{targetId}` | 关闭标签页 |
| `snapshot` | GET | `/snapshot` | 获取页面结构（推荐） |
| `screenshot` | POST | `/screenshot` | 截图 |
| `navigate` | POST | `/navigate` | 导航到 URL |
| `act` | POST | `/act` | 执行操作 |
| `pdf` | POST | `/pdf` | 导出 PDF |
| `console` | GET | `/console` | 获取控制台日志 |

## 使用示例

### cURL 调用

```bash
# 启动浏览器
curl -X POST http://127.0.0.1:18790/__browser__/start

# 打开页面
curl -X POST http://127.0.0.1:18790/__browser__/tabs/open \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com"}'

# 获取页面快照（推荐优先使用）
curl "http://127.0.0.1:18790/__browser__/snapshot"

# 截图
curl -X POST http://127.0.0.1:18790/__browser__/screenshot \
  -H "Content-Type: application/json" \
  -d '{"fullPage": true}'

# 导航
curl -X POST http://127.0.0.1:18790/__browser__/navigate \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com"}'

# 点击元素（需要先 snapshot 获取 ref）
curl -X POST http://127.0.0.1:18790/__browser__/act \
  -H "Content-Type: application/json" \
  -d '{"kind": "click", "ref": "1"}'

# 输入文本
curl -X POST http://127.0.0.1:18790/__browser__/act \
  -H "Content-Type: application/json" \
  -d '{"kind": "type", "ref": "2", "text": "hello"}'

# 按键
curl -X POST http://127.0.0.1:18790/__browser__/act \
  -H "Content-Type: application/json" \
  -d '{"kind": "press", "key": "Enter"}'

# 停止浏览器
curl -X POST http://127.0.0.1:18790/__browser__/stop
```

### Python 调用

```python
import httpx

BASE_URL = "http://127.0.0.1:18790/__browser__"

async def browser_example():
    async with httpx.AsyncClient() as client:
        # 启动浏览器
        await client.post(f"{BASE_URL}/start")
        
        # 打开页面
        await client.post(f"{BASE_URL}/tabs/open", json={"url": "https://example.com"})
        
        # 获取快照
        snapshot = await client.get(f"{BASE_URL}/snapshot")
        print(snapshot.json())
        
        # 截图
        result = await client.post(f"{BASE_URL}/screenshot", json={"fullPage": True})
        print(f"截图保存到: {result.json()['path']}")
        
        # 停止浏览器
        await client.post(f"{BASE_URL}/stop")
```

## 工作流程建议

1. **获取快照优先**：使用 `snapshot` 获取页面结构和元素引用（ref）
2. **通过 ref 操作**：使用 `act` + `ref` 进行点击、输入等操作
3. **截图按需使用**：仅在需要视觉确认时使用 `screenshot`

### 典型操作流程

```
1. POST /start        --> 启动浏览器
2. POST /tabs/open    --> 打开目标页面
3. GET /snapshot      --> 获取页面结构，记录关键元素的 ref
4. POST /act          --> 根据 ref 执行点击/输入
5. POST /screenshot   --> （可选）截图确认结果
6. POST /stop         --> 关闭浏览器
```

## 文件存储位置

- 截图: `~/.joyhousebot/browser/screenshot-*.png`
- PDF: `~/.joyhousebot/browser/page-*.pdf`

## 故障排查

### 浏览器无法启动

```bash
# 检查 Playwright 是否正确安装
python -c "from playwright.async_api import async_playwright; print('OK')"

# 重新安装 Chromium
playwright install chromium --force
```

### headless 模式问题

如果无头模式遇到问题，可以设置 `headless: false` 进行调试：

```json
{
  "browser": {
    "headless": false
  }
}
```

### 指定浏览器路径

如果需要使用特定版本的 Chromium：

```json
{
  "browser": {
    "executable_path": "/path/to/chromium"
  }
}
```

## 相关配置

浏览器工具与以下配置项相关：

- `gateway.port`: 服务端口（默认 18790）
- `gateway.node_browser_mode`: 浏览器代理路由模式（auto/manual/off）
- `gateway.node_browser_target`: 手动模式下目标节点
