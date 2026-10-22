# Browser Use Phase 2 — 网页交互

沿用第一阶段的 12 个 MCP Tool、设备路由、独立凭据和 Origin 校验。
生产环境不新增后台进程；扩展仍为原生 JavaScript，无需 npm 构建即可安装。

## 快速开始

1. 更新 Core，在 `chrome://extensions` 点击已有扩展卡片的重新加载按钮，确认版本为 `0.2.0`，
   并确认新增的 `tabs` 和 `scripting` 权限。只刷新选项页不会重载后台脚本。
2. 打开扩展选项，在“网页操作授权”填写一个 HTTP(S) 网站地址，点击“授权此网站”，手动确认 Chrome 提示。
   也可以点击“授权所有网站”，一次授予普通 HTTP/HTTPS 网站访问权限。
   必须由真实用户点击触发 `chrome.permissions.request`；MCP 没有授权入口。
3. 对目标设备调用 `capability_discover`，query 为 `browser`。
4. 调用 `browser.tabs.list` 找原生 ID，再 inspect，然后 click / fill。始终保留同一个 `device`。

授权按 **协议 + 精确主机**，包含该主机全部路径和端口，不包含其他子域名；拒绝输入通配符主机。
选项页可撤销授权，Chrome 网站设置也可收回权限。执行时同时检查显式网站授权记录与 Chrome 当前权限，
即使 `127.0.0.1` 已有桥接网络权限，也必须额外点击确认网页操作授权。

全站模式由用户点击“授权所有网站”触发，申请 `http://*/*` 和 `https://*/*`，只有 Chrome 允许后才开启内部全站标记。
执行时同时检查该标记和 Chrome 当前的两项广域权限；MCP 无法开启它。仍拒绝 Chrome 管理页、扩展页、商店等受限页面。
全站模式下不提供单站排除，移除逐站名单条目不会排除该网站。
“撤销全站授权”先关闭内部全站访问，再撤回 Chrome 广域权限；逐站记录保留，但 Chrome 可能同时撤回其覆盖的单站权限，
需要时重新点击“授权此网站”。普通页面操作仍会在执行前复核授权，撤销不会自动重试已发出的动作。

权限变化：新增 `tabs` 用于查询标题、URL、原生 ID；新增 `scripting` 用于按需注入打包脚本；
`optional_host_permissions` 声明 `http://*/*`、`https://*/*` 供用户按站或全站主动授权，**不等于自动获得所有网站权限**。
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
| `browser.page.dom.inspect` | 必填 `tab`，以及至少一项 `css` / `text` / `tag`；可选 `scope`、`limit`（默认 5，最大 10）、`depth`（默认 1，最大 3）、`wait_ms`（0–5000） | 只读探查局部 HTML 结构，返回经过过滤的节点、父子摘要及 CSS 路径 |
| `browser.page.click` | 必填 `tab`、`target`；可选 `wait_ms`（0–5000） | 在可见匹配中确定唯一目标且允许交互才点击一次，返回 clicked |
| `browser.page.fill` | click 的参数，加必填 `value`（允许空串，最大 8192 字符） | 在可见匹配中确定唯一输入框，替换内容并触发 input/change，不提交表单 |

`tab` 至少包含一个非空条件：`tab_id`、`url`、`title`。多个条件取交集；URL 和标题大小写敏感、精确匹配，
URL 包含 query 和 fragment，不做模糊搜索或自动规范化。可从 list 返回值复制正确 URL。
`tab_id` 必须来自本扩展的 Chrome 原生 API，不能使用 ChatGPT 官方浏览器插件等产品的标识符。
ID 限定为非负 JavaScript 安全整数，字符串长度在 Python 和扩展端均按 Unicode 字符计数。
从不默认选择当前激活标签页，也不默认选择第一个匹配。

`target` 至少包含一个非空条件，可用字段：`role`、`name`、`aria_label`、`label`、`placeholder`、`href`、`text`、`id`、`css`。
所有条件取交集。role / id / href 精确匹配，其余文本字段折叠连续空白后精确匹配。
DOM 探查和 ElementTarget.text 复用同一有界文本提取器，排除隐藏及受保护的后代内容；不会将原始 textContent 中的
隐藏标记、script/style、textarea 或 contenteditable 内容混入可见文案。文本仍是完整精确匹配，不做包含或前缀匹配。
`css` 是最长 1024 字符的标准 CSS 选择器，只用于 DOM 查询，可与其他条件组合；无效语法返回 invalid_selector。
不接受 XPath、正则表达式或 JavaScript 代码执行。省略 css/text 时沿用既有语义控件集合；
显式提供 css 可以定位普通 div/span，单独提供 text 会进行有界的普通 HTML 文本发现。
name 优先使用既有语义控件；没有匹配或只命中汇总文字的普通容器时，也会走有界文本发现，避免误点带 id 的外层容器。
文本发现保留标准控件的语义身份（例如 button 内的 span 不作为另一个目标）；普通容器只保留最深层匹配，
避免因为祖先汇总相同文本而误点父容器。不同分支的多个可见目标仍判歧义。
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

## 局部 DOM 探查与自定义控件

`page.inspect` 保留原有摘要结构；新增的 `page.dom.inspect` 独立提供结构探查，仍通过同一对 MCP Tool 调用。
不需要提前知道网站的专用选择器，例如先用文本发现自定义控件：

```json
{"device":"laptop","capability":"browser.page.dom.inspect","arguments":{"tab":{"tab_id":123},"text":"发送","limit":5,"depth":1}}
```

再使用已观察到的结构缩小范围（以下类名只是普通测试页面示例，不是内置规则）：

```json
{"device":"laptop","capability":"browser.page.dom.inspect","arguments":{"tab":{"tab_id":123},"scope":".composer","css":".actions > div","tag":"div","depth":2}}
```

css、text、tag 条件取 AND；tag 是普通 HTML 标签名，大小写不敏感。scope 必须是唯一 CSS 匹配，
只搜索它的后代，父摘要也不越出该根；不存在返回 scope_not_found，多匹配返回 ambiguous_scope。
scope 本身可直接通过 css 查询。没有匹配返回 target_not_found；不返回整页 outerHTML。

每项返回 tag、id、class、role、text、visible、disabled、attributes、children、parents、css、truncated。
其中 css 是临时的原生 DOM 位置路径，可直接放入 ElementTarget.css；路径过长时返回 null。
优先结合观察到的稳定类名、id 和文本形成更明确的 AND 定位，位置路径不保证跨请求期间的 DOM 稳定性。
摘要文本会裁剪，不能总是将裁剪后的文字直接用作完整精确匹配条件。

```json
{"device":"laptop","capability":"browser.page.click","arguments":{"tab":{"tab_id":123},"target":{"css":".composer .actions > div","text":"发送"}}}
```

普通 HTMLElement 也允许合成 click，不要求 button/a/role。仍要求唯一可见且未禁用，并保留 URL、Tab、授权、
期限及滚动/聚焦后的原元素身份检查。识别 :disabled、aria-disabled、inert，以及精确的通用类 disabled / is-disabled
（包含祖先）；不根据任意带有 disabled 字样的类名猜测状态。不会临时添加 tabindex 使普通控件可聚焦。
click 返回 clicked 仅表示 DOM 调用完成，不代表消息发送等业务成功。可随后再用 dom.inspect 只读观察输出或状态变化；
本轮不自动发送真实消息、不执行 BOSS 专用流程，也不增加业务状态断言或导航等待。

只读探查的边界：

- 仅访问已授权的顶层 DOM；不读 Cookie API，不执行网页 JS，不穿透 iframe、Shadow DOM。
- 属性使用白名单，最多 6 项、每值 80 字符；排除 value、data-*、on*、style、href/src、content 等可能带凭据或脚本的属性。
- input、textarea、select/option、contenteditable、script/style/template 等内容不读取；由 id/name/autocomplete 标记为密码、Token、Cookie 等的节点内容也隐藏。
  隐藏节点不返回文本，其后代文本不会汇总进父节点；不返回输入当前值、默认值或原始 HTML。
- 结构 id/class、文本与允许属性还会过滤常见 Bearer/JWT、长不透明串及显式凭据键值。此过滤不是任意业务文本的秘密检测器；已授权页面的普通可见文案仍属于探查内容。
- 每次最多输出 40 个结构节点，父级最多 2 层，子级最多 5 项，子树深度最高 3；文本/id/class 最长 160 字符，tag/role 最长 64。
  使用约 32 KiB 的共享序列化预算，保留 total 匹配数。truncated 只表示资源限制导致实际丢失内容：
  返回项超过 limit、节点/文本/属性/字节预算不足，或请求深度内的子节点超过 5 项。
  depth: 0、到达请求深度、父摘要不展开子树，以及隐私过滤/脱敏属于主动投影，本身不标记截断。
  节点 truncated 包含其已返回子树与父摘要中的实际截断；顶层 truncated 汇总这些标记，并额外反映未返回的匹配项。
- CSS 查询匹配最多 10000 项；文本发现最多扫描 10000 个元素并共享 40000 次文本节点读取预算，超限返回 page_too_large，避免漏掉重复目标后执行操作。
  单个文本候选最多读 200 个后代/2048 个原始字符；完整性不足时不把截断前缀当作完整文本。
  若已读前缀仍可能满足条件，则返回 page_too_large，避免忽略潜在重复目标后错误判断唯一性；
  已读文本已足够排除精确匹配时可以排除该候选。不要用文本搜索大段容器正文。
  大页面优先用 scope/CSS 缩小范围；不会在语义检查默认路径中无差别返回全部元素。

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
  引用节点、label 和后备正文也复用上述过滤器，防止输入内容经父节点或名称引用泄露。
  不读取任何 input.value（包括按钮类 input）或 textarea 的当前值/默认正文；无显式名称的按钮类 input 可用 id/css 定位，
  或使用页面已有的 aria-label/关联 label。普通 button 的正文名称继续支持。
  默认语义检查支持普通 button、a[href]、input、textarea、select、summary 和部分显式 role 控件；不宣称完整 WAI-ARIA 算法。
- inspect 返回 role、name、id、href、aria_label、placeholder、visible、disabled、read_only、input_type，**不返回输入框值**。
  文本摘要最多 160 字符（URL 2048），长摘要可能无法直接作为完整精确定位条件，应使用已知的明确属性缩小范围。
- click / fill 复用 visible 判定，在可见匹配中要求唯一目标；隐藏的同名元素不参与歧义判定。
  无匹配返回 target_not_found，全部隐藏返回 target_hidden，多个可见匹配返回 ambiguous_target。
  disabled 不作为筛选条件；唯一可见目标禁用时返回 target_disabled，多个可见目标中有禁用控件仍判歧义。
  操作前及 scrollIntoView、focus 后分别检查 URL、期限、原元素连接状态、匹配条件、可见唯一性及字段状态。
  原目标删除、替换或不再匹配返回 stale_target，变隐藏返回 target_hidden；不自动改选另一个同名元素。
  inspect 继续返回可见与隐藏元素，并保留每项 visible 字段。wait_ms 只等待匹配元素出现，不等待隐藏元素变为可见。
- fill 支持 text/search/email/url/tel input 和 textarea，使用原生原型 value setter 绕过常见 React 值跟踪，
  触发冒泡 input/change 事件；已用实际 React 18、Vue 3 组件测试。
  密码框返回 protected_input，只读返回 target_readonly，其他 input 类型和 contenteditable 暂不支持。
- 等待只针对元素出现，最多 5 秒，并受请求 expires_at 和 Bridge 15 秒超时约束；不等待点击后的导航或业务响应。
- 没有持久元素 ref 或跨导航缓存；每次操作重新定位。首版不支持 iframe、Shadow DOM 穿透或完整复杂富文本编辑器。
- 扫描超过 10000 个候选控件时拒绝操作；inspect 最多 20 条；所有扩展响应限制为 48 KiB，超过返回 result_too_large。

错误码集中维护于 `src/chat2local/browser/errors.py`。常见错误包括 tab_not_found、ambiguous_tab、permission_denied、
unsupported_page、page_changed、injection_failed、target_not_found、ambiguous_target、target_hidden、target_disabled、
target_readonly、protected_input、unsupported_element、stale_target、page_too_large、result_too_large、invalid_selector、scope_not_found、ambiguous_scope。
未知扩展错误仍降级为 browser_operation_failed，凭据、用户输入和页面数据不写入日志。

WebSocket 协议保持 version 1，认证、请求 ID、并发、重连、超时和释放机制不变。
新 Core 可以连接只声明 tabs.create 的 V1 扩展，此时新增能力单独显示不可用；使用 Phase 2 功能需更新 Core 并重新加载扩展。
每个能力按扩展实际同步的名称独立判断在线状态，不把创建标签页在线视为所有能力在线。

## 自动化测试

在仓库根目录执行：

```powershell
uv run pytest -q tests/test_browser_capabilities.py tests/test_browser_phase2.py tests/test_browser_contract.py tests/test_browser_bridge.py tests/test_browser_integration.py
npm ci --prefix browser-extension
npm test --prefix browser-extension
```

npm 依赖仅用于开发测试和本地验收页：jsdom 提供 DOM 环境，React/Vue 用于真实受控组件。
不加入生产扩展运行时或 Python/EXE 安装依赖，没有修改 GUI、CI 或发布流程。
当前 Windows 日常电脑不运行完整 Python 回归或进程压力测试；跨平台完整回归交由 GitHub Actions。
Node 的 DOM 测试运行实际打包内容脚本；jsdom 没有布局引擎，因此仅用适配器补充元素矩形，
可见性、语义定位、事件和 DOM 修改逻辑使用真实代码。最终布局和 Chrome 注入仍需下面的真实浏览器验收。

`tests/test_browser_contract.py` 自动对照当前 Registry 与文档 Schema；装有 Node.js 时，还会运行实际扩展校验器，
从当前模型 Schema 推导必填/额外字段、类型、数值与长度边界、Unicode 等参数用例，核对 Python→扩展的实际线协议。
可单独运行 `uv run pytest -q tests/test_browser_contract.py`。未安装 Node.js 时跨语言检查明确跳过，文档一致性检查照常执行。
新增 Capability 或字段时必须补充覆盖样例，变更模型后须重新从 Registry 导出 capability-schemas.json，否则测试失败。

DOM 探查与自定义控件初次验证：JavaScript `129 passed`；上列五个 Python 浏览器专项文件 `107 passed`。
覆盖输入值/Cookie API 不读取、属性与文本过滤、结构预算、文本祖先去重、原目标重新校验、旧功能回归，
以及新增能力经过 HTTP MCP→Hub→Agent→模拟扩展的设备隔离链路；公开 MCP Tool 仍为 12 个。
测试使用模拟 Chrome 和本地 jsdom HTML，没有操作真实 BOSS 发送控件、重启正式 Core 或重新加载用户扩展。
本轮真实 Chrome 验收留待审查后决定，下面保留既有基线的人工步骤与历史记录。

提交前文本与隐私审查修复另增 19 项确定性回归测试：覆盖语义名称及 label 引用不泄露受保护内容、
过滤文本用于精确定位、动态 DOM 重新校验、不完整读取不误判唯一性，以及实际截断与主动投影的区别。
该轮 JavaScript `148 passed`、上列五个 Python 浏览器专项文件 `107 passed`，JavaScript 语法与 `git diff --check` 通过。

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
JavaScript `34 passed`。Schema 文档与当时 Registry 的六个模型一致，`git diff --check` 通过（本轮新增后为七个）。

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
