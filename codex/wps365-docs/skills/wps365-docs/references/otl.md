# 智能文档（.otl / airpage）：读取、写入、分享链接、硬约束

> 本文件属于 `wps365-docs` 技能，配合 `{skillDir}/SKILL.md` 使用。

> 命令约定：下方命令是 POSIX 单行示例。PowerShell 中对带引号的可执行文件使用调用运算符 &，例如 & "{python}" ... 或 & "{cli}" ...；将脚本路径分隔符改为反斜杠，参数与冲突检查选项保持一致。
> API behavior and measurements below are inherited from the DSH 0.1.3 project notes; they are not new Codex cloud end-to-end verification.



## 为什么有这条专用路线

智能文档有三条读取路径，只有第三条适合 agent：

| 路径 | 问题 |
|---|---|
| `airpage block get` | 图片块**只有 id/宽高，没有 URL**，拿不到图 |
| `airpage export create --format docx` | 要建任务+轮询、下载约 350KB、unzip 翻 `word/media/`，图片只能靠 `image1.png` 顺序猜位置 |
| **`airpage export create --format json`** ✅ | **一次调用直接返回**，`attachment_list` 直接带图片 `download_url`，位置靠 `sourceKey` 精确对应，图片字节从 URL 直达磁盘、不经模型上下文 |

## 用法（读取）

```
"{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <智能文档 file_id> --out "{tmp}/otl_<短名>" --cli "{cli}"
```

脚本会产出：

| 文件 | 内容 |
|---|---|
| `<out>/doc.md` | **正文的 Markdown 还原**（标题 / 段落 / 列表编号 / 引用 / **表格** / 加粗 / 链接 / 图片引用）—— 优先读这个，可配合 `read` 的 offset/limit 分页 |
| `<out>/img/<图号>_<sourceKey>.png` | 下载好的全部附件（含**表格单元格内嵌**的图与 processon 图） |
| `<out>/otl.model.json` | 解码后的 OTL 原始模型，需要精确字段时才看 |
| stdout | **清单**：标题、节点统计、每个附件的图号/类型/尺寸/所在路径/落盘位置（不含 base64） |

常用开关：

- `--json <已有导出文件>`：复用一份已导出的 JSON，不重新调用 API（全流程可离线）
- `--no-images`：只要正文，不下载附件
- `--print-text`：把 `doc.md` 全文打到 stdout（**长文档慎用**；默认只给清单，让模型自己分页读文件）
Codex 命令一律使用资源解析步骤得到的绝对 {cli}，并在每个脚本命令上显式传 --cli "{cli}"。不要假设默认安装目录在 PATH 中，也不要依赖脚本的默认 CLI 搜索。

按需使用当前环境提供的图像查看工具打开清单中列出的本地图片路径。先读 doc.md 判断哪些图片与问题相关，避免把所有附件都读入上下文。

## 写入文档（Markdown → 块）

写入前先导出本地备份、保存当前块快照并预览转换。下面示例以整篇替换为例，传入快照后由脚本做全文冲突检查：

    "{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <智能文档 file_id> --out "{tmp}/otl-backup" --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <智能文档 file_id> --snapshot-out "{tmp}/snap.json" --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <智能文档 file_id> --md "{tmp}/guide.md" --mode replace --dry-run --cli "{cli}"
    "{python}" "{skillDir}/scripts/otl_write.py" --file-id <智能文档 file_id> --md "{tmp}/guide.md" --mode replace --expect-snapshot "{tmp}/snap.json" --cli "{cli}"

原理：`Markdown --blocks/convert--> 块 --blocks/create--> 文档`。`blocks/convert` 返回的块与 `blocks/create` 期望的 `doc_child` 是同一个 union，可直接透传，所以**不要手拼块 JSON** —— 标题层级、表格、`**加粗**`、`` `代码` ``、列表全由转换器处理。

已验证：62 块的 Markdown（含标题 13、表格 2、代码块 3、水平线 1）一次写入成功，回读内容与原文一致；`--mode replace` 会先按实际子块数删除再插入。

- `--dry-run`：只转换不写入，打印块结构摘要（**改重要文档前先跑这个**）
- `--text "<Markdown>"`：不开文件直接给文本
- 写入前务必先用读取脚本导出备份；单次写入上限 1000 块，超了脚本会自动分批
- **写入超时 ≠ 写入失败**：`blocks/create` 可能已在服务端提交，只是响应没回来。重试前必须先查块数（`otl_fetch.py` 的节点数，或 blocks 查询的 `data.block["doc"].children` 长度），**否则会把内容写两遍**
- **代码块语言只有 API 枚举内的才保留**：`bash / go / python / json / sql / yaml` 等 44 种（见规范的 `airpage_block_code_block_attrs_lang`）。写 `powershell` 这类枚举外的语言会被**静默降级成纯文本**，不报错
- **任务列表的勾选框会丢**：`- [ ]` / `- [x]` 经转换器后只剩列表项文本，`[ ]` 标记不保留，回读时还会变成有序列表项（实测 2026-10-14：写入 `- [ ] 任务列表项` / `- [x] 已完成项`，回读得到 `1. 任务列表项` / `2. 已完成项`）。要真正的勾选项就写符号字符（如 `☐` / `☑`）留在文本里
- **表头会被写成加粗**：表格首行单元格经转换后带 `bold`，回读时表头显示成 `| **项目** | **值** |`，属正常差异，不是你的 Markdown 多了粗体（实测同一次回读：源 3 处 `**…**` → 回读 6 处，多出的 3 处全在表头）

## 从分享链接读取（他人共享的文档）

分享链接如 `https://www.kdocs.cn/l/ce2ni9OvH6hj`，末段 `ce2ni9OvH6hj` 就是 `link_id`。先解析成 file_id，再走上面的脚本：

```
"{cli}" api get /v7/links/<link_id>/meta -o json
```

返回关键字段：`drive_id`、`file_id`、`created_by`（分享者）、`scope`（如 `anyone`）、`status`、`role_id`。拿到 `file_id` 后**直接**用脚本导出即可 —— 实测外部用户（`company_id: 0`）分享的 `anyone` 范围文档，用本企业 delegated token 可以直接读取，**不需要"接收文件"这类前置动作**。

注意：该接口的权限域是 `kso.file_link.readwrite`，CLI 没有把它包成 curated 命令，必须走 `api get`。

## 硬约束（踩过的坑）

1. **绝对不要把导出响应原文读进上下文**：`data.content` 是 base64，某篇 5.2 万字符 ≈ 13k tokens。脚本已本地解码，只打印清单。
2. **不要用 `attachment_list[].hash.sum` 校验下载结果**：声明的 sha1 与实际字节**不符**（实测 `03fd2e9d…` vs 实际 `1f7db144…`）。去重和校验一律用 `id` / `sourceKey`。
3. `download_url` 里的 object 路径**每次调用都会变**（临时签名），但指向同一内容；匿名 GET 即可下载，不需要额外鉴权头。
4. **附件数是"唯一 sourceKey 数"，不等于顶层 `picture` 节点数**：实测某文档 612 个节点里顶层 picture 71 个，但唯一附件 **77** 个 —— 差额是藏在 `tableCell` 里的图和 `processon` 节点。只扫顶层会静默漏图。
5. **表格必须专门渲染**：`table → tableRow → tableCell → paragraph`，一层都不能少；某篇 38 个表格如果只当未知类型跳过，正文信息量会掉一大半（实测 doc.md 从 87 KB 缩到 19 KB）。单元格里要用 `<br>` 表示换行、`\|` 转义管道符。
6. `data.picture[]` 是图片注册表，与正文节点重复，**不要重复计数**。
7. Codex 命令一律使用资源解析步骤得到的绝对 {cli}，并在每个脚本命令上显式传 --cli "{cli}"。不要假设默认安装目录在 PATH 中，也不要依赖脚本的默认 CLI 搜索。
8. 拿到的 `.md` 是**还原稿**，不是无损的 Markdown 原文；需要精确往返（改完再 `import_json_data` 导回）时，只能动 `otl.model.json`。
9. 图片会占用视觉上下文。这条路线省的是管道传输，不是看图成本。先读 doc.md 判断哪些图片真的需要看。
10. **块树查询的响应按 block_id 作键**：子块在 `data.block["doc"].children`，不是 `data.block.children`。按后者取会永远得到空列表 —— 于是 `--mode replace` 会"删除 0 个块"再插入，**静默退化成追加**，重复内容堆在文档里。
11. **写入前先备份**：云文档没有本地撤销。先跑读取脚本把当前内容导出落盘，改动重要文档前再用 `--dry-run` 确认块结构。
12. **CLI 有自己的 HTTP 超时**：调用 `api` 时必须显式传 `--timeout`（脚本内部已把 `--timeout` 转成 `180s` 一并传给 CLI）。默认超时下批量写入会在 `await headers` 阶段被客户端掐断，而服务端可能已经提交成功。
13. **删除成功了、写入超时了怎么办**：先查块数再决定是否重试。`--mode replace` 是"先删后插"，两步之间失败会让文档暂时变空 —— 这正是备份必须存在的原因。
14. `codeBlock` 的 `lang` 在导出模型里是**编辑器内部数字枚举**，与 `blocks` API 的字符串枚举编号不一致（25 是 bash，不是那个列表里的 matlab）。只有实测确认过的值才映射，其余按纯文本渲染并留注释。
15. **行内样式要按"区间"还原**：转换器会把一段加粗拆成多个元素，其中夹着代码片段。逐元素包 `**` 会产出 `**应用 ID → ****`client_id`**` 这种破碎标记；正确做法是先合并相邻同样式片段，再对加粗区间统一切换开闭状态，让代码片段嵌套在加粗内。
16. **`blocks/update` 的正文不在 `elements`**：字段是 `update_elements`，且它是**对象不是数组**，要按块类型再嵌一层 —— 段落是 `{"update_elements": {"paragraph_elements": {"elements": [...]}}}`。传 `elements` 报 `400000002 proto: syntax error … unexpected token [`，传成数组同样报错。
17. **`blocks/create` 的 `index` 是插入位置，不是追加锚点**（详见 `references/concurrency.md`）：并发新增不丢数据但顺序会颠倒。
