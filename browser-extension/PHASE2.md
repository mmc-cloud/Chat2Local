# Browser Use Phase 2 — 网页交互

沿用第一阶段的 12 个 MCP Tool、设备路由、独立凭据和 Origin 校验。
生产环境不新增后台进程；扩展仍为原生 JavaScript，无需 npm 构建即可安装。

## 快速开始

1. 更新 Core，在 `chrome://extensions` 点击已有扩展卡片的重新加载按钮，确认版本为 `0.2.0`，
   并确认新增的 `tabs` 和 `scripting` 权限。只刷新选项页不会重载后台脚本。
2. 打开扩展选项，在“网页操作授权”填写一个 HTTP(S) 网站地址，点击“授权此网站”，手动确认 Chrome 提示。
   必须由真实用户点击触发 `chrome.permissions.request`；MCP 没有授权入口。
3. 对目标设备调用 `capability_discover`，query 为 `browser`。
4. 调用 `browser.tabs.list` 找原生 ID，再 inspect，然后 click / fill。始终保留同一个 `device`。

授权按 **协议 + 精确主机**，包含该主机全部路径和端口，不包含其他子域名；拒绝输入通配符主机。
选项页可撤销授权，Chrome 网站设置也可收回权限。执行时同时检查显式网站授权记录与 Chrome 当前权限，
即使 `127.0.0.1` 已有桥接网络权限，也必须额外点击确认网页操作授权。

权限变化：新增 `tabs` 用于查询标题、URL、原生 ID；新增 `scripting` 用于按需注入打包脚本；
`optional_host_permissions` 声明 `http://*/*`、`https://*/*` 供用户逐站授权，**不等于自动获得所有网站权限**。
已有 `storage`、`alarms` 和 loopback host 权限保留；没有 `debugger`、CDP、远程代码或外部网页消息入口。

## Capability 与 Schema

完整、可机读的参数 Schema 见 [capability-schemas.json](capability-schemas.json)，运行中的
`capability_discover` 是当前版本的权威来源。所有模型拒绝额外字段，数字、字符串使用严格类型。

| Capability | 参数 | 结果与规则 |
| --- | --- | --- |
| `browser.tabs.create` | 必填 `url`，保持 V1 的 HTTP(S) URL 规则 | 创建新标签页，不等待加载 |
| `browser.tabs.list` | 可选 `tab`、`limit`（默认 20，最大 50） | 返回 tabs、total、truncated；是枚举操作，可返回多个匹配 |
| `browser.tabs.resolve` | 必填 `tab` | 只允许唯一匹配；不匹配为 tab_not_found，多匹配为 ambiguous_tab |
| `browser.page.inspect` | 必填 `tab`；可选 `target`、`limit`（默认/最大 20）、`wait_ms`（0–5000） | 返回少量控件摘要；指定 target 无匹配时 target_not_found |
| `browser.page.click` | 必填 `tab`、`target`；可选 `wait_ms`（0–5000） | 唯一且允许交互才点击一次，返回 clicked |
| `browser.page.fill` | click 的参数，加必填 `value`（允许空串，最大 8192 字符） | 替换内容并触发 input/change，不提交表单 |

`tab` 至少包含一个非空条件：`tab_id`、`url`、`title`。多个条件取交集；URL 和标题大小写敏感、精确匹配，
URL 包含 query 和 fragment，不做模糊搜索或自动规范化。可从 list 返回值复制正确 URL。
`tab_id` 必须来自本扩展的 Chrome 原生 API，不能使用 ChatGPT 官方浏览器插件等产品的标识符。
ID 限定为非负 JavaScript 安全整数，字符串长度在 Python 和扩展端均按 Unicode 字符计数。
从不默认选择当前激活标签页，也不默认选择第一个匹配。

`target` 至少包含一个非空条件，可用字段：`role`、`name`、`aria_label`、`label`、`placeholder`、`href`、`text`、`id`。
所有条件取交集。role / id / href 精确匹配，其余可见文本字段折叠连续空白后精确匹配；不执行 CSS、XPath、正则或 JavaScript。
`href` 使用浏览器结合文档地址和 `<base>` 解析后的绝对 URL；应复制 inspect 返回的 href，
HTML 中的相对路径（例如 `../help`）不会自动转换，也不会回退匹配原始属性值。

例如，以下均为传给 `capability_execute` 的参数，示例 native tab_id 请替换为实际查询结果：

```json
{"device":"laptop","capability":"browser.tabs.list","arguments":{"limit":20}}
```

```json
{"device":"laptop","capability":"browser.tabs.resolve","arguments":{"tab":{"url":"https://example.com/"}}}
```

```json
{"device":"laptop","capability":"browser.page.inspect","arguments":{"tab":{"tab_id":123},"target":{"role":"button"},"limit":10}}
```

```json
{"device":"laptop","capability":"browser.page.click","arguments":{"tab":{"tab_id":123},"target":{"role":"button","name":"搜索文档"},"wait_ms":1000}}
```

```json
{"device":"laptop","capability":"browser.page.fill","arguments":{"tab":{"tab_id":123},"target":{"role":"textbox","label":"Search query"},"value":"Chat2Local"}}
```

成功沿用 `{"ok":true,"result":{...}}`；失败沿用 `{"ok":false,"error":{"code":"...","message":"..."}}`。
歧义错误可以额外带 `error.details.candidates`，只包含最多 5 条白名单摘要，不透传任意异常消息。

## DOM 执行与定位

链路：MCP → 既有 DeviceRouter / Hub / Agent → CapabilityRegistry → BrowserBridge →
`background.js` → `browser-operations.js` → Chrome scripting API → `content-script.js` → 顶层 DOM。

- Background 只管理已有 WebSocket 生命周期与请求匹配，浏览器业务分发在独立静态模块中。
- `chrome.scripting.executeScript` 只注入扩展打包的 `content-script.js`，使用 ISOLATED world、frame 0。
  注入结果的 `documentId` 用于后续 `tabs.sendMessage`，不会把命令自动重发给导航后的新文档。
- 内容脚本只接收来自本扩展的固定消息类型/操作；不接收网页 postMessage，不 eval 请求代码。
- 消息发送前在异步权限检查之后重查原生标签页，复核调用者完整的 TabSelector（ID / URL / 标题）。
  等待完成后，内容脚本还向 Background 复核当前授权与完整选择条件；条件失效返回 page_changed，不发送或执行操作，不改选其他标签页。
  页面 URL 变化、导航待完成、文档丢失或元素替换均返回明确错误，操作不自动重试。
- name 优先取 aria-labelledby 引用文本、aria-label、关联 label，再取常见按钮文本、alt、title、placeholder。
  支持普通 button、a[href]、input、textarea、select、summary 和部分显式 role 控件；不宣称完整 WAI-ARIA 算法。
- inspect 返回 role、name、id、href、aria_label、placeholder、visible、disabled、read_only、input_type，**不返回输入框值**。
  文本摘要最多 160 字符（URL 2048），长摘要可能无法直接作为完整精确定位条件，应使用已知的明确属性缩小范围。
- click / fill 要求唯一匹配；隐藏元素也参与歧义判定，防止悄悄选择另一个同名控件。
  操作前检查连接状态、可见性、禁用/aria-disabled/inert；focus 后再次确认唯一性及字段状态。
- fill 支持 text/search/email/url/tel input 和 textarea，使用原生原型 value setter 绕过常见 React 值跟踪，
  触发冒泡 input/change 事件；已用实际 React 18、Vue 3 组件测试。
  密码框返回 protected_input，只读返回 target_readonly，其他 input 类型和 contenteditable 暂不支持。
- 等待只针对元素出现，最多 5 秒，并受请求 expires_at 和 Bridge 15 秒超时约束；不等待点击后的导航或业务响应。
- 没有持久元素 ref 或跨导航缓存；每次操作重新定位。首版不支持 iframe、Shadow DOM 穿透或完整复杂富文本编辑器。
- 扫描超过 10000 个候选控件时拒绝操作；inspect 最多 20 条；所有扩展响应限制为 48 KiB，超过返回 result_too_large。

错误码集中维护于 `src/chat2local/browser/errors.py`。常见错误包括 tab_not_found、ambiguous_tab、permission_denied、
unsupported_page、page_changed、injection_failed、target_not_found、ambiguous_target、target_hidden、target_disabled、
target_readonly、protected_input、unsupported_element、stale_target、page_too_large、result_too_large。
未知扩展错误仍降级为 browser_operation_failed，凭据、用户输入和页面数据不写入日志。

WebSocket 协议保持 version 1，认证、请求 ID、并发、重连、超时和释放机制不变。
新 Core 可以连接只声明 tabs.create 的 V1 扩展，此时新增能力单独显示不可用；使用 Phase 2 功能需更新 Core 并重新加载扩展。
每个能力按扩展实际同步的名称独立判断在线状态，不把创建标签页在线视为所有能力在线。

## 自动化测试

在仓库根目录执行：

```powershell
uv run pytest -q
npm ci --prefix browser-extension
npm test --prefix browser-extension
```

npm 依赖仅用于开发测试和本地验收页：jsdom 提供 DOM 环境，React/Vue 用于真实受控组件。
不加入生产扩展运行时或 Python/EXE 安装依赖，没有修改 GUI、CI 或发布流程。
Node 的 DOM 测试运行实际打包内容脚本；jsdom 没有布局引擎，因此仅用适配器补充元素矩形，
可见性、语义定位、事件和 DOM 修改逻辑使用真实代码。最终布局和 Chrome 注入仍需下面的真实浏览器验收。

`tests/test_browser_contract.py` 自动对照当前 Registry 与文档 Schema；装有 Node.js 时，还会运行实际扩展校验器，
从当前模型 Schema 推导必填/额外字段、类型、数值与长度边界、Unicode 等参数用例，核对 Python→扩展的实际线协议。
可单独运行 `uv run pytest -q tests/test_browser_contract.py`。未安装 Node.js 时跨语言检查明确跳过，文档一致性检查照常执行。
新增 Capability 或字段时必须补充覆盖样例，变更模型后须重新从 Registry 导出 capability-schemas.json，否则测试失败。

## 真实 Chrome 本机验收

建议使用配对时已有的独立测试 config，避免修改正式 Agent。测试 helper 在一个隔离进程内启动
**真实 MCP Hub + AgentClient + BrowserBridge**，另用线程提供白名单本地静态页面；它不是生产新增服务。
固定使用 127.0.0.1 上的 18765（MCP）、18766（测试页），浏览器桥接端口来自单独 config（通常 8766）。
正式 Agent 保持运行；测试锁与状态写入独立目录。
首次运行先执行 `npm ci --prefix browser-extension` 准备 React/Vue 测试页资源。
验收助手要求授权 `http://127.0.0.1:18766`，并保留 `http://localhost:18766` 未授权以验证拒绝分支。

```powershell
uv run python browser-extension/tests/manual_runtime.py --config "$HOME/.chat2local/browser-smoke/config.yaml"
```

完成扩展重新加载与本机网站授权后，可以在另一个终端运行自动验收助手：

```powershell
uv run python browser-extension/tests/manual_check.py
```

它会仅创建本机 fixture 标签页，通过真实 MCP→Hub→Agent→Chrome 依次验证普通输入、textarea、
按钮/链接、动态目标、歧义、受保护字段、实际 React/Vue 模型，以及未授权 localhost 的拒绝；不会读取或操作已有个人标签页，
也不会自动申请权限或重试 click/fill。测试标签页保留，便于人工检查。

首次配对 config 的 browser 配置方法见 [README](README.md)。结束时在 helper 终端按 Ctrl+C，
或使用既有 RuntimeClient 控制接口停止其独立 directory 对应的测试 Core，勿停止正式 Agent。

1. 重新加载扩展，打开选项，保留配对凭据。授权 `http://127.0.0.1:18766`，检查出现的 Chrome 提示。
   开始前未授权时，对此页面 inspect 应返回 permission_denied；MCP 无法自行申请权限。
2. 打开 `http://127.0.0.1:18766/`。通过 MCP list / resolve 找到它的原生 tab ID。
   使用测试 helper 时所有调用显式传 `device: "browser-smoke-agent"`；省略 device 是 Hub 本机，不是浏览器 Agent。
3. inspect `{"role":"textbox","label":"Search query"}`，fill 为测试文字，再 click `{"role":"button","name":"Run search"}`。
   页面应显示 `Clicked: <测试文字>`。填写 Notes，确认只改变对应字段。
4. 点击页面链接，确认返回 clicked；不把它解释为导航已完成。对同名 Duplicate 按钮点击，必须 ambiguous_target，
   可用候选信息的 id / 其他属性进一步限定，不能默认第一个。检查 disabled、hidden、只读与密码字段返回稳定错误。
5. 打开 `http://127.0.0.1:18766/frameworks`。分别 fill label 为 React query、Vue notes 的控件，
   确认旁边 output 显示新文本，证明更新了框架模型，而不只是 DOM 表面值。
6. 再打开 `/duplicate`，其标题与 `/` 相同；只按标题 resolve 应 ambiguous_tab，候选最多 5 个。
   用 native tab_id 精确 resolve 应成功。手动关闭测试标签页，再使用旧 ID 应 tab_not_found。
7. 对未授权的 `http://localhost:18766/` 检查 permission_denied；它与 127.0.0.1 是不同主机。
   对 chrome://extensions、Chrome Web Store 或其他扩展页检查 unsupported_page。
   点击 Add delayed button 后，以 wait_ms 查找 Delayed action；等待不允许重复点击产生副作用。
8. 以上均通过本地 MCP 的 browser-smoke-agent 路由，验证完整 MCP→Hub→Agent→Chrome 链路。
   禁用扩展后所有浏览器能力应不可用，但同设备 read / Process / Handoff 继续工作；重新启用应重连。
   关闭 worker DevTools，空闲一分钟后再次操作，验证后台生命周期。

### 已执行的本机验收

2026-10-22 首次完整验收的自动化结果：Python 全量 `1623 passed, 30 skipped`；浏览器 Python 专项 `105 passed`；
JavaScript `34 passed`。Schema 文档与当前 Registry 的六个模型一致，`git diff --check` 通过。

随后审查修复了注入、异步权限检查和动态元素等待期间的完整 TabSelector 复核，补充跨语言参数一致性检查，
并对齐 Unicode 长度、安全整数 ID 及不可打印 URL 字符规则。该轮 Python 专项 `107 passed`、JavaScript `38 passed`，
语法检查与 `git diff --check` 通过；未在该修复轮重跑 Python 全量或真实 Chrome 验收。

2026-10-22 使用扩展 `0.2.0` 与本机独立测试设备 `browser-smoke-agent`，通过真实 MCP HTTP、Hub、
Agent WebSocket 和 Chrome 完成验收助手的 13 项检查：六能力上线、原生标签页定位、HTML input / textarea、
按钮与链接、同名控件拒绝、密码/只读/隐藏/禁用字段保护、动态控件等待、React 18 / Vue 3 模型更新、
重复标题拒绝、未授权 localhost 拒绝，以及同一 Agent 的原有 read 工具。
验收助手先通过只读查询等待新标签页 URL 切换完成，不把 tabs.create 的成功当作加载完成。

人工关闭标签页、手动禁用扩展、Chrome 受限页面及 HTTPS 网站授权提示未在本轮逐项重复操作；
对应关闭、断线、重连、受限协议、权限拒绝及清理分支有自动化测试，上述人工步骤保留供复核。
本轮临时验收 Runtime 已正常停止，8766 / 18765 / 18766 端口重新绑定检查通过；原有正式 Agent 保持运行。

## 限制与后续

脚本事件 `isTrusted=false`，无法替代所有真实用户手势，可能不能打开受限制弹窗、文件选择器或满足站点的 trusted-event 要求。
不绕过安全措施和反自动化机制。clicked 不代表业务成功，filled 不代表表单提交；业务侧事件处理器本身仍可能产生额外行为。
超时或断线后操作可能已发生，不做动作自动重试、exactly-once 承诺或跨重启去重。
选择器按精确文本设计，复杂可访问名称、跨 iframe、Shadow DOM 和富文本可在后续阶段分别扩展，保持现有公共 Tool 与设备边界。
