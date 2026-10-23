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
- read/inspect/dom.inspect 的读取与隐私投影、分页内容版本和 ref 策略保持原义；click/fill 保持 Phase 2 的
  唯一可见目标、scrollIntoView、focus 与执行行为。`fill(value="")` 继续用于清空文本框，不新增 clear。

## 新增 Capability

以下名称均以 `browser.page.` 开头，所有调用都要求显式 `tab`，复用 Phase 2 TabSelector。
除 page scroll 外都要求 `target`，复用 ElementTarget 的 ref、css 与语义条件，条件取 AND。
所有对象拒绝额外字段、类型转换和未声明的强制执行选项。

| 名称 | 新参数与默认值 | 行为与成功结果 |
| --- | --- | --- |
| `double_click` | `target`；`wait_ms=0`，0–5000 | 两轮 mousedown/mouseup/click，detail 分别为 1/2，再发 detail=2 的 dblclick；`status=double_clicked`，`event_is_trusted=false`、`navigation_waited=false`。单击处理器可能执行两次。 |
| `hover` | 同上 | 依次派发 pointerover/pointerenter/mouseover/mouseenter/pointermove/mousemove；enter 不冒泡；不聚焦、不建立真实指针状态、不派发离开事件。`status=hovered`、`event_is_trusted=false`、`css_hover_guaranteed=false`。 |
| `focus` | 同上 | 滚动到目标后原生 focus，并验证 activeElement；不添加 tabindex。不能实际聚焦时返回 state_not_reached；`status=focused`。 |
| `select` | 同上，加必填 `options` 数组，最多 50 项 | 仅原生 select；完整替换选中项。每项可带 `value`、`label`、`index` 中至少一个，条件取 AND。单选必须一项；多选允许 `[]` 清空。改变后各发一次冒泡 input/change，未改变时不发；返回 `status=selected`、`changed`、`selected_count`。 |
| `set_checked` | 同上，加必填严格布尔 `checked` | 仅原生 checkbox/radio。已匹配状态不滚动、不聚焦、不点击；否则准备后再判断，最多 click 一次并验证结果。返回 `status=checked`、`checked`、`changed`、`event_is_trusted=false`。 |
| `scroll` | 必填 `container=page\|element`；`delta_x=0`、`delta_y=0`；可选 target、`wait_ms=0` | 明确容器上的相对 CSS 像素滚动；每轴范围 ±100000，不能同时为 0。element 必须有 target，page 不接受 target。返回 `status=scrolled`、container、target（页面为 null）、before/after 的 x/y 与 changed。 |
| `press_key` | target、wait_ms，加必填 `key`；`modifiers=[]` | 先聚焦，再只发一次 keydown/keyup；返回 `status=key_pressed`、`event_is_trusted=false`、`default_prevented={keydown,keyup}`。不插入文本、不模拟 Tab 遍历、Enter 提交或浏览器默认快捷键。 |
| `wait_for` | target、`state=visible`、`timeout_ms=5000`，0–10000 | 只读等待状态；返回 `status=matched`、state、target（不存在时为 null）、elapsed_ms。0 立即检查。失败区分 wait_timeout、stale_target、page_changed、权限和请求期限错误。 |

所有成功结果还含选中的 `tab` 及脱敏展示元信息；除 page scroll/不存在的等待目标外，均包含脱敏 target 摘要。
状态设置操作不回显输入值、选项 value/label 或键盘输入参数；select 只读取必要原生选项字段来验证调用者明确提供的选择条件。
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
自定义 ARIA checkbox/dropdown 不由这两个能力强行设置，返回 unsupported_element。

### 键盘与合成指针语义

key 支持 Enter、Tab、Escape、ArrowUp/Down/Left/Right、Home、End、PageUp、PageDown、Backspace、Delete、Space、
单个 ASCII 字母或数字；Space 对应 event.key 的一个空格。字母/数字设置 KeyA/Digit1 等 code，
其余使用对应命名 code。不补发已弃用 keypress、composition/input 或 modifier 键本身。
modifiers 只能是 Alt、Control、Meta、Shift 的不重复数组，最多 4 项，在两个事件上设置对应布尔标志。
键名和大小写由调用者显式给定，Shift 标志不自动转换字符。

press_key 拒绝密码和只读字段；目标必须实际获得焦点。页面处理器仍可以根据事件产生副作用。
取消 keydown 仍会发送 keyup；两个取消结果分别返回，不能据此推断任何浏览器原生默认动作执行成功。
hover 使用目标矩形中心坐标发送事件，不执行命中测试，也不会真实移动指针或可靠建立 CSS :hover。
合成事件与真实输入的区别参考 [UI Events trusted events](https://www.w3.org/TR/uievents/#trusted-events)。

### 滚动容器和状态等待

page 仅滚动 document.scrollingElement；element 仅滚动指定元素，不先 scrollIntoView/focus，也不改滚其祖先。
元素必须在每个非零请求轴上具有 auto/scroll/overlay overflow 和足够的 scroll 尺寸，否则 target_not_scrollable。
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
| actionable | 唯一 HTML 元素存在、可见，且共享 disabled/inert/禁用类判断为 false；只表示基础 DOM 条件 |

actionable 完全只读，不执行 click/focus/scroll，不保证业务成功、命中测试或网站接受合成事件。
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
ref 在任意 DOM 变化后失效的保守策略不变：旧 ref 始终 stale_target，**不能作为元素消失/隐藏的证明**。
等待动态状态优先使用明确的语义/CSS 条件；CSS 本身也不承诺跨请求身份。

## 操作安全和错误恢复

准备阶段复用原有页面/期限、ref、原节点连接、匹配条件、可见唯一性、disabled/inert/禁用类及字段状态检查，
在滚动与聚焦后再检查。hover 不聚焦；scroll 不执行隐式滚动准备；wait_for 完全不执行输入动作。
事件开始后，在同一次同步事件序列内绑定已验证的原节点继续检查；自身事件造成的无关 DOM 更新可以使公共 ref 失效，
但不会触发重新解析该 ref、位置 CSS 回退或切换节点。原节点替换/删除、条件失配、隐藏、禁用、URL 或期限变化会停止后续事件。
当前序列结束后，后续请求仍必须使用重新观察的 ref。原有 click/fill 的返回与事件执行行为保持兼容。

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
长时间休眠/系统重启及其他浏览器版本。iframe、Shadow DOM、富文本/自定义 ARIA 控件仍保持既有边界。
后续可在独立阶段改进引用失效粒度、浏览器版本矩阵和复杂控件覆盖，本轮不扩展权限或输入后端。

## 复审后的统一部署

本轮仅保留工作区改动，先等待独立复审，不自动部署、提交或发布。复审通过后在同一更新窗口完成：

1. **Hub**：更新源码/分发包中的 MCP Tool 参数、输出 Schema、元数据和 instructions，再按计划重启，使其接受 detail 并发布新默认行为。
2. **各 Core/Agent**：更新 LocalDispatcher、CapabilityRegistry、Pydantic 和完整发现契约，再按计划重启对应实例；正式 EXE 用户届时统一构建、替换，当前不执行。
3. **Chrome 扩展**：更新本次 browser-operations.js 与 content-script.js，再由用户重新加载相应扩展；无需增加网站权限。
4. 刷新 MCP 客户端工具定义，先查询 summary，再查询 wait_for 的 full 确认 actionable，最后在受控页面复核 0ms 超时与新状态。

DeviceRouter、DeviceSession 和 Agent 请求传输使用原有通用 arguments JSON，无需新协议层。
新 Hub 会把 detail 传至 Agent，旧 Agent 的严格模型会拒绝该参数；旧 Hub 也未发布新参数。
因此不承诺新旧版本混用，不在腾讯云暂未同步时切换正式调用链，也不自动回退或修改 capability_execute。
