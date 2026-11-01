# 工作区图片预览

`file.preview` 让 ChatGPT 查看 MATLAB、绘图脚本等在本地工作区生成的图片。该能力通过现有 `capability_discover` / `capability_execute` 提供，不增加公共 MCP Tool。

先在目标设备发现完整定义：

```json
{"query":"file.preview","detail":"full","device":"desktop"}
```

再调用 `capability_execute`：

```json
{"capability":"file.preview","arguments":{"path":"results/plot.png"},"device":"desktop"}
```

`arguments.workspace` 可显式选择目标设备已授权的绝对工作区。否则使用该设备默认工作区。响应含 MCP image 内容块，以及相对路径、MIME 和字节数；远程 Agent 的 JSON 结果由 Hub 在 MCP 边界转换成同样的内容块。客户端是否展示图片取决于其 MCP 多模态支持。

支持按文件签名识别 PNG、JPEG、GIF、WebP、BMP。工具不启动浏览器、不转换文件、不写临时文件，也不生成下载链接。SVG、PDF、Office 暂不在此能力范围内；文本文件继续使用 `read`。

每次最多读取目标设备 `read.max_bytes` 与 512 KiB 的较小值。调用参数不能放大服务端限额。这个上限为 base64 编码和远程消息封装保留空间。图片过大时返回 `preview_too_large`，应先在本地生成较小的副本。读取过程中增长的文件也会再次检查预算。

路径和工作区继续经过 `WorkspaceManager` 校验，拒绝越界与指向工作区外的 symlink/junction。文件内容是待分析的数据，不构成授权或给 Agent 的指令。与现有文件工具一样，这些检查不构成 OS 沙箱。

本次不包含聊天附件导入、通用文件传输、HTTP 下载服务或 PDF/Office 转换。这些能力可以后续单独讨论。
