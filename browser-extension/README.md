# Chat2Local Browser Use

可选、独立安装的 Chrome Manifest V3 扩展，提供标签页创建/查询/选择、独立网页读取与分段续读，以及授权网页的 inspect/click/fill、局部 DOM 只读探查与 CSS 自定义控件定位。
**Chat2Local Browser Use 独立负责读取、定位、操作和结果观察，不再依赖官方 ChatGPT Chrome 插件读取页面。**
推荐按需使用：`tabs.list / resolve → page.read → page.inspect / dom.inspect → click / fill → page.read 再观察`。
公共 MCP Tool 保持 12 个；浏览器能力为 8 个，统一通过 capability_discover / capability_execute 调用。
第二阶段 Schema、语义定位、网站授权与完整验收步骤见 [PHASE2.md](PHASE2.md)。
发现结果同时提供各能力的 `result_contract`、共享 `conventions` 和 `error_guidance`，说明成功字段、隐私展示及错误恢复。
读取/检查结果提供安全 `ref` 时，后续用 `target: {"ref": "实际返回的ref"}` 定位；展示 URL/名称已脱敏，不能当作原始精确条件。
任意 DOM 变更会使旧 ref 失效，返回 stale_target 后重新观察，不自动回退位置型 CSS。
Core 配置和凭据也可通过 [GUI Browser 设置](../gui/pywebview/CONFIGURATION.md) 完成，无需手动创建 Token 文件。
不需要 Node 构建、前端框架或额外 Python 依赖，不随 Python 包或 EXE 自动安装。
Core 默认关闭浏览器接入；未安装扩展、未连接或断线均不影响原有 10 个 Tool。

## 安装与配对

1. 使用 **Chrome 120+**。在 `chrome://extensions` 打开开发者模式，选择“加载已解压的扩展程序”，
   指向仓库根目录的 `browser-extension/`。复制 Chrome 显示的 32 位扩展 ID。
2. 在**运行目标 Agent 的本机**生成独立随机凭据。以下命令在项目目录执行，不输出凭据，
   并以独占创建方式避免覆盖现有文件；文件已存在时应复用原配对，或自行备份后轮换。

   ```powershell
   uv run python -c "import os,secrets; from pathlib import Path; p=Path.home()/'.chat2local'/'browser.token'; p.parent.mkdir(parents=True,exist_ok=True); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,secrets.token_urlsafe(32).encode('ascii')); os.close(fd)"
   ```

   凭据文件放在私有用户目录，限制为当前 OS 用户可读。POSIX 使用 `0600`；Windows 依赖用户目录 ACL，
   如该目录有额外共享权限，请通过文件“属性 → 安全”收紧 ACL。不要把该文件放进仓库。
3. 在目标设备使用的 `~/.chat2local/config.yaml` 添加配置（或添加到 `--config` 指定文件）：

   ```yaml
   browser:
     enabled: true
     port: 8766
     extension_id: abcdefghijklmnopabcdefghijklmnop # 替换为实际 Chrome 扩展 ID
     token_file: ~/.chat2local/browser.token
   ```

   浏览器凭据必须是 32–128 位字母、数字、`_`、`-`。**不要使用 Hub token**；Agent / Hub
   会拒绝与当前 Hub token 相同的浏览器凭据。端口可修改，监听地址固定为 `127.0.0.1`，没有 host 配置。
4. 点击扩展图标打开设置（或在扩展详情中选择“扩展程序选项”），勾选“启用连接”，填入同一端口。
   在本机文本编辑器中读取 `browser.token`，复制到密码输入框并保存。凭据只保存在当前 Chrome profile 的
   `chrome.storage.local`，不使用 `storage.sync`。只向可信扩展上下文开放存储。
5. 重启既有 Agent，例如 `uv run chat2local agent --workspace 'D:\Projects\example'`，
   Hub URL 和 Hub token 沿用原配置。也支持本机 standalone / Hub 模式，浏览器端点同样只在 loopback 上启动。
   扩展设置页状态应变为 `connected`。

启用配置但凭据不可读、格式错误、复用了 Hub token 或监听端口占用时，浏览器桥接会安全降级并记录固定警告，
Core / Hub / Agent 继续运行，能力显示不可用。修复后重启 Core。`enabled: false` 时不读取凭据、不绑定端口。
如切换 Chrome profile、移动未打包扩展目录导致 ID 变化，请重新确认并更新 `extension_id`。
一个本机端点只接纳一个已认证扩展连接；同机多实例应使用不同端口。

## MCP 调用

沿用既有设备选择规则：省略 `device` 指向 **MCP 服务所在设备**。远程浏览器必须显式传目标 Agent ID。
`list_devices` 返回的工具列表是设备支持的 MCP Tool 名称；浏览器的实时状态通过 `capability_discover` 查询。

```json
{
  "device": "laptop",
  "query": "browser.tabs.create"
}
```

将以上参数传给 `capability_discover`。也支持 `"query": "Chrome tab"` 等大小写不敏感的关键词，
多个词全部匹配；省略 query 返回全部已知能力。结果节选示例（Schema、共享指导和返回契约只展示部分字段）：

```json
{
  "ok": true,
  "result": {
    "capabilities": [{
      "name": "browser.tabs.create",
      "description": "Create one Chrome tab for an HTTP(S) URL. Side effect: opens a tab; does not wait for load or prove business success. Check tabs before retrying an uncertain result.",
      "input_schema": {
        "type": "object",
        "required": ["url"],
        "additionalProperties": false,
        "properties": {"url": {"type": "string"}}
      },
      "available": true,
      "result_contract": {"tab_id":"integer: native Chrome tab ID for subsequent calls."}
    }]
  }
}
```

扩展断开或浏览器接入关闭时，已知能力仍返回 `available: false`；未知查询返回 `capabilities: []`。
实际返回的 schema 还包含长度、模式和参数说明。仅接受绝对 `http://` 或 `https://` URL，
拒绝缺少 URL、额外字段、非字符串、凭据、空白、控制字符、反斜杠和非法端口。

传给 `capability_execute`：

```json
{
  "device": "laptop",
  "capability": "browser.tabs.create",
  "arguments": {"url": "https://example.com/"}
}
```

成功结果：

```json
{"ok": true, "result": {"tab_id": 123, "window_id": 1, "url": "https://example.com/"}}
```

`url` 是已提交请求 URL 的规范化脱敏展示值（去除 Query、Fragment、凭据等）；精确选择后续标签页使用 tab_id。
成功表示 Chrome 已创建标签页，不表示页面已加载完成或最终跳转目标。
失败结果统一为 `{"ok": false, "error": {"code": "...", "message": "..."}}`：

| code | 含义 |
| --- | --- |
| `unknown_capability` | 本版本没有该能力 |
| `invalid_arguments` | Capability 参数不符合 schema / URL 规则 |
| `capability_unavailable` | 扩展未连接、已断开或没有同步该能力 |
| `capability_timeout` | 浏览器 15 秒内没有返回；操作可能已经完成 |
| `capability_busy` | 已达到 16 个本机并发请求上限 |
| `request_expired` | 请求到达扩展时已超过执行期限，未执行浏览器操作 |
| `browser_operation_failed` | Chrome 执行失败，返回固定诊断，避免泄露扩展错误内容 |
| `device_unavailable` | 设备离线、不支持新 Tool、Hub 请求超时等原设备路由错误 |
| `capability_error` | 目标设备工具分发失败 |

扩展错误码在 Core 的 `browser/errors.py` 中集中维护，包含通用错误和第二阶段网页交互错误。
经白名单校验后保留错误分类；错误消息始终由 Bridge 使用固定文本生成，未知错误码降级为
`browser_operation_failed`，不透传扩展原始消息；歧义错误仅附带经过白名单清理的有限候选摘要。

外层 MCP 入参格式错误（例如缺少 `capability` / `arguments`、将 arguments 传为数组）由 MCP SDK
返回标准 Tool 参数错误；进入能力分发后的失败才使用上述业务结果结构。浏览器执行不使用文件 workspace 权限。

## 实际调用链与本机协议

```text
ChatGPT / MCP client
  → capability_discover / capability_execute
  → DeviceRouter（复用 device 规则）
  → Hub DeviceSession → 原有 /device/ws → AgentClient
  → LocalToolDispatcher → CapabilityRegistry
  → BrowserBridge：ws://127.0.0.1:8766/browser/ws
  → Chrome MV3 background.js → browser-operations.js
  → Chrome tabs API / 静态 content-script.js → 授权网页 DOM
  → 同一请求链返回结果
```

本机 / Hub 自身目标由 DeviceRouter 直接调用本机 dispatcher，无需另连 Agent。
不新增设备路由协议，也不把浏览器能力名称当作 Agent 的 MCP Tool 名称。

- 复用项目已有 `websockets` 依赖，浏览器 listener 与现有 Core / Agent **同进程**，只在启用时启动。
- WebSocket 握手要求 Origin **精确等于** `chrome-extension://<配置的扩展ID>`；无 Origin、普通网页、其他扩展拒绝。
  path 必须为 `/browser/ws`，不接受 query string；凭据不会放到 URL 中。
- 握手后 5 秒内扩展发送 `hello`，包括 `protocol_version: 1`、独立 token 和能力名列表；
  服务端用常量时间比较验证凭据，只接纳 Core 支持的能力，再返回 `hello_ack`。认证前无法执行操作。
  握手期间保留连接占位以拒绝重复接入；只有 `hello_ack` 成功发送后，才将同步能力标记为可用。
- 扩展只处理收到成功 ack 后的 `request`。每条命令含 UUID `request_id`、能力名、arguments 和 `expires_at`；
  `response` 带同一 ID。Core 对应 Future 处理异步返回、乱序回复、超时、取消与断线；未知 / 迟到 ID 忽略。
  扩展拒绝过期请求，并在单次连接内缓存最近最多 256 个请求 ID 的响应，抑制重复执行。
- 扩展每 20 秒发应用心跳维持 MV3 WebSocket 活动，服务端返回 pong；两侧检测失联。
  正常关闭立即下线，网络失联通常在约 40–60 秒内检测。扩展用 1–20 秒退避重连，
  另有每 30 秒的 Chrome alarm，在 worker 重启 / 停止计时器后恢复连接；休眠可能延迟触发。
- Agent 的 Hub 连接重连保留浏览器 Runtime；Core / Agent 正常退出会关闭浏览器连接、listener 并释放等待者。
  运行时连接、能力可用性与待处理请求只保存在内存，不与 Handoff、Chat 或 Process 状态混存。
- 日志不输出 token、认证帧或浏览器参数。DOM 脚本仅按需注入，无外部网页消息入口、`externally_connectable`、
  自动授予的广域 host 权限、远程脚本或书签权限。第二阶段新增 `tabs`、`scripting`，
  网站权限通过选项页用户点击按站或全站授予；仍保留 `storage`、`alarms` 和 `http://127.0.0.1/*` 本机 host 权限，CSP 将扩展自身网络连接限制为
  `ws://127.0.0.1:*`。本机 host 权限用于 Chromium 本地网络访问检查下的扩展接入；
  Chrome host match pattern 无法限定端口，因此端口由扩展设置控制。

Origin 与 token 保护来自普通网页的调用；同一 OS 用户下的恶意本地程序可读取用户凭据或伪造 Origin，
本机接入不是 OS sandbox。受信的本机 Agent 是扩展的命令来源。

## 自动化测试

在仓库根目录运行：

```powershell
uv run pytest -q
npm ci --prefix browser-extension
npm test --prefix browser-extension
```

Python 测试无需 Chrome，覆盖参数、能力目录、真实 loopback WebSocket、Origin / token 拒绝、重复连接、
乱序响应、超时 / 取消、掉线重连、清理、可选监听失败、standalone / Hub 本机生命周期，
以及 **HTTP MCP → Hub → 两个真实 Agent → 模拟扩展** 的设备隔离集成调用。
Node 测试使用内置 `node:test` / `vm` 模拟 Chrome API 与 worker 环境，覆盖真实后台脚本的认证门禁、
标签页创建、无效命令、过期请求、同连接去重、API 错误、心跳、重连及 worker 恢复。
另使用开发依赖 jsdom 运行实际打包的 DOM 脚本，并使用 React / Vue 组件检查受控输入模型更新；
这些测试依赖不会成为生产扩展的安装或运行依赖。

## 手动验收与调试

1. **关闭浏览器接入**启动 Core / Hub / Agent。确认原有 read、search、Process、Handoff 功能正常，
   两个新 Tool 可见，`browser.tabs.create` 返回 `available: false`。
2. 按上面的安装步骤连接扩展，设置页确认 `connected`。通过真正的 ChatGPT MCP 连接对目标设备调用
   discover，再 execute 打开 `https://example.com`。确认只在目标设备 Chrome 出现一个新标签页，
   返回 tab_id / window_id，Hub 或其他设备的 Chrome 不发生变化。
3. 关闭 worker 的 DevTools（避免调试器影响生命周期），空闲超过一分钟后再次执行。
   重启 Chrome / Agent，确认扩展自动恢复；Agent 正常退出后确认原浏览器端口可以重新绑定。
4. 在 `chrome://extensions` 禁用扩展，确认 discover 变为不可用，execute 返回 `capability_unavailable`，
   原工具继续可用；重新启用后自动恢复。关闭设置页的“启用连接”再保存，也应断开并保持关闭。
5. 错误参数：缺少 `url`、`javascript:alert(1)`、`file:///...`、未知能力；确认错误明确且没有新标签页。
   更改扩展中的凭据为另一个合法随机串，确认无法配对；改回后恢复。普通网页不应通过 Origin 校验。
6. 使用两个 Agent 时分别指定 device 验证路由；省略 device 应明确落在 Hub 自身，不能隐式选择另一台机器。

查看 `chrome://extensions` 中该扩展的 service worker 检查窗口可调试；设置页显示连接状态。
Agent 端固定警告提示检查凭据文件与端口。认证失败不显示收到的 token。
轮换凭据需更新文件与扩展设置并重启 Agent；停用时设 `browser.enabled: false` 并重启。

## 范围与限制

第二阶段已提供页面 inspect / click / fill 与基础语义定位，详见 [Phase 2](PHASE2.md)。不包含标签组或书签，GUI 和发布流程保持既有实现。
一个端点对应一个 Chrome profile，没有浏览器 profile 路由或跨重启的请求去重。
超时、断开、Hub 重连或服务重启后**不要盲目重试**：Chrome 操作可能已发生，必须先人工检查实际标签页。
没有端到端 exactly-once 或页面加载完成保证。第二阶段已通过真实 Chrome 的本机 MCP→Hub→Agent 验收，
覆盖普通 HTML、React / Vue 输入与点击等主要链路；详细覆盖及剩余人工检查见 [PHASE2.md](PHASE2.md)。

下一阶段在代码审查与真实 Chrome 验收后，再按独立 Capability schema 增量加入更高级的页面能力，
保留现有 Tool 和设备路由边界。

实现依据：Chrome 官方 [WebSocket worker 生命周期说明](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets)、
[alarms API](https://developer.chrome.com/docs/extensions/reference/api/alarms)、
[tabs API 权限说明](https://developer.chrome.com/docs/extensions/reference/api/tabs)。
本机访问权限参考 Chromium 团队的 [扩展本地网络访问说明](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/pUDh8RiTjJk)。
