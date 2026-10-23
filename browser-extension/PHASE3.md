# Browser Use Phase 3 — 完整 DOM 交互

Phase 3 在 [Phase 2](PHASE2.md) 的定位、弱引用和授权机制上增加八个内部 Capability。
公共 MCP Tool 仍为 12 个，Browser Capability 共 16 个。没有修改 manifest 权限、WebSocket version 1、
设备路由或 15 秒 Bridge 超时，没有增加生产依赖、CDP/debugger 或第二套输入后端。
本轮不包含浏览器窗口管理、导航、书签或其他后续阶段能力，也没有提交、推送或发布版本。

统一更新 Core 与扩展后，用 `capability_discover` 的默认 summary 查询能力名称和在线状态，再按需查询 full。
full 模式与 [capability-schemas.json](capability-schemas.json) 提供参数 Schema、`result_contract`、
共享 `conventions` 和 `error_guidance`；以目标设备实际返回的 `available` 为准。
旧扩展仍可只声明已有能力，新能力会独立显示不可用。

## 两级能力发现

先查询概览，省略 detail 也等价于 summary：

```json
{"device":"laptop","query":"browser","detail":"summary"}
```

成功时 result 仅含 capabilities，每项只有 name、description、available，不生成或返回 input_schema、
result_contract、conventions、error_guidance。选中能力后，显式查询 full 再执行：

```json
{"device":"laptop","query":"browser.page.scroll","detail":"full"}
```

full 返回匹配能力的完整定义，并在 result 顶层各附一份 conventions/error_guidance；
query 仍按名称/描述的大小写无关关键词 AND 搜索，省略 query 枚举全部。设备选择和实时可用性规则不变。
detail 仅允许 summary/full，显式 null、其他字符串或类型均拒绝。
导出的 capability-schemas.json 保留 full 文档格式，不代表运行时默认返回 full。

## 职责与兼容性

- `content-script.js` 继续负责定位、文档内 ref、脱敏摘要和 DOM 执行。提取了请求检查、授权复核、
  有界出现等待、操作检查、事件序列及原生选项验证函数；状态等待单独实现，不挤入 click/fill 的出现等待。
  复用现有语义/CSS 定位、可见性、disabled/inert/禁用类和文本预算，没有增加纯粹为拆文件的抽象。
- `browser-operations.js` 继续负责严格参数校验、完整 TabSelector、网站授权、documentId 注入/消息分发。
  新操作成功返回前再复核授权、完整 TabSelector 与期限；失败不会重发。
- `capabilities.py` 定义严格 Pydantic 参数与标准 JSON Schema、描述及返回契约；
  `errors.py` 维护能力白名单、固定错误消息与恢复指导。导出 Schema 和扩展校验器通过跨语言测试保持一致。
- read/inspect/dom.inspect 的读取与隐私投影、分页内容版本保持原义；第一组修复将 ref 改为节点生命周期，见下文。click/fill 保持 Phase 2 的
  唯一可见目标、scrollIntoView、focus 与执行行为。`fill(value="")` 继续用于清空文本框，不新增 clear。

## 新增 Capability

以下名称均以 `browser.page.` 开头，所有调用都要求显式 `tab`，复用 Phase 2 TabSelector。
除 page scroll 外都要求 `target`，复用 ElementTarget 的 ref、css 与语义条件，条件取 AND。
所有对象拒绝额外字段、类型转换和未声明的强制执行选项。

| 名称 | 新参数与默认值 | 行为与成功结果 |
| --- | --- | --- |
| `double_click` | `target`；`wait_ms=0`，0–5000 | 两轮 pointerdown/mousedown/pointerup/mouseup/click，再发 dblclick；鼠标按下/释放/点击 detail 为 1/2，dblclick 为 2。取消 pointerdown 时省略该轮兼容 mousedown/up，仍派发 pointerup/click。`status=double_clicked`，`event_is_trusted=false`、`navigation_waited=false`。单击处理器可能执行两次。 |
| `hover` | 同上 | 一次从文档外进入：pointerover 发给目标；pointerenter 给祖先至目标逐层各一次；然后 mouseover、同样的 mouseenter 链、pointermove/mousemove。enter 不冒泡；不聚焦、不维护跨调用指针状态、不派发离开事件。`status=hovered`、`event_is_trusted=false`、`css_hover_guaranteed=false`。 |
| `focus` | 同上 | 滚动到目标后原生 focus，并验证 activeElement；不添加 tabindex。不能实际聚焦时返回 state_not_reached；`status=focused`。 |
| `select` | 同上，加必填 `options` 数组，最多 50 项 | 仅原生 select；完整替换选中项。每项可带 `value`、`label`、`index` 中至少一个，条件取 AND。单选必须一项；多选允许 `[]` 清空。改变后各发一次冒泡 input/change，未改变时不发；返回 `status=selected`、`changed`、`selected_count`。 |
| `set_checked` | target、wait_ms，加必填严格布尔 `checked` | 原生 checkbox/radio 与 ARIA checkbox/switch；全部匹配（含隐藏项）必须唯一。已匹配状态不滚动、不聚焦、不点击；否则最多 click 一次，原节点只读核验最多 250ms。隐藏原生 input 必要时只用唯一可见关联 label。返回 `status=checked`、`checked`、`changed`、`event_is_trusted=false`。 |
| `scroll` | 必填 `container=page\|element`；`delta_x=0`、`delta_y=0`；可选 target、`wait_ms=0` | 明确容器上的相对 CSS 像素滚动；每轴范围 ±100000，不能同时为 0。element 必须有 target，page 不接受 target。返回 `status=scrolled`、container、target（页面为 null）、before/after 的 x/y 与 changed。 |
| `press_key` | target、wait_ms，加必填 `key`；`modifiers=[]` | 先聚焦并向原目标发 keydown，复核授权后向当前焦点发 keyup；失去焦点时使用 body/root。返回 `status=key_pressed`、原 target、`keyup_target`、`event_is_trusted=false`、`default_prevented={keydown,keyup}`。不插入文本、不模拟 Tab 遍历、Enter 提交或浏览器默认快捷键。 |
| `wait_for` | target、`state=visible`、`timeout_ms=5000`，0–10000 | 只读等待状态；返回 `status=matched`、state、target（不存在时为 null）、elapsed_ms。0 立即检查。失败区分 wait_timeout、stale_target、page_changed、权限和请求期限错误。 |

所有成功结果还含选中的 `tab` 及脱敏展示元信息；除 page scroll/不存在的等待目标外，均包含脱敏 target 摘要。
状态设置操作不回显输入值、选项 value/label 或键盘输入参数；select 的操作接口保持兼容。
选项发现由 inspect 的可选 select_options 提供，见下方第二组说明；其他表单输入值仍不读取。
任何成功状态都不证明业务完成，需重新 read/inspect 观察结果。

### 原生选择和勾选

`options` 的每项可使用：`value`（精确、允许空串）、`label`（折叠空白后精确、允许空串），
两者最长 256 个 Unicode 字符；`index` 是原生 select.options 的零基索引，范围 0–9999，包含 optgroup 内选项。
重复 value/label 必须用其他条件或 index 消歧，不默认第一项。全部选项先验证，再进行修改；
选项不存在、歧义、禁用或 optgroup 禁用均拒绝。重复选择同一选项、单选传 0/多项返回 invalid_arguments。
最多检查 10000 个原生选项，单个候选 label 超过 65536 UTF-16 单元时拒绝有歧义风险的 label 查询；扫描检查请求期限。
滚动/聚焦后会重新验证选项及其原节点身份。使用原生 selectedIndex/selected setter，然后 input/change，
事件处理后核验完整选中集合。没有只改 DOM 就假定 React/Vue 已接受的成功承诺。

勾选使用 HTMLElement.click() 的原生控件激活行为，兼容常见 React/Vue 的 change 监听；不先改 checked 再点击，
避免重复翻转。已选中 radio 请求 false 返回 `unsupported_state`，应选择同组另一项；
尚未选中 radio 请求 false 是无操作成功。这遵循 [HTML radio 的原生语义](https://html.spec.whatwg.org/multipage/input.html#radio-button-state-(type=radio))。
页面 preventDefault 或事件处理器撤回状态时返回 `state_not_reached`，不再点击一次。
set_checked 也支持有可读 aria-checked 的 role=checkbox/switch，只点击并观察，不写 ARIA 属性。
原生 indeterminate 与 ARIA mixed 都单独表示，不按 checked=false 提前报告未选中成功。
自定义 dropdown 继续通过基础能力组合完成，page.select 仍仅支持原生 select。

### 键盘与合成指针语义

key 支持 Enter、Tab、Escape、ArrowUp/Down/Left/Right、Home、End、PageUp、PageDown、Backspace、Delete、Space、
单个 ASCII 字母或数字；Space 对应 event.key 的一个空格。字母/数字设置 KeyA/Digit1 等 code，
其余使用对应命名 code。不补发已弃用 keypress、composition/input 或 modifier 键本身。
modifiers 只能是 Alt、Control、Meta、Shift 的不重复数组，最多 4 项，在两个事件上设置对应布尔标志。
键名和大小写由调用者显式给定，Shift 标志不自动转换字符。

press_key 拒绝密码字段，允许 readonly / aria-readonly 元素接收合成按键；fill 继续拒绝只读写入。
keydown 前目标必须实际获得焦点。keydown 处理器关闭/移除弹层、禁用原目标或转移焦点属于正常状态变化，
不会再要求旧目标保持可见/启用/焦点。keyup 跟随同一授权文档内的当前有效焦点；没有有效焦点时发给可用的 body/root，
不主动聚焦后备目标、不按原 locator 寻找替身，也不向密码接收者派发。keyup_target 是发送前的脱敏接收者摘要。
两次事件之间和 keyup 后复核授权、完整 TabSelector、页面及期限；导航/权限撤回/过期会终止并返回错误。
已知进度以 error.details.events_dispatched 的两个布尔值报告：true/false 为只发出 keydown；true/true 表示已发出两次但最终安全复核失败。
超时/断线时缺少进度表示未知，不能当作未执行。不会自动重试，也不在新页面补发 keyup。
取消 keydown 仍会发送 keyup；两个取消结果分别返回，不能据此推断任何浏览器原生默认动作执行成功。
按键目标跟随焦点的依据见 [UI Events keyup](https://w3c.github.io/uievents/#event-type-keyup) 和
[MDN keyup](https://developer.mozilla.org/en-US/docs/Web/API/Element/keyup_event)。
hover 使用目标矩形中心坐标发送事件，relatedTarget=null，将每次调用定义为从文档外新进入。
每个祖先只收到一次自身的非冒泡 enter（捕获监听器仍可观察后代事件）；无关兄弟不收到 enter。
重复调用会重复表达一次进入，不维护真实指针位置、停留或离开状态；不会真实移动指针、命中测试或建立 CSS :hover。
合成事件与真实输入的区别参考 [UI Events trusted events](https://www.w3.org/TR/uievents/#trusted-events)。

### 滚动容器和状态等待

page 仅滚动 document.scrollingElement；element 仅滚动指定元素，不先 scrollIntoView/focus，也不改滚其祖先。
元素必须在每个非零请求轴上具有 auto/scroll/overlay/hidden overflow 和足够的 scroll 尺寸，否则 target_not_scrollable。
hidden 允许程序滚动；clip 不允许，按浏览器计算后的每轴样式判断（混合轴 CSS 可能将 clip 计算为 hidden）。
使用 [scrollBy 的 instant 行为](https://developer.mozilla.org/en-US/docs/Web/API/Element/scrollBy)；边界钳制和 CSS scroll snap
可能使 after 与请求距离不同，changed=false 可以表示已经到边界。RTL 使用浏览器原生横轴坐标。
不模拟 wheel，不等待懒加载、动画、网络响应或页面业务完成。

wait_for 的定位在**包括隐藏元素的全部匹配中**要求唯一；多项立即 ambiguous_target，不把隐藏副本过滤掉后假定状态。

| state | 成功条件 |
| --- | --- |
| attached | 存在一个匹配元素 |
| detached | 没有匹配元素 |
| visible | 存在且通过共享可见性判断 |
| hidden | 不存在，或者唯一元素不可见 |
| enabled | 存在且共享 disabled 判断为 false，与可见性无关 |
| disabled | 存在且共享 disabled 判断为 true，与可见性无关 |
| actionable | 唯一可见、未禁用的 HTML 元素，pointer-events 非 none；有正尺寸视口内矩形，且采样点能命中自身或后代 |

actionable 完全只读，不执行 click/focus/scroll；DOM 命中采样不保证后续可信输入、浏览器默认行为或网站业务成功。
它不把密码、只读或特殊类型控件变为可填写：等待基础条件成功后，具体操作仍检查自己的保护条件。
例如隐藏但未禁用的元素可满足 enabled，却不满足 actionable；只读框可以满足 actionable 的基础条件，fill 仍报 target_readonly。

等待截止时间从内容脚本收到调用开始计算，正数超时要求状态观察完成时间**严格早于**截止时间，恰好到期也不算。
timeout_ms=0 只允许第一次同步状态检查取得匹配资格，该检查在任何异步授权查询前完成，不等待后来出现的元素。
初始与最终授权检查均保留，异步返回后仍重新定位、检查当前状态、ref、页面与完整 TabSelector；
这些复核只确认已及时观察的匹配，不能将超时后的新状态或替换节点提升为按时成功。
已及时匹配的同一节点仍满足状态且授权有效时可以成功，elapsed_ms 包含安全复核耗时，因此可能超过 timeout_ms；
这表示安全检查耗时，不表示额外等待新状态。整体仍受请求 expires_at 和 Bridge 15 秒期限约束。

轮询间隔最多 50ms，约每 250ms 以及匹配/超时返回前复核当前授权和完整 TabSelector。
超时后继续必要的安全复核：权限撤回、页面/选择条件变化、请求期限和 ref 失效仍分别返回
permission_denied、page_changed、request_expired、stale_target；安全条件有效但未按时满足状态才返回 wait_timeout。
不跨导航等待，也不会默认转向另一个文档。
ref 保留原节点身份，原节点的 hidden/disabled 等状态变化现在可以被同一 ref 等待；节点移除/替换仍为 stale_target，
**不能作为元素消失的证明**。等待可能重建节点的动态页面优先使用明确的语义/CSS 条件；CSS 本身不承诺跨请求身份。

## 操作安全和错误恢复

准备阶段复用原有页面/期限、ref、原节点连接、匹配条件、可见唯一性、disabled/inert/禁用类及字段状态检查，
在滚动与聚焦后再检查。hover 不聚焦；scroll 不执行隐式滚动准备；wait_for 完全不执行输入动作。
指针事件开始后固定已验证的原节点身份，不再重查旧文案或 CSS 条件；原节点替换/删除（包括移除后重插）、隐藏、禁用、URL 或期限变化会停止后续事件。
hover 还会验证进入路径，避免中途重挂后向旧祖先补发 enter。键盘以当前文档焦点派发 keyup，采用上文单独的生命周期规则。
ref 是节点身份而非旧属性快照：ref 单独使用时允许原节点属性变化；显式叠加条件在下一次请求及准备阶段仍取 AND 重新验证。
MutationObserver 只处理 childList 中实际移除的元素/子树，不因文本节点删除、无关更新或属性更新清空全表；
每次解析同步消费待处理记录，并验证 WeakRef、isConnected、ownerDocument 和 URL。节点不会被重新定位成另一个元素。
原有 click/fill 的返回与事件执行行为保持兼容，填值、密码和只读保护不放宽；ref 现在允许无关 focus 状态更新。

新错误包括 `option_not_found`、`ambiguous_option`、`option_disabled`、`unsupported_state`、`state_not_reached`、
`target_not_scrollable`、`wait_timeout`。其余继续复用已有错误码和最多 5 条脱敏歧义候选，不透传异常文本或选项值。
授权仍要求扩展既有显式 opt-in 与 Chrome 当前权限共同通过；MCP 无法申请授权或扩大范围。
单次动作可以在失败前执行部分事件或准备动作；最终授权检查也可能在动作完成后返回错误。
因此任何错误、超时、断线、扩展重启都不能证明没有副作用；必须先观察，绝不自动重放。

## 自动化与受控浏览器验收

在仓库根目录执行（开发依赖已安装时无需重复 npm ci）：

```powershell
npm test --prefix browser-extension
uv run pytest -q tests/test_browser_capabilities.py tests/test_browser_phase2.py tests/test_browser_phase3.py tests/test_browser_contract.py tests/test_browser_bridge.py tests/test_browser_integration.py tests/test_capability_discovery.py tests/test_mcp_surface.py tests/test_mcp_metadata.py tests/test_mcp_factory.py tests/test_dispatch.py tests/test_dispatch_logging.py tests/test_devices.py tests/test_agent_logging.py tests/test_management_browser.py
```

新增 JS 专项在 dom-interactions、wait-for、phase3-operations 三个测试文件中，覆盖所有正常操作、
真实 React 18/Vue 3 模型与事件、原生选项错误、幂等勾选、容器边界、动态 DOM、旧 ref、隐禁状态、
部分事件失败、授权/TabSelector/期限的前后复核和明确请求恢复。继续运行全部 Phase 1/2 扩展回归。
Python 专项覆盖严格输入和标准 JSON Schema、导出文档、JS 线协议字段/边界/组合约束、
16 能力发现与可用性、固定错误、超时/断线后不重放、HTTP MCP→Hub→两个 Agent 的设备隔离，及公共 Tool 仍为 12 个。
初版验收结果为 JavaScript 350 passed、Python 228 passed；下方审查修复记录描述本轮验证。

### 审查修复：等待边界、actionable 与两级发现

新增隔离时钟与显式 Promise gate 的确定性回归，覆盖 0ms 首次未匹配、授权期间出现、正数截止前/恰好截止/截止后、
及时匹配后慢授权、最终隐藏/替换、同步扫描耗时及权限/Tab/URL/请求期限/ref 错误分类，不依赖实际调度速度。
actionable 覆盖动态出现、隐藏/禁用/inert/ARIA/禁用类、歧义、旧 ref、只读无副作用，以及具体操作保护仍然生效。
两级发现覆盖默认 summary、full、非法参数、不生成 Schema、查询/在线状态一致性、输出 Schema 和完整双 Agent 路由。
性能测试验证 summary 不含完整定义且显著小于 full，以比例比较而非精确字节数作为断言。
本轮审查修复验证结果：Browser Use JavaScript 全部 **385 passed**，上述 Python Browser/MCP/Dispatcher/设备相关回归
**364 passed**，均无失败或跳过；JSON Schema/模型/扩展线协议一致性、JS/Python 语法和 `git diff --check` 通过。

同一未连接 Registry、UTF-8 紧凑 JSON（包括 ok/result 封装）的实测值，实际大小会随描述和在线状态略变：

| 查询 | 字节数 |
| --- | ---: |
| 全部 16 项 summary（也是默认行为） | 4,447 |
| 全部 16 项 full | 90,411 |
| browser.page.scroll 的 full | 13,434 |

summary 比完整发现约小 95.1%，不加入 Schema 缓存或额外协议。
本轮只在源码与隔离自动化环境验证，没有重启正式 Core、更新腾讯云、重建 EXE 或重载日常 Chrome 扩展。

受控浏览器复用 Phase 2 的 manual_runtime/manual_check，新增白名单 `/phase3` fixture。
manual_check 只创建本地测试标签页，不选择已有个人标签页；只重试初始只读观察，不重放动作。
同时修复旧助手写死六个能力，以及误把已脱敏 URL 与带 query 的原始 URL 比较/定位的问题。

```powershell
uv run python browser-extension/tests/manual_runtime.py --config "<独立测试config.yaml>" --directory "<独立runtime目录>"
uv run python browser-extension/tests/manual_check.py --report "<验收结果.json>"
```

使用独立 Chrome/Chromium profile，打开开发者模式加载本扩展，配对独立测试 Core，并只在选项页授权
`http://127.0.0.1:18766`；保留 localhost 未授权。helper 使用 18765（MCP）、18766（fixture）及独立 browser.port。
未打包扩展需要保持开发者模式开启，关闭该开关会使 Chrome 在重载后停用扩展。

初版于 2026-10-23 完成隔离 Chromium 148.0.7778.96 的真实 MCP→Hub→Agent→MV3→Content Script 验收，
涵盖全部 16 个能力、普通 HTML 和 React/Vue 模型、选项拒绝、实际容器/页面滚动、动态状态及未授权主机拒绝。
验收助手 **17 组检查通过**；另 **2 项断线恢复检查通过**：真实 MV3 重载中断正在等待的调用返回
capability_unavailable，自动重连后明确发起的新请求可重新注入并成功。
正式 Agent、日常浏览器配置、网站授权和业务网站均未改动；测试完成后停止独立 helper 并关闭隔离浏览器。

不在日常 Windows 电脑执行完整 Python 进程压力测试；跨平台完整回归交给 CI。
未声称覆盖全部 React/Vue 版本、复杂组件库、浏览器原生 OS 快捷键、真实用户手势、IME、真实 pointer 命中、
长时间休眠/系统重启及其他浏览器版本。iframe、Shadow DOM、富文本和未提供可靠状态/关联的自定义控件仍保持既有边界。
下述第一组修复已调整引用失效粒度；浏览器版本矩阵和复杂控件覆盖仍需继续完善，不扩展权限或输入后端。

### 第一组问题修复：#1、#2、#3、#4、#5、#12

| 问题 | 根因 | 本轮修复 |
| --- | --- | --- |
| #1 ref 过度失效 | MutationObserver 把所有 DOM 更新映射为全表失效 | 仅使移除节点/子树的 ref 失效，保留同一节点身份；解析时检查文档、URL、连接状态和待处理移除记录。操作前重新验证显式条件及状态。 |
| #2 正常键盘交互中止 | keyup 前后复用旧目标可见性与焦点要求 | keydown 使用准备后的原目标，keyup 跟随当前同文档焦点；正常关闭/移除/禁用/焦点转移不再按旧目标报错。每次安全复核失败仍终止，不重试。 |
| #3 readonly 限制过宽 | fill 与 press_key 共用只读拒绝规则 | press_key 允许 readonly/aria-readonly，保留密码保护；fill 和原生状态设置的只读保护保留。 |
| #4 双击属性变化中止 | 后续事件重新使用初始文案/CSS 定位 | 输入开始后固定原节点，仅复核身份和安全状态；补全 pointerdown/up 与对应鼠标事件，处理 pointerdown 的兼容鼠标取消规则。 |
| #5 父级 enter 缺失 | 非冒泡 enter 只派发给目标 | 对原进入路径从外到内逐层派发一次 pointerenter/mouseenter；不发给兄弟，不把 enter 设为冒泡，不维护长期指针状态。 |
| #12 hidden 容器被拒绝 | 可程序滚动 overflow 白名单不包含 hidden | 每个非零请求轴允许 hidden，继续检查溢出尺寸；计算样式为 clip 时拒绝，不滚祖先、不聚焦。 |

兼容性：12 个公共 Tool、16 个 Capability、参数名及默认值不变；没有权限、生产依赖、协议或输入后端变更。
click/fill/read/inspect 的操作与隐私边界保留；分页内容版本仍排除 ref，属性/无关更新不再要求续读时更新 ref。
press_key 成功结果新增 keyup_target；错误可附白名单 events_dispatched，Python 错误过滤与公共 MCP 返回 Schema 已同步。
过去刻意依赖“无关更新必须 stale_target”的测试已改为验证同节点保持、真移除失效；ref 等待现在能观察同节点 hidden/disabled 改变。

回归集中在 `tests/phase3-fixes.test.mjs`，并调整旧的 ref、分页、等待、事件及契约测试。
覆盖全部目标操作的无关更新、ref+条件复核、移除/替换/重插/跨文档收养、隐藏/禁用、键盘焦点生命周期、
权限/导航/过期/密码接收者中断、双击首击文案变化与危险变化、父级进入顺序/捕获/兄弟隔离、hidden/clip 滚动。
Python 测试同时验证实际 Bridge 进度传输、不重放、非法进度字段过滤、MCP 错误输出 Schema 和发现/导出契约一致性。

本轮结果：扩展全部 JavaScript **426 passed**；上述 Python Browser/MCP/Dispatcher/设备回归 **376 passed**，无失败或跳过。
JS/Python 语法、Schema 一致性与 `git diff --check` 通过。未运行完整进程压力测试。
另在独立 Chromium **148.0.7778.96** 中，用实际打包 Content Script 和原生 DOM 完成 **9 组检查**：
ref 聚焦更新、键盘隐藏/移除/焦点转移/readonly、双击改文案、祖先 enter、hidden 与 clip 的实际滚动。
该补充检查使用受控 runtime 授权桩验证 DOM 行为，不是新一轮真实 MV3→正式 Core 验收；不加载扩展、不操作个人网页。
隔离浏览器已关闭，正式 Core/Hub/Chrome 扩展未重启、部署或重载。

仍保留 DOM-only 限制：合成事件不保证可信输入、原生默认行为、CSS :hover、真实命中、文本选择或用户手势；
hover 每次均表达从外部进入，不支持跨调用离开/停留状态。第一组当时未包含第二组问题；下节记录后续完善。
复杂组件库、其他浏览器版本及真实业务流程仍需后续受控验收。
本轮改动仅留本地，待独立复审后统一更新，不执行 Commit/Push/Tag/Release。

### 第二组功能完善：#6、#7、#8、#9、#10、#11、#13、#14

保留 12 个公共 Tool、16 个 Browser Capability；没有新增权限、生产依赖或输入后端。
继续由内容脚本处理 DOM、引用和状态核验；后台做参数校验、授权和分发，Python 注册和导出的 Schema 同步。
没有引入组件库专用适配器，也没有给 select 添加自定义下拉框状态机。

| 问题 | 实现与安全边界 |
| --- | --- |
| #6 原生选项发现 | inspect 新增可选 select_options={offset,limit}，必须显式 target 且含隐藏项唯一匹配原生 select；返回 label/value/index/selected/disabled。每页最多 50 项、选项字节预算 16 KiB；按实际 next_offset 继续。沿用文本/URL 脱敏、10000 项上限及请求期限；select、option、optgroup 或祖先的凭据标记会返回 protected_input，不读其他输入值。 |
| #7 自定义下拉框 | 统一读取 expanded/controls/owns/active_descendant/selected；dom.inspect 保留关键 ARIA 属性。可在顶层文档其他位置读取 Portal/Teleport 的 listbox/option。Agent 明确执行六步组合操作，每步结果可检查，无自动展开/选择。 |
| #8 ARIA Checkbox/Switch | 支持 role=checkbox/switch 且当前 aria-checked 可读。匹配目标状态时无操作；否则仅正常 DOM click 一次，原节点只读核验最多 250ms。未知或未达状态返回 state_not_reached，不写 aria-checked、不重放。 |
| #9 隐藏原生 Input | 使用原生 input.labels / label.control 关联。需要激活时必须唯一可见 label，分别固定 input 和 label 引用；准备检查连接、关联、可见、disabled/inert/readonly，点击后保留身份/关联核验，允许正常隐藏或禁用。没有可靠 label 返回 target_hidden，多 label 或多目标返回 ambiguous_target，不猜测外观节点。 |
| #10 三态 Checkbox | 原生 indeterminate 优先于 checked；ARIA checkbox 支持 mixed。混合态不等于未选中，不能零操作成功。一次原生激活仍无法到达明确 true/false 时返回 state_not_reached，不做第二次点击。radio 仍不能直接取消已选中状态。 |
| #11 统一状态 | inspect 与 dom.inspect 共用状态投影；保留旧摘要字段。DOM 属性改为固定 20 项白名单，关键 ARIA 属性不再被前六项挤掉。输入值、data-*、on*、style 等不导出，原有文本/节点/响应预算继续生效。 |
| #13 滚动发现 | 两种观察都返回每轴计算 overflow、position、min/max、scrollable、at_start/at_end；支持 auto/scroll/hidden，clip 按实际计算样式识别。读取不滚动、不聚焦；scroll 接口不变，也不寻找其他容器。坐标遵循原生 RTL、writing-mode 和 flex reverse 方向。页面根节点沿用 document.scrollingElement 的特殊滚动语义。 |
| #14 actionable | 检查共享可见/禁用状态、pointer-events、有效视口交集及 elementFromPoint；最多 20 个矩形，每个最多 9 个采样点。允许后代命中、部分出屏和中心被遮而边缘可达；完全遮挡、clip 裁剪等不通过。不自动移动视口或聚焦。 |

接口增加一个可选参数，已有操作参数及默认值保持不变：

```json
{"device":"laptop","capability":"browser.page.inspect","arguments":{"tab":{"tab_id":123},"target":{"ref":"el_copy_from_latest_result"},"select_options":{"offset":0,"limit":20}}}
```

offset 默认 0、范围 0–9999；limit 默认 20、范围 1–50；省略 select_options 不读取选项 label/value。
返回元素上的 select_options 包含 options、total、offset、has_more、next_offset、truncated。
options 项含 index、脱敏 label/value、selected、disabled、truncated。页内字节预算可能让实际项数少于 limit，
始终按 next_offset 继续；has_more 表示仍有选项，truncated 也可表示字段裁剪。此分页读取当前 DOM，不提供跨请求冻结快照。
脱敏/裁剪后的 label/value 不一定能精确匹配原值，应使用当前 index 或重新观察后的已知条件，动态重排后重新读取。

两个观察接口都给每个元素添加 state、scroll，dom.inspect 还补齐 read_only：

- state 基础字段为 disabled、read_only；按控件类型附 check_state（checked/unchecked/mixed/unknown）、checked、indeterminate、selected、expanded。
  原生 mixed 时 checked 保留底层布尔值；ARIA mixed/unknown 的 checked 为 null。不要单独用 checked 判断三态。
- controls / owns / active_descendant 对应经过脱敏裁剪的 aria-controls / aria-owns / aria-activedescendant。
  input combobox 也能读取关联信息，但仍不读其用户输入值。ARIA 状态只是页面声明，不证明业务状态。
- 原生 select 的 selection={multiple,total_options,selected_count,selected_indices,has_more}，最多给出前 20 个选中索引；
  全量选项及各项选中状态用 select_options 分页查看，不自动读取所有 label/value。
- scroll={is_page,x,y}，每轴含 overflow、position、min、max、scrollable、at_start、at_end；边界容差 1 CSS px。
  scrollable 表示可程序滚动且有溢出区域，不承诺懒加载成功。计算 overflow 为 hidden 可程序滚动，为 clip 的普通元素不可。

自定义下拉框的实际验证流程为 inspect combobox → click 原 ref → wait_for listbox/option → dom.inspect Portal/Teleport 选项 →
click 选项 ref → inspect 原 combobox ref 验证展开、活动项及选择文案。React Portal 与 Vue Teleport 的隔离真实 Chromium 测试均通过，
选项卸载后旧 ref 仍返回 stale_target，没有把它当作 detached 成功或定位另一项。

本轮专项测试：`phase3-controls.test.mjs`、`phase3-geometry.test.mjs`；旧测试只更新新增字段、固定属性白名单和布局桩。
重点验证分页/脱敏/预算、三态、幂等、单次点击/延迟状态核验、实际 React/Vue 模型、隐藏 input 关联/重复 ID/多 label、
准备时节点或关联变化、不支持目标不回退、关键 ARIA 保留、滚动信息和只读命中判断。
Python 增加分页参数严格校验、Schema/模型/JS 边界一致性及真实 BrowserBridge 的参数默认值/状态传输、不自动重放检查。

范围内测试命令（不要在日常电脑改成完整 pytest/压力测试）：

```powershell
node --test browser-extension/tests/*.test.mjs
.venv/Scripts/python.exe -m pytest -q tests/test_browser_capabilities.py tests/test_browser_phase2.py tests/test_browser_phase3.py tests/test_browser_contract.py tests/test_browser_bridge.py tests/test_browser_integration.py tests/test_capability_discovery.py tests/test_mcp_metadata.py tests/test_mcp_surface.py tests/test_mcp_factory.py
.venv/Scripts/python.exe browser-extension/tests/chromium_controls.py --chromium "<已有 Chromium 可执行文件>" --report .test-tmp-phase3/group2-native.json
```

真实浏览器 helper 只使用 Chromium 命令行和标准库 loopback 服务，单个临时 profile，资源仅来自本地白名单。
不使用 CDP、Playwright、debugger 或真实输入后端；运行实际打包 Content Script，授权使用受控桩，**不代表新一轮 MV3→Core 验收**。
不会重载日常扩展或读取已有标签页，结束后关闭测试实例与本地服务。

本轮最终验证：扩展 JavaScript **476 passed**，上述改动范围内 Python **275 passed**，Chromium 148 隔离页面 **14 组通过**，无失败或跳过。
Chromium 检查包含后代命中、完全/中心遮挡、pointer-events/零尺寸/出屏/裁剪、动态移除遮罩、真实 overflow/根节点滚动、
RTL/writing-mode/flex reverse 原生坐标范围、原生选项、三态/隐藏 input label、React Portal/Vue Teleport 六步流程和实际框架状态模型。
JS/Python 语法、跨语言契约与导出 Schema、一致性及 `git diff --check` 通过；未执行完整 pytest、压力测试或业务网站操作。
自审核对了每项需求、单次激活及失败后不重放、原目标/label 关联保护、敏感 option/optgroup 读取保护与观察只读性，
修复勾选返回摘要保持操作前快照、label 资源/readonly 边界，并用实际 Chromium 验证反向滚动坐标；当前回归未发现第一组修复受损。
改动保留工作区，未 Commit/Push/Tag/Release，正式 Core、腾讯云 Hub 与日常 Chrome 扩展均未重启或更新。

仍未支持：无可靠 ARIA 状态的组件、任意外观代理、ARIA radio 的状态设置、跨 iframe/Shadow DOM、未加载的虚拟化选项。
只把选择写入 input.value 且不提供可观察 ARIA/文案的自定义 combobox 无法通过这套隐私投影确认选择，需要网站提供可观察状态。
需要可信用户手势或原生键盘默认行为的控件仍可能拒绝合成事件。250ms 核验只覆盖常见响应式更新，更慢业务更新应显式重新 inspect，不能盲目再点。
actionable 的有限采样可能保守拒绝极窄可点击区域、复杂 transform 或超过 20 个碎片的元素；采样成功也不保证下一时刻没有遮挡，
不承诺真实事件、浏览器默认行为或业务成功。选项分页不强制懒加载，ARIA 关系不跨文档或自动寻找替代控件。
第一组引用、键盘、双击、hover、hidden 滚动专项仍纳入全部 JS 回归；read/click/fill 的执行路径保持兼容。

### set_checked 最终修复：区分准备检查与结果核验

点击后曾复用完整操作 guard，导致“已选中后正常隐藏、禁用或变只读”误报失败。
现在点击前保持完整唯一性、可见性、disabled/readonly、节点及 label 关联检查；点击后的核验仅检查原节点/控件身份、
激活节点和 label.control 关联、文档/URL/期限及授权，再读取目标状态。正常隐藏、禁用或只读不再否定已达到的状态。
节点删除/替换/重插、控件身份改变或 label 改绑仍拒绝，不改指其他节点；未达到目标仍为 state_not_reached。
权限撤销不能因状态已匹配而返回成功。250ms 核验窗口和现有安全复核耗时规则不变，最多点击一次。

新增 `set-checked-completion.test.mjs` 的 30 项功能测试覆盖原生/ARIA 控件成功后的状态变化、未达目标、
节点生命周期、label 安全性、权限/页面/期限检查和不重放；真实 Chromium 补充同样的成功场景。
不改敏感 select 状态策略、其他能力、Schema 或公共 Tool；本轮仍保留工作区改动，等待独立复审。
最终验证：扩展 JavaScript **506 passed**（含新增 30 项）、相关 Python Browser/MCP 契约 **275 passed**，
受控 Chromium **15 组通过**；语法检查与 `git diff --check` 通过。高并发首轮一项原有 200ms 动态等待测试超时，
降低为 `node --test --test-concurrency=2 browser-extension/tests/*.test.mjs` 后全部通过，未修改该能力或测试条件。
未运行全量 pytest、未重启或更新正式服务/扩展；当前回归未发现第一组或第二组其他能力受损。

## 复审后的统一部署

本轮仅保留工作区改动，先等待独立复审，不自动部署、提交或发布。复审通过后在同一更新窗口完成：

1. **Hub**：更新源码/分发包中的 MCP Tool 参数、输出 Schema、元数据和 instructions，再按计划重启，使其接受 detail 并发布新默认行为。
2. **各 Core/Agent**：更新 LocalDispatcher、CapabilityRegistry、Pydantic 和完整发现契约，再按计划重启对应实例；正式 EXE 用户届时统一构建、替换，当前不执行。
3. **Chrome 扩展**：更新本次 browser-operations.js 与 content-script.js，再由用户重新加载相应扩展；无需增加网站权限。
4. 刷新 MCP 客户端工具定义，先查询 summary，再查询 wait_for 的 full 确认 actionable，最后在受控页面复核 0ms 超时与新状态。

DeviceRouter、DeviceSession 和 Agent 请求传输使用原有通用 arguments JSON，无需新协议层。
新 Hub 会把 detail 传至 Agent，旧 Agent 的严格模型会拒绝该参数；旧 Hub 也未发布新参数。
因此不承诺新旧版本混用，不在腾讯云暂未同步时切换正式调用链，也不自动回退或修改 capability_execute。
