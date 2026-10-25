# Chat2Local Browser Use

可选、独立安装的 Chrome Manifest V3 扩展，提供标签页创建/查询/选择与管理、普通窗口管理、基础导航、独立网页读取与分段续读，以及授权网页的 DOM 定位、点击/填写、双击/悬停/聚焦、原生选择/勾选、滚动、合成按键与元素状态等待。
**Chat2Local Browser Use 独立负责读取、定位、操作和结果观察，不再依赖官方 ChatGPT Chrome 插件读取页面。**
推荐按需使用：`tabs.list / resolve → page.read → page.inspect / dom.inspect → click / fill → page.read 再观察`。
公共 MCP Tool 保持 12 个；浏览器能力为 28 个，统一通过 capability_discover / capability_execute 调用。
接口参数以 `capability_discover(detail="full")` 和 [capability-schemas.json](capability-schemas.json) 为准；
阶段实现及历史验收记录可通过 Git 历史查阅。
能力发现默认 summary，只返回 name/description/available；按需查询 detail=full 才提供参数 Schema、各能力的 `result_contract`、共享 `conventions` 和 `error_guidance`。
读取/检查结果提供安全 `ref` 时，后续用 `target: {"ref": "实际返回的ref"}` 定位；展示 URL/名称已脱敏，不能当作原始精确条件。
ref 固定同一文档/URL 中的节点身份，无关 DOM 和原节点文字/属性更新不会使它失效；节点移除（含移除后重插）、替换、导航或淘汰才失效。
每次操作及滚动/聚焦准备后仍重新验证显式定位条件和控件状态。返回 stale_target 后重新观察，不自动回退位置型 CSS。

标签页、窗口与导航使用已认证的 Browser Bridge；网页内容读取与 DOM 操作继续要求网站授权。

| Capability | action / 参数 |
| --- | --- |
| `browser.tabs.manage` | `activate`、`close`；`move` 必须传 `index`（`-1` 表示末尾），可选目标 `window_id`；`set_pinned` 必须传布尔 `pinned`。均使用已有显式 `tab` 选择器，拒绝零匹配和多匹配。移动仅支持普通窗口，返回 Chrome 实际索引。 |
| `browser.windows.list` | 可选精确 `window_id`；`limit` 只控制窗口数量（默认 20，上限 50）。`tab_offset` 默认 0，`tab_limit` 默认 50、范围 1–50，控制各窗口的标签概要分页。保留完整 `tab_ids` 和 `total_tabs`，每个窗口还返回 `has_more`、`next_offset`、`tab_snapshot`。超出字节预算返回 `result_too_large`，可缩小 `tab_limit` 或指定精确窗口，不返回部分关闭前置条件。 |
| `browser.windows.manage` | `create`（可选 HTTP(S) `url`）、`focus`（必需 `window_id`）、`close`（必需 `window_id` 和唯一的 `expected_tab_ids`）。拒绝无关字段，包括显式 null。 |
| `browser.navigation.go` | `navigate`（必需 HTTP(S) `url`）、`reload`、`back`、`forward`，均需显式 `tab`。执行前重新读取同一标签页，复核全部显式条件、ID、URL 和 pendingUrl；目标消失、身份/条件/URL 变化，或两次观察间 pendingUrl 增加、改变、消失时返回 `page_changed`。持续 loading、单纯 loading/complete 切换或稳定存在的 pendingUrl 均不阻止导航，不增加等待。返回 `status: "initiated"`、`navigation_waited: false`，不声称网页加载完成。 |
| `browser.navigation.status` | 通过现有显式 `tab` 查询存在性、当前/待提交 URL、`loading_status`。可传 `go` 返回的 `navigation_id` 复核该次尝试；省略时仅展示最近一次 ID，并发下不代表原调用。原生 ID 已关闭时返回 `exists: false`。不读取网页或触发操作。 |
| `browser.navigation.wait` | 精确 `tab_id` 跟踪同一标签；`condition` 为 `url`（必需 `url`）、`url_changed`（`url_baseline_id` 或精确 `baseline_url` 二选一）、`current_complete`（必需 `url`）、`navigation_complete`（必需 `url` 和 `navigation_id`）。`timeout_ms` 默认 5000、上限 8000，0 表示单次观察。返回 `matched/unmet`、原因和最终观察；不重新导航。 |
| `browser.groups.list` | 可选精确 `group_id`、`window_id`，同名不构成选择条件。返回原生 ID、标题、颜色、折叠状态、完整成员 ID 及有界脱敏概要。分组页用 `offset/limit/snapshot`，成员页用 `tab_offset/tab_limit/tab_snapshot`；成员续页必须精确 group_id。每页最多 50 项，集合/顺序变化拒绝旧快照。 |
| `browser.groups.manage` | `create`（必需唯一 `tab_ids`，可选目标 `window_id`、`title/color/collapsed`）、`update`（必需 `group_id` 和至少一种属性）、`move`（必需 `group_id/index`，可选目标 `window_id`）、`add_tabs/remove_tabs`（必需 `group_id/tab_ids`）。每次最多 50 个显式标签 ID，拒绝重复、无关字段及显式 null。 |
| `browser.bookmarks.query` | `roots` 发现根目录和浏览器文件夹；`get` 必需字符串 `bookmark_id`；`children/subtree` 必需字符串 `folder_id`；`search` 至少提供 `query/title/url` 一项，组合时 AND；`recent` 查询最近添加的书签。除 get 外支持 `offset/limit/snapshot`；subtree 可传 `depth`。 |
| `browser.bookmarks.manage` | `create` 必需 `kind: "bookmark"/"folder"`、`parent_id/title`，书签必需 URL、文件夹禁止 URL；`update` 必需 `bookmark_id` 及至少一种 `title/url`；`move` 必需 `bookmark_id/parent_id`；`remove` 必需 `bookmark_id`。create/move 可指定非负 `index`，省略表示末尾。 |
| `browser.bookmarks.preview_remove_tree` | 必需精确字符串 `folder_id`；`examples_limit` 默认 10、上限 20。完整统计子树，返回脱敏示例、`preview_id/expires_at`。只读，不删除、不证明已获得用户确认。 |
| `browser.bookmarks.remove_tree` | 必需同一 `folder_id/preview_id`；Agent 先在对话中说明预览影响并取得明确确认。重新核验完整快照、权限和期限后仅调用一次 removeTree，再查询验证不存在。 |

书签授权独立于网站和分组：必须在 Options 点击“授权书签访问”，本地 `bookmarkAccess` 与 Chrome 可选 `bookmarks` 权限同时有效才可执行四种 Capability。撤销先停用本地门禁，再请求 Chrome 撤权；MCP 不会申请权限，也不增加删除确认弹窗。
书签 ID 始终是原生字符串，同名节点通过 ID 区分。先 query roots 获取实际根/内置文件夹 ID，不写死特殊数字 ID。类型由 URL 属性是否存在确定；返回标题最多 512 字符、URL 最多 2048 字符，并提供父 ID、实际位置及时间信息。HTTP(S)、FTP、file、chrome、chrome-extension 和 about:blank 的显示 URL 复用安全路径过滤，移除凭据、查询和 Fragment；可执行/不支持的 URL 以 null 和 `url_omitted` 标记，已有书签仍可按 ID 查询及整理。显示标题和 URL 不能复制为原始精确搜索条件。
查询分页默认/上限 50 项，非零 offset 必须携带相同 action、筛选和深度下的 snapshot；成员、内容或顺序变化以及 worker 重启均返回 `bookmark_changed`，应丢弃旧页重新读取。快照采用内存随机密钥的 HMAC，不公开可猜测的 URL 哈希。subtree 为带 depth/parent_id 的先序平铺结构，默认深度 2、上限 5；`depth_limited` 和 `children_omitted` 明确未探索的文件夹，可按 ID 后续查询。单次遍历最多 10000 个节点、规范化快照最多 2 MiB，超限明确失败；total 只代表已声明查询深度的范围。响应仍受现有 40 KiB 结果预算限制，过大时减小 limit。
创建/修改 URL 支持经校验的绝对 HTTP(S)、FTP、file、chrome、chrome-extension 及 about:blank，拒绝凭据、空白、反斜线、控制字符和 javascript/data/blob 等可执行或数据地址；Chrome 原生 API 保留最终约束。仅改标题、移动或删除已有书签不套用写 URL 限制。文件夹不能设置 URL；普通 remove 仅接受单个书签或空文件夹，非空文件夹返回 `bookmark_not_empty`。移动核验原生返回与实际父节点/索引、目标同级节点顺序，接受同父级前移/后移等合法索引调整，不重复移动。
根、浏览器特殊文件夹和受管理节点不可直接修改；结合 `folderType`、`unmodifiable` 和根目录/祖先关系识别，兼容缺少 folderType 的旧版 Chrome。非受管理内置文件夹可接收子节点。文件夹移动和递归删除会完整检查子树，防止间接移动或删除受保护后代；无法完整核验就拒绝执行。
递归删除流程必须是 `preview_remove_tree → 告知用户准确文件夹和全部统计 → 用户明确确认 → remove_tree`。预览的 bookmark_count/subfolder_count 覆盖所有后代，total_nodes 还包含目标文件夹；示例可截断并用 has_more 声明。完整预览上限为 10000 节点、相对深度 64、2 MiB 规范化快照；缺失子树数据或超限不签发凭据。快照绑定原始标题/URL、ID、父 ID、索引、层级/顺序、创建/文件夹修改时间及保护/同步属性，不使用脱敏值比较。
预览凭据是随机、内存内的，最多 32 条、有效 120 秒；重启或淘汰后失效。执行前重新读取完整子树并比较快照，再检查授权和期限；变更需重新预览并重新取得确认。第一次原生删除尝试前使同一文件夹的所有旧凭据失效，API 报错也不恢复。凭据只证明快照匹配，扩展无法验证 ChatGPT 对话中的确认。Chrome 不提供原子比较并删除，最后核验后仍可能有并发改动；返回 `bookmark_outcome_unknown`、state_not_reached、超时或断线时先查询实际树，不自动重试、回滚或创建替代节点。保护规则参见 [Chrome Bookmarks API](https://developer.chrome.com/docs/extensions/reference/api/bookmarks)。

导航流程推荐 `navigation.go → navigation.status / navigation.wait → page.read / page.wait_for`。
`navigation.go` 保持只发起、不等待的语义，成功结果新增只读 `navigation_id`，标识这一次调用。后台从执行前开始观察原生标签事件；等待注册前发生的事件也可用于核验。后续应传原始 go 返回的 ID，不能用“最近一次”状态查询替换它，以免并发 go 接错调用。
`navigation_complete` 要求从该尝试观察到加载或 URL 转移，且最终精确目标 URL 报告 complete、没有 pendingUrl；新 URL 导航还必须观察到请求目的地，不能凭无关 URL 的 complete 推测重定向。旧页面仍是 complete、同 URL 已在加载却无法区分新旧轮次等情形返回未证明状态。
`current_complete` 仅说明指定当前 URL 的文档报告 complete，明确不证明刚发起的新导航完成；SPA 路由可用精确 `url` 或显式基准 `url_changed` 观察。Chrome complete 不保证 React/Vue 的业务内容就绪，业务元素继续使用 `page.wait_for`。
观察 URL 变化时使用 `navigation.status → 获取 url_baseline_id → navigation.wait({tab_id, condition: "url_changed", url_baseline_id})`。status 为已知 URL 返回绑定该标签和原始完整 URL 的不可预测随机 ID（URL 不可用/标签已关闭则为 null），敏感 URL 仅存于扩展内存；查询参数和 Fragment 的变化也会识别。基准最多保留 128 条、120 秒，不被等待续期或消耗；扩展重启、淘汰、过期、未知或跨标签使用返回 `url_baseline_unavailable`，必须重新观察，不回退到显示 URL。等待结果里的 `observation.url_baseline_id` 为 null，不创建新基准。`baseline_url` 继续支持调用者独立知道精确完整 URL 的情形，与 ID 互斥；不能将已脱敏的显示 URL 当成精确基准。
等待用标签事件积累证据并最多每 100ms 重新查询，单次上限 8 秒且受现有请求期限约束。标签关闭、标识丢失、被新 go 覆盖、观察到新的加载轮次或等待超时分别报告原因和最终状态；绝不自动重放导航。
观察记录只在扩展内存保留最多 128 条、120 秒，扩展重启/记录淘汰后不恢复。tabs API 没有文档级导航事务 ID；同一标签的人工导航如果与事件流不可区分，不提供跨并发操作的因果/原子保证。不明确的状态保持 unproven，未观察到请求目的地的重定向需重新观察后明确选择条件，不冒充已证明完成。

分组授权与网站授权独立：在扩展设置页点击“授权标签分组”，才能启用全部分组 Capability。仅有 Chrome 权限或仅有本地同意记录都不足以启用；拒绝/撤销后返回 `groups_permission_denied`。MCP 和网页内容不会申请权限。撤销先关闭内部授权门，即使 Chrome 权限移除失败也停用分组功能。
分组管理使用 Chrome 原生 API 处理固定、窗口、profile 和连续性限制，不自动取消固定、移动无关标签或重试。创建可先分组再修改属性；后一步失败时 `error.details.progress` 给出 `completed_steps`、已知 `group_id/tab_ids` 和 `partial/unknown`，必须先观察实际状态，不能自动回滚或重新执行。退出全部标签后空分组自然消失，没有独立删除 action。
`groups.manage(move)` 在操作前后读取分组和窗口标签布局，核验身份、目标窗口、完整成员顺序及连续性，并返回实际首成员索引 `actual_index`（其他 action 为 null）。末尾截断和固定/分组区域的合法边界调整可成功；本来已在有效位置也可成功。明确可移动但位置未改变、移动到错误位置或核验期间布局变化返回 `state_not_reached`；结果不确定时不再次移动。Chrome 不提供原子的核验并移动操作。

读取超过 50 个标签的窗口时，先用 `{"window_id": 7}` 查询第一页；若 `has_more: true`，再传同一 `window_id`、该窗口的 `next_offset` 作为 `tab_offset`，以及原样的 `tab_snapshot`。例如 70 个标签默认分 50 / 20 两页读取。
非零 `tab_offset` 必须同时提供精确窗口和快照；有快照时也必须提供精确窗口。`tab_offset == total_tabs` 返回空的末页，超过总数返回 `invalid_arguments`；末页 `has_more: false`、`next_offset: null`。
每页包含 `tab_offset`、`tab_limit`；`tabs_truncated` 表示这一页之前或之后仍有被省略的概要，不等同于 `has_more`。未指定 `window_id` 时保留多窗口首页概览，每个窗口分别提供完整 ID 列表和续页参数。
`tab_snapshot` 是窗口 ID 与有序标签页 ID 列表的 SHA-256，不保存服务端状态。续页发现集合或顺序变化时返回 `window_changed`，应丢弃已积累的页并从头读取，不能替换快照后继续拼接。它只标识观察到的集合/顺序，不冻结标题、URL 等元数据，也不能检测两次观察间已经恢复的短暂变化。

关闭整个窗口前，上层 Agent 必须在对话中向用户展示目标窗口及其完整标签页集合并取得确认，随后将该次观察的 `window_id` / `tab_ids` 作为关闭前置条件；不新增扩展确认 UI。
扩展在删除前重新读取窗口，集合顺序无关，但增加、删除、替换或移出标签页均返回 `window_changed`。此时应重新观察并重新取得确认，不能静默替换预期集合。
Chrome 没有原子的“比较集合并关闭”接口，最后核验与 Chrome 执行删除间仍可能有并发变化；关闭返回的是操作前摘要，可能受 beforeunload 等原生行为影响，应重新查询实际状态。
分页内容版本排除临时 ref：可读内容未变时仍可续读，并返回当前 ref；真正内容变化或导航仍使旧游标失效。
Core 配置和凭据也可通过 [GUI Browser 设置](../gui/pywebview/CONFIGURATION.md) 完成，无需手动创建 Token 文件。
不需要 Node 构建、前端框架或额外 Python 依赖，不随 Python 包或 EXE 自动安装。
Core 默认关闭浏览器接入；未安装扩展、未连接或断线均不影响原有 10 个 Tool。

## 安装与配对

1. 使用 **Chrome 120+**。从 [GitHub Releases](https://github.com/mmc-cloud/Chat2Local/releases) 下载
   `Chat2Local-v<version>-chrome-extension.zip`，解压到固定目录（也可直接使用源码仓库的 `browser-extension/`）。
   打开 `chrome://extensions` 的开发者模式，选择“加载已解压的扩展程序”，
   指向包含 `manifest.json` 的目录。复制 Chrome 显示的 32 位扩展 ID。
2. 在**运行目标 Agent 的本机**生成独立随机凭据（也可通过 Core Web UI 或桌面 GUI 管理 Token）。以下命令不要求克隆仓库，也不输出凭据，
   并以独占创建方式避免覆盖现有文件；文件已存在时应复用原配对，或自行备份后轮换。

   ```powershell
   python -c "import os,secrets; from pathlib import Path; p=Path.home()/'.chat2local'/'browser.token'; p.parent.mkdir(parents=True,exist_ok=True); fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600); os.write(fd,secrets.token_urlsafe(32).encode('ascii')); os.close(fd)"
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
5. 重启既有 Agent，例如 `chat2local agent --workspace 'D:\Projects\example'`（源码运行可用 `uv run chat2local agent`），
   Hub URL 和 Hub token 沿用原配置。也支持本机 standalone / Hub 模式，浏览器端点同样只在 loopback 上启动。
   扩展设置页状态应变为 `connected`。

启用配置但凭据不可读、格式错误、复用了 Hub token 或监听端口占用时，浏览器桥接会安全降级并记录固定警告，
Core / Hub / Agent 继续运行，能力显示不可用。修复后重启 Core。`enabled: false` 时不读取凭据、不绑定端口。
如切换 Chrome profile、移动未打包扩展目录导致 ID 变化，请重新确认并更新 `extension_id`。
一个本机端点只接纳一个已认证扩展连接；同机多实例应使用不同端口。

## 网站操作授权

在扩展选项页由用户主动点击“授权此网站”或“授权所有网站”；MCP 无法代替用户授予权限。
单站授权按协议和精确主机生效（涵盖全部路径与端口，不自动包含子域名）；全站授权覆盖普通 HTTP/HTTPS 网站，
但仍不支持 Chrome 内部页面、扩展页和商店等受限页面。即使已开启全站模式，也不意味着获得书签等独立的 Chrome API 权限。
执行 DOM 操作时会同时检查扩展保存的授权状态与 Chrome 当前权限；全站模式不提供单站排除。
用户可在选项页撤销全站授权或移除单站条目；撤销广域权限后，之前的单站记录可能需要重新向 Chrome 授权。
已有请求在权限变化时会复核授权，操作结果不保证能够回滚。

## MCP 调用

沿用既有设备选择规则：省略 `device` 指向 **MCP 服务所在设备**。远程浏览器必须显式传目标 Agent ID。
`list_devices` 返回的工具列表是设备支持的 MCP Tool 名称；浏览器的实时状态通过 `capability_discover` 查询。

```json
{
  "device": "laptop",
  "query": "browser",
  "detail": "summary"
}
```

将以上参数传给 `capability_discover`。也支持 `"query": "Chrome tab"` 等大小写不敏感的关键词，
多个词全部匹配；省略 query 返回全部已知能力。detail 默认 summary，每项仅含 name、description、available，
不返回完整 Schema、结果契约或公共错误说明。选中能力后，用同一 device 请求 full：

```json
{"device":"laptop","query":"browser.tabs.create","detail":"full"}
```

full 结果节选示例（Schema、共享指导和返回契约只展示部分字段）：

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
    }],
    "conventions": {"retry":"Observe actual state before retrying an uncertain action."},
    "error_guidance": {"stale_target":"Obtain a fresh ref by observing again."}
  }
}
```

扩展断开或浏览器接入关闭时，已知能力仍返回 `available: false`；未知查询返回 `capabilities: []`。
detail 只接受 summary/full；显式 null、其他字符串或类型均拒绝。full 的实际 Schema 还包含长度、模式和参数说明。仅接受绝对 `http://` 或 `https://` URL，
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
| `request_expired` | 请求在入口、准备或操作后超过期限；可能已有副作用，先观察实际状态，不能自动重试 |
| `browser_operation_failed` | Chrome 执行失败，返回固定诊断，避免泄露扩展错误内容 |
| `window_not_found` / `unsupported_window` | 精确窗口不存在，或窗口类型不是 normal |
| `window_changed` | 续页的标签页集合/顺序已变化，或关闭前完整标签页集合已变化。分页应丢弃旧页重新读取；关闭则重新观察并取得用户确认 |
| `history_unavailable` | 后退或前进方向没有可用历史记录 |
| `groups_permission_denied` | 在 Options 中由用户点击授予独立 tabGroups 权限；MCP 无法申请 |
| `group_not_found` / `group_changed` | 分组不存在，或目标/成员/页快照已经变化；重新观察，不能盲目重试 |
| `url_baseline_unavailable` | URL 基准未知、过期、已淘汰、扩展重启后丢失或属于其他标签；重新 status 获取基准，不使用显示 URL 替代 |
| `device_unavailable` | 设备离线、不支持新 Tool、Hub 请求超时等原设备路由错误 |
| `capability_error` | 目标设备工具分发失败 |

扩展错误码在 Core 的 `browser/errors.py` 中集中维护，包含通用错误及网页交互/状态等待错误。
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
  自动授予的广域 host 权限或远程脚本；书签与分组权限都是独立、手动授予的可选权限。网页操作使用 `tabs`、`scripting`，
  网站权限通过选项页用户点击按站或全站授予；仍保留 `storage`、`alarms` 和 `http://127.0.0.1/*` 本机 host 权限，CSP 将扩展自身网络连接限制为
  `ws://127.0.0.1:*`。本机 host 权限用于 Chromium 本地网络访问检查下的扩展接入；
  Chrome host match pattern 无法限定端口，因此端口由扩展设置控制。

Origin 与 token 保护来自普通网页的调用；同一 OS 用户下的恶意本地程序可读取用户凭据或伪造 Origin，
本机接入不是 OS sandbox。受信的本机 Agent 是扩展的命令来源。

## 自动化测试

在仓库根目录运行：

```powershell
uv run pytest -q tests/test_browser_capabilities.py tests/test_browser_phase2.py tests/test_browser_phase3.py tests/test_browser_phase4a.py tests/test_browser_phase4b.py tests/test_browser_phase4c.py tests/test_browser_contract.py tests/test_browser_bridge.py tests/test_browser_integration.py
npm ci --prefix browser-extension
npm test --prefix browser-extension
```

Python 测试无需 Chrome，覆盖参数、能力目录、真实 loopback WebSocket、Origin / token 拒绝、重复连接、
乱序响应、超时 / 取消、掉线重连、清理、可选监听失败、standalone / Hub 本机生命周期，
以及 **HTTP MCP → Hub → 两个真实 Agent → 模拟扩展** 的设备隔离集成调用。
Node 测试使用内置 `node:test` / `vm` 模拟 Chrome API 与 worker 环境，覆盖真实后台脚本的认证门禁、
标签页创建、无效命令、过期请求、同连接去重、API 错误、心跳、重连及 worker 恢复。
书签使用虚拟树和 Mock 原生 API，覆盖分页/深度/字节边界、保护节点、独立授权、完整预览统计、快照变化、凭据过期/消耗、并发删除和不确定结果不重试；不对真实用户书签执行自动化删除。
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

当前支持标签页管理、普通窗口管理、基础导航与有界状态等待、可选授权的标签分组及书签管理、网页读取、DOM 定位与点击/填写、双击、悬停、聚焦、选择、勾选、滚动、合成按键及元素状态等待；不包含历史记录、下载或截图。
一个端点对应一个 Chrome profile，没有浏览器 profile 路由或跨重启的请求去重。
超时、断开、Hub 重连或服务重启后**不要盲目重试**：Chrome 操作可能已发生，必须先人工检查实际标签页。
没有端到端 exactly-once 或页面加载完成保证。已有真实 Chrome 的代表性 MCP 调用链验收，
覆盖普通 HTML、React / Vue 等主要交互；不能据此保证所有第三方网站兼容。

合成双击、悬停和按键不等价于真实输入，不保证 CSS :hover、原生键盘默认动作或浏览器快捷键。
动作错误也可能发生在部分事件之后；观察页面实际状态后再决定是否继续，不能自动重放动作。
inspect/dom.inspect 现提供统一控件状态、ARIA 关联及滚动信息；inspect 的 select_options 支持原生选项有界分页。
set_checked 支持 ARIA checkbox/switch 与可靠关联 label 的隐藏原生 input；混合态单独核验，不反复点击或伪造状态。
自定义下拉框使用观察、click、wait_for、dom.inspect、选择和再观察的明确组合；select 仍仅支持原生控件。
actionable 增加只读几何/命中采样，不保证可信输入或业务成功。

实现依据：Chrome 官方 [WebSocket worker 生命周期说明](https://developer.chrome.com/docs/extensions/how-to/web-platform/websockets)、
[alarms API](https://developer.chrome.com/docs/extensions/reference/api/alarms)、
[tabs API 权限说明](https://developer.chrome.com/docs/extensions/reference/api/tabs)。
本机访问权限参考 Chromium 团队的 [扩展本地网络访问说明](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/pUDh8RiTjJk)。
