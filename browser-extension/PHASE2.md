# Browser Use Phase 2 — 网页交互

本文保留 Phase 2 的能力基线与历史验收；当前 Phase 3 的八项新增 DOM 操作及最新 16 能力契约见 [PHASE3.md](PHASE3.md)。

沿用第一阶段的 12 个 MCP Tool、设备路由、独立凭据和 Origin 校验。
生产环境不新增后台进程；扩展仍为原生 JavaScript，无需 npm 构建即可安装。
Chat2Local 现在独立负责网页读取、定位、执行和结果观察，不再将官方 ChatGPT Chrome 插件作为必要依赖。
当前为 8 个内部 Browser Capability，公共 MCP Tool 仍为 12 个。

## 快速开始

1. 更新 Core，在 `chrome://extensions` 点击已有扩展卡片的重新加载按钮，确认版本为 `0.2.0`，
   并确认新增的 `tabs` 和 `scripting` 权限。只刷新选项页不会重载后台脚本。
2. 打开扩展选项，在“网页操作授权”填写一个 HTTP(S) 网站地址，点击“授权此网站”，手动确认 Chrome 提示。
   也可以点击“授权所有网站”，一次授予普通 HTTP/HTTPS 网站访问权限。
   必须由真实用户点击触发 `chrome.permissions.request`；MCP 没有授权入口。
3. 对目标设备调用 `capability_discover`，query 为 `browser`，默认 summary；再用具体能力名和 `detail: "full"` 查询完整定义。
4. 按需使用 `tabs.list / resolve → page.read → page.inspect / dom.inspect → click / fill → page.read 再观察`。
   不要求每次调用全部接口；始终保留同一个 `device`。

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
`capability_discover(detail="full")` 是当前版本完整契约的权威来源，默认 summary 仅返回概览。所有模型拒绝额外字段，数字、字符串使用严格类型。
full 发现结果的每项包含 `result_contract`（成功 result 内字段到含义的精简映射，不是输出 JSON Schema）；
顶层 `result.conventions` 说明结果封装、安全引用、展示字段和重试规则，`result.error_guidance` 按错误码提供恢复指导，
两者共享一份，不在 8 个能力中重复复制。`capability-schemas.json` 导出同样的结构，省略运行时 `available`。
参数 JSON Schema 使用标准 anyOf + required + 非 null 条件表达 TabSelector、ElementTarget、DOM 查询的组合约束；
Pydantic 仍负责运行时验证，合法的可选 null 在发送扩展前统一省略。URL 还需通过已有语义解析校验。

| Capability | 参数 | 结果与规则 |
| --- | --- | --- |
| `browser.tabs.create` | 必填 `url`，保持 V1 的 HTTP(S) URL 规则 | 创建新标签页，不等待加载 |
| `browser.tabs.list` | 可选 `tab`、`limit`（默认 20，最大 50） | 返回 tabs、total、truncated；是枚举操作，可返回多个匹配 |
| `browser.tabs.resolve` | 必填 `tab` | 只允许唯一匹配；不匹配为 tab_not_found，多匹配为 ambiguous_tab |
| `browser.page.read` | 必填 `tab`；可选 `cursor`、`max_chars`（默认 8000，范围 1000–12000） | 读取当前顶层页面结构化正文与概览；按 next_cursor 续读，页面变化后旧游标失效 |
| `browser.page.inspect` | 必填 `tab`；可选 `target`、`limit`（默认/最大 20）、`wait_ms`（0–5000） | 返回少量控件摘要；指定 target 无匹配时 target_not_found |
| `browser.page.dom.inspect` | 必填 `tab`，以及至少一项 `css` / `text` / `tag`；可选 `scope`、`limit`（默认 5，最大 10）、`depth`（默认 1，最大 3）、`wait_ms`（0–5000） | 只读探查局部 HTML 结构，返回经过过滤的节点、父子摘要及 CSS 路径 |
| `browser.page.click` | 必填 `tab`、`target`；可选 `wait_ms`（0–5000） | 在可见匹配中确定唯一目标且允许交互才点击一次，返回 clicked |
| `browser.page.fill` | click 的参数，加必填 `value`（允许空串，最大 8192 字符） | 在可见匹配中确定唯一输入框，替换内容并触发 input/change，不提交表单 |

`tab` 至少包含一个非空条件：`tab_id`、`url`、`title`。多个条件取交集；URL 和标题大小写敏感、精确匹配，
URL 包含 query 和 fragment，不做模糊搜索或自动规范化。输出 URL/标题经过脱敏，后续定位优先使用 list 返回的 tab_id；
不要把展示 URL/标题当作已知的原始精确条件。
`tab_id` 必须来自本扩展的 Chrome 原生 API，不能使用 ChatGPT 官方浏览器插件等产品的标识符。
ID 限定为非负 JavaScript 安全整数，字符串长度在 Python 和扩展端均按 Unicode 字符计数。
从不默认选择当前激活标签页，也不默认选择第一个匹配。

`target` 至少包含一个非空条件，可用字段：`ref`、`role`、`name`、`aria_label`、`label`、`placeholder`、`href`、`text`、`id`、`css`。
所有条件取交集。role / id / href 精确匹配，其余文本字段折叠连续空白后精确匹配。
DOM 探查和 ElementTarget.text 复用同一有界文本提取器，排除隐藏及受保护的后代内容；不会将原始 textContent 中的
隐藏标记、script/style、textarea 或 contenteditable 内容混入可见文案。文本仍是完整精确匹配，不做包含或前缀匹配。
`css` 是最长 1024 字符的标准 CSS 选择器，只用于 DOM 查询，可与其他条件组合；无效语法返回 invalid_selector。
不接受 XPath、正则表达式或 JavaScript 代码执行。省略 css/text 时沿用既有语义控件集合；
显式提供 css 可以定位普通 div/span，单独提供 text 会进行有界的普通 HTML 文本发现。
name 优先使用既有语义控件；没有匹配或只命中汇总文字的普通容器时，也会走有界文本发现，避免误点带 id 的外层容器。
文本发现保留标准控件的语义身份（例如 button 内的 span 不作为另一个目标）；普通容器只保留最深层匹配，
避免因为祖先汇总相同文本而误点父容器。不同分支的多个可见目标仍判歧义。
`href` 仍精确匹配原始、结合文档地址和 `<base>` 解析的绝对 URL，仅适用于独立已知完整 URL。
返回的 href/url 是脱敏展示信息，不能直接复制为 href 条件；HTML 相对路径（例如 `../help`）也不会自动转换。
从读取结果执行操作时，优先复制其 `ref` 到 `target.ref`，不要根据脱敏值猜测完整 URL。

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

## 安全元素引用与 Agent 返回契约

page.read 的链接/语义控件、page.inspect 的元素、dom.inspect 的根元素以及目标歧义候选均返回 `ref`。
例如同名链接 `/jobs?filter=python` 与 `/jobs?filter=java` 的展示 URL 都可能是 `/jobs`，但 ref 不同，
Agent 可以用选中记录的 ref 准确操作，不需要未脱敏 Query：

```json
{"device":"laptop","capability":"browser.page.click","arguments":{"tab":{"tab_id":123},"target":{"ref":"el_copy_from_latest_result"}}}
```

上面的 ref 必须替换为本次实际观察结果。其余 target 条件仍然取 AND；不匹配或歧义不会自动回退选第一项。
ref 是内容脚本闭包中的文档随机标识和递增编号，引用表最多 10000 条，节点使用 WeakRef，不写属性、不保存正文、不写磁盘。
每次解析引用前会检查 MutationObserver 的待处理记录；任意子节点、属性或文本变化会清空引用表，
导航、新文档、节点回收或容量淘汰也可能使引用失效。引用失效返回 stale_target，必须重新 read/inspect/dom.inspect。
此策略有意保守：无关 DOM 动画或属性变化也可能要求重新观察，不承诺跨页面更新保持引用。
操作前、滚动后、聚焦后继续校验引用、可见性、禁用状态、唯一性、权限及完整 TabSelector。
普通原始 href 和显式 CSS 输入继续保留原有精确匹配语义；位置型 CSS 仅描述当时结构，不是安全跨请求身份，不能作为失效 ref 的自动回退。

| Capability | Agent 可依赖的成功字段与后续动作 |
| --- | --- |
| tabs.create | tab_id/window_id、脱敏展示 url；只确认创建，不确认加载或业务成功；结果不明先查 tabs |
| tabs.list | tabs、total、truncated；用 native tab_id 继续，截断时缩小筛选 |
| tabs.resolve | 唯一 tab_id/window_id/title/url；零项/多项是错误，不激活或导航标签页 |
| page.read | page/overview/blocks、offset/total_blocks、has_more/next_cursor、content_truncated/truncated；按游标续读，page_changed 后重新开始 |
| page.inspect | elements（含 ref、visible、disabled、read_only、truncated）、total/truncated；适合语义控件，不读取输入值 |
| page.dom.inspect | elements（含 ref、临时 css、过滤属性/文本、父子结构）、total/truncated；适合自定义结构，文本不证明可交互 |
| page.click | status=clicked、target、event_is_trusted=false、navigation_waited=false；只是调用一次合成 click，需再观察业务结果 |
| page.fill | status=filled、target、submitted=false；原生 setter + input/change，不显式提交，但页面事件处理器仍可能产生副作用 |

所有只读/操作摘要和歧义候选共用 browser-safety.js：先对完整有界原文脱敏，再裁剪；超过 65536 UTF-16 单元的属性整项省略为脱敏标记，
不返回未经检查的前缀。超出文本提取预算而不完整的摘要正文/名称不作为可用前缀返回，并标记 truncated。
普通名称、role、aria 属性和安全 href 字段仍保留；URL 去除用户名密码、Query、Fragment，文本过滤常见凭据、Bearer/JWT 和不透明长串。
这不是任意业务秘密的检测器，也不改变网页数据不可信的边界。

恢复约定由 discover 直接提供：参数/CSS 错误先修正输入；目标不存在/歧义先重新观察并细化；
target_disabled/hidden/readonly/protected_input 停止操作，不绕过状态；stale_target 丢弃旧 ref；
page_changed 丢弃游标并重新读取；permission_denied 需用户授权，MCP 不能扩权；
page_too_large 需缩小 DOM 范围（降低 max_chars 不减少提取工作），result_too_large 可降低输出预算。
超时、断线或执行失败可能已产生副作用，create/click/fill 不得盲目重放，先观察实际状态。

## 独立网页读取与长页面续读

`page.read` 负责当前页面正文与概览，`page.inspect` 负责语义控件，`page.dom.inspect` 负责局部 DOM。
读取仍经过同一 DeviceRouter / Hub / Agent / BrowserBridge / Background / Content Script 链路，网站授权规则不变。
下面示例均传给 `capability_execute`，使用真实的原生 tab_id；不需要官方浏览器插件提供页面内容。

```json
{"device":"laptop","capability":"browser.page.read","arguments":{"tab":{"tab_id":123},"max_chars":8000}}
```

输入对象拒绝额外字段，完整 JSON Schema 见 [capability-schemas.json](capability-schemas.json) 的 capabilities 中 `name=browser.page.read` 项：

| 字段 | 类型与约束 |
| --- | --- |
| `tab` | 必填，复用 TabSelector；原生 ID、精确 URL、精确标题取 AND，至少一项 |
| `cursor` | 可省略/null；非空字符串，最长 512 个字符，字符集 `[A-Za-z0-9_-]`；使用返回的 next_cursor，勿自行编辑 |
| `max_chars` | 严格整数，1000–12000，默认 8000；限制本段 blocks.text 的 Unicode 字符数，另有字节限制 |

成功结构如下；示例省略了部分 blocks：

```json
{
  "ok": true,
  "result": {
    "tab": {"tab_id":123,"window_id":1,"title":"Example","url":"https://example.com/"},
    "page": {"url":"https://example.com/","title":"Example","description":"Public description","language":"zh","truncated":false},
    "overview": {"headings":2,"links":3,"controls":2,"images":1},
    "blocks": [
      {"type":"heading","tag":"h1","depth":2,"disabled":false,"level":1,"heading_level":1,"block_id":0,"part":0,"text":"标题"},
      {"type":"paragraph","tag":"p","depth":2,"disabled":false,"block_id":1,"part":0,"text":"正文内容"}
    ],
    "offset": 0,
    "total_blocks": 12,
    "has_more": true,
    "next_cursor": "opaque_base64url_cursor",
    "truncated": true,
    "content_truncated": false,
    "content_is_untrusted": true
  }
}
```

blocks 保持 DOM 阅读顺序，每个文本段最多 1000 个 Unicode 字符。长段落按相同 block_id、递增 part（从 0 开始）续接，
不要在同一 block_id 的片段之间自行插入换行；block_id 只标识本次内容版本的逻辑文本段，不能用于点击定位。
heading 带 level；list_item 带 list_depth / ordered；table_cell 带 row / column（从 1 开始）/ header。
link 带经过过滤的 url 与 ref；control 带 role / name / disabled 与 ref；image 返回 alt 等描述文字，不返回 src。
paragraph、text、quote、code 分别表示普通段落、其他有意义文字、引用和保留换行的 pre 正文。
内嵌链接等片段也保留 heading_level、list_depth 或表格行列信息。depth 表示 DOM 嵌套深度，不是视觉坐标。
任意正文块都有 disabled；它只表示已识别的禁用标记，普通 div 的文字不等于已确认可交互控件，操作前仍可 inspect。
overview 是当前完整捕获中的可见标题、链接、控件与有描述图片数量；total_blocks 是分片后的记录总数。

当 has_more=true，用同一设备和同一 tab 传回 next_cursor（max_chars 可调整）：

```json
{"device":"laptop","capability":"browser.page.read","arguments":{"tab":{"tab_id":123},"cursor":"上一次的next_cursor","max_chars":8000}}
```

- 游标包含 Chrome documentId、由当前原始 URL 与过滤后结构化内容计算的 SHA-256 版本，以及下一条记录 offset。
  内容哈希排除 blocks.ref 这类临时操作标识，保留页面元信息、正文、块顺序/结构、控件状态和截断标记。
  原始 URL 只在扩展内部参与摘要，不放入游标明文。每次续读重新提取并计算版本，不跨请求缓存用于续读的正文快照，不写磁盘。
  原有同连接请求去重机制仍保留最多 256 条响应（可包含已返回分段），断线后释放；这不是续读页面缓存。
- 导航、同 URL 重载、可读正文/标题、可见性、禁用状态等影响读取结果的变化会返回 page_changed；不返回混合版本的数据。
  此时丢弃旧游标，不带 cursor 从头读取。无关 DOM 更新或相同内容元素替换只改变 ref、未改变上述可读内容时，分页可以继续。
  续读返回本次重新提取的当前 ref；旧 ref 仍按原有规则失效，不能因为分页成功就继续用于操作。
- 游标格式不合法返回 invalid_cursor；过期、授权撤回、设备离线或扩展断开仍沿用原有错误。请求开始及提取后检查 URL/期限，
  SHA-256 异步计算后再次检查网站授权和完整 TabSelector。单次返回是提取时刻的内容，不承诺后续页面停止变化。
- has_more 只说明当前已加载 DOM 还有记录未返回；不会滚动、触发懒加载或推测网站服务器的后续内容。
  truncated=true 表示本段还有后续记录，或内容元信息被裁剪；content_truncated 专门标记后者。
  page.truncated 与块级 truncated 标记具体的元信息裁剪。has_more=false 且 content_truncated=false 表示本次可读结果已完整返回。

读取与隐私边界：

- 复用 protectedNode、过滤文本、语义名称、可见性及 disabled 检测；只读已授权顶层 HTML DOM。
  不读 input/textarea/select 值、Cookie、script/style/template/contenteditable 正文或凭据标记节点，不穿透 iframe/Shadow DOM，不执行网页代码。
  隐藏正文、关闭 details 中除首个 summary 外的正文、content-visibility:hidden 子树均不返回。
- URL 移除用户名密码、全部 query 和 fragment，脱敏已识别凭据路径段及其后续值；正文中的 HTTP(S) URL 也按同样规则过滤。
  URL 太长会缩为 origin 或省略并标记截断。图片只返回描述，不读回图片地址；meta 只允许 description 和页面语言/标题。
  脱敏 URL 不能当作原始精确 href；定位优先使用返回的 ref，原生 ID/CSS 仅用于独立已知的原始条件。
- 页面正文会过滤常见凭据键值、Bearer/JWT 和长不透明串；这不是任意业务秘密的完备检测器。
  普通可见页面内容仍是用户授权读取的数据，必须作为不可信数据处理；页面文字不授予操作权限。
- 标题/description 最长 512、语言/role 最长 64、控件名称最长 256、图片描述最长 512；裁剪均显式标记。
  正文按段续读而非截掉尾部。每段正文记录最多约 32 KiB，包含元信息的返回预留在约 40 KiB 内，低于现有扩展返回体的 48 KiB 限制。
- 一次提取最多访问 50000 个 DOM 节点、128 层、1000000 个原始可见文本 UTF-16 单元、10000 条输出记录。
  超大或无限增长 DOM 明确返回 page_too_large；复用的控件名称读取还受原有文本预算限制。
  第一版不实现阅读器正文判定、完整布局/无障碍树、iframe、Shadow DOM 或自动加载更多。

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

每项返回 tag、id、class、role、text、visible、disabled、attributes、children、parents、css、ref、truncated。
其中 css 是临时的原生 DOM 位置路径，路径过长时返回 null；后续执行优先使用 ref。
独立已知的稳定类名、id 和文本仍可形成明确的 AND 定位，位置路径不保证跨请求期间的 DOM 稳定性。
摘要文本会裁剪，不能总是将裁剪后的文字直接用作完整精确匹配条件。

```json
{"device":"laptop","capability":"browser.page.click","arguments":{"tab":{"tab_id":123},"target":{"css":".composer .actions > div","text":"发送"}}}
```

普通 HTMLElement 也允许合成 click，不要求 button/a/role。仍要求唯一可见且未禁用，并保留 URL、Tab、授权、
期限及滚动/聚焦后的原元素身份检查。识别 :disabled、aria-disabled、inert，以及精确的通用类 disable / disabled / is-disabled
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
- `chrome.scripting.executeScript` 只注入扩展打包的 `browser-safety.js` 和 `content-script.js`，使用 ISOLATED world、frame 0。
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
- inspect 返回 ref、role、name、id、href、aria_label、placeholder、visible、disabled、read_only、input_type、truncated，**不返回输入框值**。
  文本摘要最多 160 字符（URL 2048）；字段均先脱敏再裁剪，长名称不完整时不返回可误用的前缀，优先使用 ref 定位。
- click / fill 复用 visible 判定，在可见匹配中要求唯一目标；隐藏的同名元素不参与歧义判定。
  无匹配返回 target_not_found，全部隐藏返回 target_hidden，多个可见匹配返回 ambiguous_target。
  disabled 不作为筛选条件；唯一可见目标禁用时返回 target_disabled，多个可见目标中有禁用控件仍判歧义。
  共享判断覆盖 :disabled、aria-disabled、inert、.disable、.disabled、.is-disabled（含祖先）；不使用 class 子串猜测。
  page.inspect、dom.inspect、page.read 和执行操作使用相同判断；状态不缓存，类名变化会在下一次读取反映。
  禁用状态和交互语义分别判断：普通 div 添加 disable/disabled/is-disabled 不会变成 control，仍保留文字及 disabled 状态，
  可用 dom.inspect 探查；标准控件、明确语义角色和 tabindex 提示继续按现有规则识别。
  操作前及 scrollIntoView、focus 后分别检查 URL、期限、原元素连接状态、匹配条件、可见唯一性及字段状态。
  原目标删除、替换或不再匹配返回 stale_target，变隐藏返回 target_hidden；不自动改选另一个同名元素。
  inspect 继续返回可见与隐藏元素，并保留每项 visible 字段。wait_ms 只等待匹配元素出现，不等待隐藏元素变为可见。
- fill 支持 text/search/email/url/tel input 和 textarea，使用原生原型 value setter 绕过常见 React 值跟踪，
  触发冒泡 input/change 事件；已用实际 React 18、Vue 3 组件测试。
  密码框返回 protected_input，只读返回 target_readonly，其他 input 类型和 contenteditable 暂不支持。
- 等待只针对元素出现，最多 5 秒，并受请求 expires_at 和 Bridge 15 秒超时约束；不等待点击后的导航或业务响应。
- ref 只在当前文档内弱引用并受 DOM 变更约束，不持久化、不跨文档复用；原始语义/CSS 条件每次重新查询。
  首版不支持 iframe、Shadow DOM 穿透或完整复杂富文本编辑器。
- 扫描超过 10000 个候选控件时拒绝操作；inspect 最多 20 条；所有扩展响应限制为 48 KiB，超过返回 result_too_large。

错误码集中维护于 `src/chat2local/browser/errors.py`。常见错误包括 tab_not_found、ambiguous_tab、permission_denied、
unsupported_page、page_changed、injection_failed、target_not_found、ambiguous_target、target_hidden、target_disabled、
target_readonly、protected_input、unsupported_element、stale_target、page_too_large、result_too_large、invalid_selector、invalid_cursor、scope_not_found、ambiguous_scope。
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

独立网页读取与 .disable 改进新增本地 HTML 测试，覆盖完整结构、嵌套链接层级、隐私/URL 过滤、长 Unicode 正文续读、
内容缩短/导航/文档变化时拒绝旧游标、大小限制、React/Vue 更新、授权/超时和所有禁用标记（含祖先及动态变化）。
Python 集成测试包含 page.read 的目标 Agent 隔离及读取中扩展断开；Schema 契约涵盖新增能力及字段边界。
独立网页读取初次实现结果：JavaScript `183 passed`；上列五个 Python Browser 专项文件 `115 passed`；
公开 MCP 元数据与输出 Schema 检查 `8 passed`。JavaScript/Python 语法、跨语言 Schema 契约及 `git diff --check` 通过。
本轮仍只执行模拟 Chrome / 本地测试服务，不操作真实页面，不重启正式 Core 或扩展；待审查后再进行真实 Chrome 验收与 Handoff 阶段同步。

接口审查修复在上述 183/115 基线上增加共享输出脱敏、文档内弱引用与完整发现契约：
JavaScript 新增跨 read/inspect/dom.inspect/操作/歧义候选的凭据回归、裁剪边界、同名不同 Query 链接精确点击、
替换/重排/删除/属性与文本变化后的旧 ref 拒绝、同 URL 跨文档拒绝、权限撤回及滚动/聚焦再校验。
调用场景只依赖导出的 discover 信息，覆盖唯一 tab 选择、正文续读、语义/局部 DOM 分工、禁用后停止与页面变化后重新读取；
还逐一比对 8 个真实打包操作的成功顶层字段与 result_contract。
Python 新增 nullable 跨字段 Schema/Pydantic 对照、所有参数说明检查、共享错误指导与发现体积检查；
原有跨语言边界测试及 HTTP MCP→Hub→目标 Agent 集成现在包含 ref 参数、候选 ref 和完整发现元数据。
这些是确定性契约与场景测试，没有运行真实 LLM 评测，也未运行无关全量 Python 回归。
本轮接口审查修复最终结果：JavaScript `200 passed`（新增 17），Python Browser 专项 `126 passed`（新增 11）；
JS/Python 语法、标准 JSON Schema/Pydantic/扩展线协议契约与 `git diff --check` 通过。
8 个能力的完整紧凑发现结果约 34 KiB；契约测试限定小于 40000 UTF-8 字节，避免输出 Schema 无限膨胀。
仍待真实 Chrome 验收的事项：在审查通过后的独立本地测试页，用 read 返回的两个同名链接 ref 分别点击；
替换/重排后旧 ref 必须 stale_target，重新读取得到新 ref 后再操作；确认所有只读/操作/歧义摘要无测试凭据。
当前无上述已确认 P1/P2 的未修复项；真实 Chrome 布局/注入验证仍未进行。
可选后续优化是更细粒度的引用失效策略和更广的业务秘密识别，当前保守失效与脱敏规则是明确边界，不属于本轮新增操作范围。

### page.read 与 .disable 的后续人工验收

1. 审查通过后更新 Core 并重新加载扩展，在独立测试环境授权本地 HTTP 测试页，discover 确认 8 个能力。
2. 用包含标题、列表、表格、链接、图片描述、隐藏内容和表单字段的本地测试页调用 page.read，检查结构顺序、隐私过滤及 URL 脱敏。
3. 长正文用 max_chars=1000 连续传 next_cursor 到 has_more=false；改变可见正文后，旧游标必须 page_changed。
4. 本地自定义控件添加/移除精确的 disable 类，检查三种读取方式的 disabled 状态；禁用时 click 必须 target_disabled 且无点击副作用。
   这一步仅使用本地模拟控件，不操作真实 BOSS 发送按钮。确认页面观察不等于业务成功判断。

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
JavaScript `34 passed`。Schema 文档与当时 Registry 的六个模型一致，`git diff --check` 通过（DOM 探查后为七个，本轮独立读取后为八个）。

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
