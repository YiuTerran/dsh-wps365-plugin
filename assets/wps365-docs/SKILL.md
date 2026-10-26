---
name: wps365-docs
description: 读取或写入 WPS 365 云文档。智能文档（.otl / airpage）走 export_to_json + 附件下载读正文与内嵌图片、Markdown → blocks/convert → blocks/create 写入（**可逐段原地编辑**，写入前按块级哈希做局部并发比对）；普通文件（.docx / .xlsx / .pptx）走 file_fetch.py 下载原件 + file_write.py 三步原地传回，实现"下载 → 本地 Office 技能编辑 → 传回"，公式/格式/合并/多工作表全部保留；多维表（.dbt）走 dbsheet.py 的记录级读写、快照比对与附件下载。**修改既有内容前一律先做并发比对，被他人改过则放弃更新并提示冲突。** 支持从 kdocs/plus 分享链接读取他人共享的文档。当任务需要读取、写入、检索、总结、审查 WPS 365 云文档内容，或需要拿到文档/多维表里的图片时使用。
---

# 读取与写入 WPS 365 云文档

文末「本机资源」一节注入 `{skillDir}`、`{python}`、`{cli}`、`{tmp}` 的真实值；执行命令前先看那一节并替换占位符。

命令一律写成**单行**；路径含空格用双引号包住；**不要用 `&&` 或管道串联**，这样 bash / PowerShell / cmd 都能直接执行（平台见本机资源）。

## 授权：2 小时 token 会自动续期

- `wps365-cli` 的 access token 只有 2 小时，但 CLI 会在过期前 10 秒自动刷新，并在 401 时透明重试。refresh token 标称 **365 天**，刷新会轮换；撤销或保存失败仍可能提前失效，频繁重登不是正常的续期。
- 只有 refresh token 过期/被撤销、keyring 中的 token 丢失，或 CLI 回退到 app 身份后报 `required delegated scopes`，才需要重新登录。
- 设备码登录后 `refresh_token_expires_at` 可能是 `0001-01-01`（Go 零值，响应未带有效期），**不是过期**；`auth.py status` 显示“未知”，不会触发刷新或重登。
- 本目录脚本的 `run_cli` 已内置恢复：授权错误先 `auth refresh --delegated`，refresh token 被拒/缺失时 `auth login --device`（浏览器确认），成功后重试一次；存储、网络或服务端异常会停止并说明原因。直接跑 CLI 不经过这层封装。
- **权限：被拒后对同一命令升权**：DSH 不预授权。认证/云端调用（含 `ensure`、refresh、登录、分享链接、读取）先在当前沙盒运行；`ensure` 会在任何刷新前用 `stage=preflight` 探测凭据目录，失败是安全的。出现 `[sandbox: file access denied ...]`、`stage=preflight` 或凭据读写被拒时，对**同一条命令**重试一次，带 `sandbox_permissions`（最窄的更宽档；DSH 通常只有 `"danger-full-access"` 能覆盖凭据目录）和一句当前用户语言的 `justification`（说明要读写 WPS 凭据），由审批弹窗征得同意；不要绕过审批，也不要先登录再补权限。批准只覆盖该次执行，后续调用仍按会话权限运行。提权不可用或被拒时停止，提示需在本机终端认证；脚本不能自行提权。
- **硬性前置步骤**：首次云端 CLI 或文档脚本调用前（含分享链接），执行 `"{python}" "{skillDir}/scripts/auth.py" ensure --cli "{cli}"`，成功后再发业务请求；它因凭据权限失败时按上一条升权后重跑。用户明确要求 app 身份操作时保留该身份，不执行用户授权恢复。
- 直接调用遇到 token 失效、`required delegated scopes`，或 `delegated token unavailable, using app identity`（即使随后是 `403 invalid_scope`），执行 `"{python}" "{skillDir}/scripts/auth.py" ensure --force-refresh --cli "{cli}"`，成功后重试原命令一次。只有 `403 invalid_scope`、且确认正在使用用户身份时，可能是真正的 scope 限制，不反复刷新或登录。
- `status` / `status --json` **只检查、不刷新**；`has_refresh: true` 只说明存有 refresh token，不代表刷新成功。恢复未尝试时，不要停在状态说明、转浏览器或断言用户没有权限。
- **区分认证与凭据存储错误**：`credential_store_blocked` 或 `keyring.key` / 保存 token 时的 `operation not permitted`、`permission denied` 表示本地凭据读写被拒绝；脚本会停止，不能当成 token 过期而在相同权限下反复登录。按上面的升权方式重跑并读回 `auth status`；宿主不能提权时说明需在本机终端认证，也不要自动修改全局权限。服务端 refresh token 可能已轮换但本地保存失败；恢复权限后若报 `invalid_grant`，重新登录获取并保存新凭据。
- **可写探测仅作兜底**：`ensure` 和脚本业务入口用临时空文件检查 CLI 返回的 `config_dir`，不改凭据。`stage=preflight` 表示请求发出前已停止；该探测不能代替审批，也不保证系统钥匙串可写。缺少 `config_dir` 的旧 CLI 保留原行为（探测跳过，保存失败按上一条处理），`stage=status/refresh/login/business` 帮助区分实际失败阶段，不能仅凭一个分类断言根因。
- `refresh_grant_rejected`：服务端拒绝/找不到 refresh token，即使本地记录未过期也要重新登录。`auth_refresh_failed`：网络、CLI 启动/超时、服务端或响应格式异常，不能据此断言 token 过期；先解决错误并重试，不自动登录。诊断不得输出 token。
- 不要自行设置 `WPS365_NO_AUTO_LOGIN=1` / `WPS365_AUTO_LOGIN=0`；用户已配置就遵守：它只禁用登录弹窗，**不禁用 refresh**。报告时区分“已尝试刷新”“刷新后验证成功”“原请求已重试”。
- 不要把 `WPS365_ACCESS_TOKEN` 写进环境变量：它跳过存储和自动刷新，2 小时后必然失效。`WPS365_AUTH=app` 的 client_credentials 适合无人值守，但不能替代用户态文档权限。

本文件只放**必须立刻知道**的规则与命令骨架，细节按需读 `{skillDir}/references/`：

| 需要什么 | 读哪个文件 |
|---|---|
| 智能文档 `.otl` 的读取、写入、分享链接、17 条硬约束 | `references/otl.md` |
| 普通文件 `.docx` / `.xlsx` / `.pptx` 的下载 → 改 → 传回 | `references/office-files.md` |
| 多维表 `.dbt` 的记录读写、快照比对、附件与 15 条硬约束 | `references/dbsheet.md` |
| 写入前的并发比对、块级/整文件哈希、冲突判定与实测语义 | `references/concurrency.md` |

## 三类云文档，三条路线（别混用）

| 类型 | 读取 | 写入 |
|---|---|---|
| **智能文档 `.otl`**（airpage） | `otl_fetch.py` | `otl_write.py`（**可逐段原地编辑**） |
| **普通文件 `.docx` / `.xlsx` / `.pptx`** | `file_fetch.py` | `file_write.py` 三步原地传回 |
| **多维表 `.dbt`** | `dbsheet.py schema/list/get` | `dbsheet.py create/update/delete` |

三类写入语义根本不同：`.otl` 有块级 API，可以逐段增删改；`.docx` / `.xlsx` 没有（`.xlsx` 的单元格接口连字面值都写不了，只能写公式），所以后者的常规姿势是**下载到本地改、再整文件传回**；`.dbt` 是数据库，只能按记录读写，**不能抽正文、也不能当文件夹列子文件**。

⚠️ 例外必须说清：**有服务端内容模型的「在线表格」不采纳字节覆盖** —— 版本号与字节都会更新，读回却仍是旧值。`xlsx_edit.py` 上传后会读回 `--set` 写过的字面值单元格，不一致就**以退出码 1 明确失败**；真要改在线表格的内容，走 `references/office-files.md` 的单元格/公式接口，或让用户在线手动粘贴。

## ⚠️ 写入前必须比对：文件是否已被他人修改

**硬性前置步骤，不是可选优化。** 多人协作下，读与写之间可能已被他人改动。**发现被改过就放弃更新、并告知用户存在冲突**，由用户决定合并还是覆盖 —— 绝不静默覆盖别人的改动。原理与实测数据见 `references/concurrency.md`。

| 类型 | 比对方式 | 怎么做 |
|---|---|---|
| **`.otl`** | **块级局部比对** —— 只校验本次改动依赖的块 | ① `--list-blocks` 挑出要改的块；② `--snapshot-out {tmp}/snap.json --scope-index 0,1` 存快照；③ 写回带 `--expect-snapshot {tmp}/snap.json` |
| **`.docx` / `.xlsx` / 其他** | **整文件哈希**（`hash` sha1 + `version`，**不必下载**） | ① `file_write.py --print-state` 取基线；② 写回带 `--expect-hash … --expect-version …`；`xlsx_edit.py` 自动比对 |
| **`.dbt`** | **记录级快照**（字段值 + 工作表 schema 哈希） | ① `dbsheet.py snapshot --record … --out snap.json`；② 写回带 `--expect-snapshot snap.json`；③ 脚本写后读回校验 |

判定规则：声明的依赖块**被改/被删** → ❌ 放弃写入并报出是哪几个块；只有**无关块**变化 → ✅ 放行；未声明 scope → ⚠️ 全文任何变化都算冲突（`--allow-added` 可放行纯新增）；`--mode replace` → ❌ 无论是否声明 scope 都按全文比对；`--force` → 强制覆盖并打印告警。多维表没有记录级 version：更新只比本次要改的字段，删除比全部字段，schema 变过就要求重新快照。

⚠️ `.otl` 的盘级 `version` / `hash` 是**异步滞后**的（实测写完约 30 秒后才更新），**绝不能拿盘级 hash 检测 `.otl` 变更**，也不能写完立刻用它校验；块级比对才是实时的。

## 读取

```
# 智能文档 .otl → doc.md（正文 Markdown，含表格）+ img/（全部附件）
{python} {skillDir}/scripts/otl_fetch.py --file-id <file_id> --out {tmp}/otl_<短名>

# 普通文件 .docx/.xlsx/.pptx → doc.md / meta.json / original.<ext> / <sheet>.csv
{python} {skillDir}/scripts/file_fetch.py --file-id <file_id> --out {tmp}/wps_<短名> [--download] [--csv]

# 多维表 .dbt：先 schema，再分页读记录
{python} {skillDir}/scripts/dbsheet.py schema --file-id <file_id> --out {tmp}/dbt_schema.json
{python} {skillDir}/scripts/dbsheet.py list --file-id <file_id> --sheet <表名或ID> --max-records 50
```

- 长文档**别把正文打进 stdout**：默认只给清单，正文用 `read` 分页读 `<out>/doc.md`（`--print-text` 慎用）
- 图片按清单里的图号读：`read_image("<out>/img/0041_XXXX.png")`；先读 `doc.md` 判断哪张真要看
- **不要把导出响应原文读进上下文**：`.otl` 的 `data.content` 是 base64（某篇 5.2 万字符 ≈ 13k tokens），脚本已本地解码，只打印清单
- 只有分享链接时：先执行 `"{python}" "{skillDir}/scripts/auth.py" ensure --cli "{cli}"`，成功后再单独执行 `"{cli}" api get /v7/links/<link_id>/meta -o json` 换出 `file_id`
- `.xlsx` 默认走本地 openpyxl 解析（**唯一能拿到公式原文**的方式），要用本机资源那节给的 `{python}`
- `.dbt` 不是文件夹：`drive file list` 会返回空，`file-content` 报“文档内容抽取失败”；读结构和记录只能走 dbsheet 接口。Form 视图不承载记录，FlexPaper 工作表读不了正文/记录，具体见 `references/dbsheet.md`

## 写入

```
# 智能文档：Markdown → blocks/convert → blocks/create（--mode append 追加 / replace 清空后写 / prepend 插到最前；改重要文档前先加 --dry-run 看块结构）
{python} {skillDir}/scripts/otl_write.py --file-id <file_id> --md {tmp}/guide.md --mode replace

# 普通文件：原地替换（file_id 与分享链接不变，版本历史保留）
{python} {skillDir}/scripts/file_write.py --drive-id <盘> --file-id <file_id> --file 本地文件

# 云端 .xlsx 一条龙：下载 → 改 → 重算 → 传回，自动比对
{python} {skillDir}/scripts/xlsx_edit.py --drive-id <盘> --file-id <file_id> --set "数据!B2=99"

# 多维表：先快照，再部分更新，脚本写后读回
{python} {skillDir}/scripts/dbsheet.py snapshot --file-id <file_id> --sheet <表> --record <记录ID> --out {tmp}/snap.json
{python} {skillDir}/scripts/dbsheet.py update --file-id <file_id> --sheet <表> --record <记录ID> --set "字段=值" --expect-snapshot {tmp}/snap.json
```

四条硬规矩：

1. **先备份**：云文档没有本地撤销，写入前先用读取脚本把当前内容导出落盘
2. **写入超时 ≠ 写入失败**：`blocks/create`、`records/create` 都可能已在服务端提交。重试前必须先用读取脚本查块数/记录数，否则会写两遍
3. **`--mode replace` 是先删后插**，两步之间失败会让文档暂时变空 —— 这正是备份必须存在的原因
4. **多维表写入后必须读回**：选项/Link 非法值可能被接口**静默清空**，脚本会逐字段比对本 地期望值与读回值，不一致以退出码 1 失败

其他要点：单次写入上限 1000 块（脚本自动分批）；代码块语言只有 API 枚举内的才保留（写 `powershell` 会被**静默降级成纯文本**）；不要手拼块 JSON，转换器负责标题/表格/加粗/代码/列表；`.xlsx` 的接口写入后二进制**异步落盘**，写完不要立刻下载校验。

多维表另有几条只在这里强调：`record list` 默认只回 1 条，脚本固定 100 并翻页；`fields_value` 是 JSON **字符串**；Link 写入用记录 ID、过滤用显示名；Form 视图不承载记录。附件用 `dbsheet.py attachments --out <目录>` 下载，`--images-only` 只看图，服务器可能把图片转码，脚本按文件头改扩展名。完整命令与 15 条硬约束见 `references/dbsheet.md`。

## 编辑内容本身

- `.docx` / `.xlsx` / `.pptx` 的内容编辑：先 `file_fetch.py` 下载，再用 `office-docx` / `office-xlsx` / `office-pptx` 技能改，最后 `file_write.py` 传回
- 只想拿正文文本、不需要图：可用 `mcp__wps365__airpage_block_get`，但它**没有**图片 URL
