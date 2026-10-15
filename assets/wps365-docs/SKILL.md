---
name: wps365-docs
description: 读取或写入 WPS 365 云文档。智能文档（.otl / airpage）走 export_to_json + 附件下载读正文与内嵌图片、Markdown → blocks/convert → blocks/create 写入（**可逐段原地编辑**，写入前按块级哈希做局部并发比对）；普通文件（.docx / .xlsx / .pptx）走 file_fetch.py 下载原件 + file_write.py 三步原地传回，实现"下载 → 本地 Office 技能编辑 → 传回"，公式/格式/合并/多工作表全部保留，写入前比对整文件哈希。**写入前一律先做并发比对，被他人改过则放弃更新并提示冲突。** 支持从 kdocs/plus 分享链接读取他人共享的文档。当任务需要读取、写入、检索、总结、审查 WPS 365 云文档内容，或需要拿到文档里的图片时使用。
---

# 读取与写入 WPS 365 云文档

`{skillDir}`、`{python}`、`{cli}`、`{tmp}` 四个占位符都由插件在文末「本机资源」一节注入真实值（资源目录、Python 解释器、wps365-cli 路径、临时目录）；执行命令前先看那一节，把占位符替换掉再跑。

命令一律写成**单行**、路径含空格用双引号包住，**不要用 `&&` 或管道串联** —— 这样 macOS / Linux 的 bash 与 Windows 的 PowerShell / cmd 都能直接执行（那一节还会告诉你本机是哪个平台）。

本文件只放**必须立刻知道**的规则与命令骨架，细节按需读 `{skillDir}/references/`：

| 需要什么 | 读哪个文件 |
|---|---|
| 智能文档 `.otl` 的读取、写入、分享链接、17 条硬约束 | `references/otl.md` |
| 普通文件 `.docx` / `.xlsx` / `.pptx` 的下载 → 改 → 传回 | `references/office-files.md` |
| 写入前的并发比对、块级/整文件哈希、冲突判定与实测语义 | `references/concurrency.md` |

## 两类云文档，两条路线（别混用）

| 类型 | 读取 | 写入 |
|---|---|---|
| **智能文档 `.otl`**（airpage） | `otl_fetch.py` | `otl_write.py`（**可逐段原地编辑**） |
| **普通文件 `.docx` / `.xlsx` / `.pptx`** | `file_fetch.py` | `file_write.py` 三步原地传回 |

两类写入语义根本不同：`.otl` 有块级 API，可以逐段增删改；`.docx` / `.xlsx` 没有（`.xlsx` 的单元格接口连字面值都写不了，只能写公式），所以后者的常规姿势是**下载到本地改、再整文件传回**，而不是找接口逐格/逐段写。

⚠️ 例外必须说清：**有服务端内容模型的「在线表格」不采纳字节覆盖** —— 版本号与字节都会更新，读回却仍是旧值。`xlsx_edit.py` 上传后会读回 `--set` 写过的字面值单元格，不一致就**以退出码 1 明确失败**；真要改在线表格的内容，走 `references/office-files.md` 的单元格/公式接口，或让用户在线手动粘贴。

## ⚠️ 写入前必须比对：文件是否已被他人修改

**硬性前置步骤，不是可选优化。** 云文档是多人协作的，从你读到内容到写回之间别人可能已经改过。**发现被改过就放弃更新、并告知用户存在冲突**，由用户决定合并还是覆盖 —— 绝不静默覆盖别人的改动。原理与实测数据见 `references/concurrency.md`。

| 类型 | 比对方式 | 怎么做 |
|---|---|---|
| **`.otl`** | **块级局部比对** —— 只校验本次改动依赖的块 | ① `--list-blocks` 挑出要改的块；② `--snapshot-out {tmp}/snap.json --scope-index 0,1` 存快照；③ 写回带 `--expect-snapshot {tmp}/snap.json` |
| **`.docx` / `.xlsx` / 其他** | **整文件哈希**（`hash` sha1 + `version`，**不必下载**） | ① `file_write.py --print-state` 取基线；② 写回带 `--expect-hash … --expect-version …`；`xlsx_edit.py` 自动比对 |

判定规则：声明的依赖块**被改/被删** → ❌ 放弃写入并报出是哪几个块；只有**无关块**变化 → ✅ 放行；未声明 scope → ⚠️ 全文任何变化都算冲突（`--allow-added` 可放行纯新增）；`--mode replace` → ❌ 无论是否声明 scope 都按全文比对；`--force` → 强制覆盖并打印告警。

⚠️ `.otl` 的盘级 `version` / `hash` 是**异步滞后**的（实测写完约 30 秒后才更新），**绝不能拿盘级 hash 检测 `.otl` 变更**，也不能写完立刻用它校验；块级比对才是实时的。

## 读取

```
# 智能文档 .otl → doc.md（正文 Markdown，含表格）+ img/（全部附件）
{python} {skillDir}/scripts/otl_fetch.py --file-id <file_id> --out {tmp}/otl_<短名>

# 普通文件 .docx/.xlsx/.pptx → doc.md / meta.json / original.<ext> / <sheet>.csv
{python} {skillDir}/scripts/file_fetch.py --file-id <file_id> --out {tmp}/wps_<短名> [--download] [--csv]
```

- 长文档**别把正文打进 stdout**：默认只给清单，正文用 `read` 分页读 `<out>/doc.md`（`--print-text` 慎用）
- 图片按清单里的图号读：`read_image("<out>/img/0041_XXXX.png")`；先读 `doc.md` 判断哪张真要看
- **不要把导出响应原文读进上下文**：`.otl` 的 `data.content` 是 base64（某篇 5.2 万字符 ≈ 13k tokens），脚本已本地解码，只打印清单
- 只有分享链接时：先 `{cli} api get /v7/links/<link_id>/meta -o json` 换出 `file_id`
- `.xlsx` 默认走本地 openpyxl 解析（**唯一能拿到公式原文**的方式），要用本机资源那节给的 `{python}`

## 写入

```
# 智能文档：Markdown → blocks/convert → blocks/create（--mode append 追加 / replace 清空后写 / prepend 插到最前；改重要文档前先加 --dry-run 看块结构）
{python} {skillDir}/scripts/otl_write.py --file-id <file_id> --md {tmp}/guide.md --mode replace

# 普通文件：原地替换（file_id 与分享链接不变，版本历史保留）
{python} {skillDir}/scripts/file_write.py --drive-id <盘> --file-id <file_id> --file 本地文件

# 云端 .xlsx 一条龙：下载 → 改 → 重算 → 传回，自动比对
{python} {skillDir}/scripts/xlsx_edit.py --drive-id <盘> --file-id <file_id> --set "数据!B2=99"
```

三条硬规矩：

1. **先备份**：云文档没有本地撤销，写入前先用读取脚本把当前内容导出落盘
2. **写入超时 ≠ 写入失败**：`blocks/create` 可能已在服务端提交。重试前必须先用读取脚本查块数，否则会把内容写两遍
3. **`--mode replace` 是先删后插**，两步之间失败会让文档暂时变空 —— 这正是备份必须存在的原因

其他要点：单次写入上限 1000 块（脚本自动分批）；代码块语言只有 API 枚举内的才保留（写 `powershell` 会被**静默降级成纯文本**）；不要手拼块 JSON，转换器负责标题/表格/加粗/代码/列表；`.xlsx` 的接口写入后二进制**异步落盘**，写完不要立刻下载校验。

## 编辑内容本身

- `.docx` / `.xlsx` / `.pptx` 的内容编辑：先 `file_fetch.py` 下载，再用 `office-docx` / `office-xlsx` / `office-pptx` 技能改，最后 `file_write.py` 传回
- 只想拿正文文本、不需要图：可用 `mcp__wps365__airpage_block_get`，但它**没有**图片 URL
