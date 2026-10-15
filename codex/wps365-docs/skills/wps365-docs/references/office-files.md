# 普通文件（.docx / .xlsx / .pptx）：下载 → 本地改 → 传回

> 本文件属于 `wps365-docs` 技能，配合 `{skillDir}/SKILL.md` 使用。

> 命令约定：下方命令是 POSIX 单行示例。PowerShell 中对带引号的可执行文件使用调用运算符 &，例如 & "{python}" ... 或 & "{cli}" ...；将脚本路径分隔符改为反斜杠，参数与冲突检查选项保持一致。
> API behavior and measurements below are inherited from the DSH 0.1.2 project notes; they are not new Codex cloud end-to-end verification.



这类文件**没有块级正文 API**，`.xlsx` 的单元格接口甚至写不了字面值（只能写公式）。所以唯一好用的姿势是：

> **`file_fetch.py` 下载原件 → 本地用已安装的 Codex Office 能力编辑 → `file_write.py` 原地传回**

file_write.py 以字节流上传，接口不限制文件内部结构；编辑工具是否保留所需格式，仍需按该工具的能力检查。实测（.xlsx）本地改了值 + 加行 + 加工作表后传回，原有的 `=B2*C2` / `=SUM(D2:D3)`、合并区 `A9:B9`、双表全部完好。

## 读取

```
"{python}" "{skillDir}/scripts/file_fetch.py" --file-id <file_id> --out "{tmp}/wps_<短名>" [--download] [--csv] [--link <分享链接>] --cli "{cli}"
```

| 产物 | 内容 |
|---|---|
| `<out>/doc.md` | `.docx` → 正文 Markdown（**含表格**）；`.xlsx` → 逐表 Markdown + **公式清单** |
| `<out>/meta.json` | name / version / size / link_url / parent_id |
| `<out>/original.<ext>` | 原件字节（`--download`；`.xlsx` 默认就下载） |
| `<out>/<sheet>.csv` | 每个工作表一份（`--csv`） |

`.xlsx` 默认走**本地 openpyxl 解析**（要用 `load_workspace_dependencies` 给的 Python 跑，才有 openpyxl）—— 这是**唯一能拿到公式原文**的方式。`--via-api` 改走 sheets 接口，不下载即可取值，但有两个固有缺陷：拿不到公式原文（`=B2*C2` 只回 `16.5`）、`cell_text` 是显示值会丢精度（`1234567890123` → `1.23457E+12`，脚本一律改用 `original_cell_value`）。

## 写入

替换前先取云端基线并下载本地备份。写回时使用这次读取的哈希和版本，并保留 --backup-dir；冲突时停止，除非用户明确授权覆盖。

    "{python}" "{skillDir}/scripts/file_write.py" --drive-id <盘> --file-id <file_id> --print-state --cli "{cli}"
    "{python}" "{skillDir}/scripts/file_fetch.py" --file-id <file_id> --out "{tmp}/office-backup" --download --cli "{cli}"
    "{python}" "{skillDir}/scripts/file_write.py" --drive-id <盘> --file-id <file_id> --file "{tmp}/edited.docx" --expect-hash <sha1> --expect-version <version> --backup-dir "{tmp}/bk" --cli "{cli}"

新建文件时用不会覆盖现有同名文件的默认失败策略：

    "{python}" "{skillDir}/scripts/file_write.py" --drive-id <盘> --parent-id 0 --md "{tmp}/内容.md" --name "新文档.docx" --on-name-conflict fail --cli "{cli}"

file_write.py 的 --dry-run 只申请上传、不 PUT 或提交文件；不要用它替代冲突检查或真实写入。

上传是**字节透传**，`.docx` / `.xlsx` / `.pptx` 通用（实测覆盖前两种）。

## 改 .xlsx：Codex 的推荐流程

在 Codex 中，先用 file_fetch.py 下载原件，再用已安装的 spreadsheets 能力编辑和重算，最后用 file_write.py 按原文件的基线传回。不要假设 Codex 有 DSH LibreOffice Kit、Node 运行时或它们的自动发现路径。

openpyxl 保存工作簿时会移除公式缓存值；当下游依赖已计算结果时，使用当前环境可用的 spreadsheet 能力或用户明确提供的 LibreOffice 重算工具重算，并在上传前检查公式和计算结果。xlsx_edit.py 是 DSH 附带的可选兼容脚本，不能作为 Codex 的默认能力。只有已明确找到依赖时，才通过 --node 或 --lo-cli 显式指定它；也可以用 --dry-run 做本地改动预览，再由 spreadsheets 技能重算。脚本缺少运行时或依赖时，应说明限制，不要猜测可执行文件路径，也不要自动安装。

若已确认本机有 Node 与 LibreOffice Kit，可显式运行下面的一条龙脚本；它会自动比较基线，但不会让 Codex 自动获得 DSH 运行时：

    "{python}" "{skillDir}/scripts/xlsx_edit.py" --drive-id <盘> --file-id <file_id> --set "数据!B2=99" --set "数据!D2==B2*C2" --node "<node executable>" --lo-cli "<LibreOffice CLI>" --backup-dir "{tmp}/bk" --cli "{cli}"

PowerShell 使用调用运算符，路径参数仍需加引号：

    & "{python}" "{skillDir}\scripts\xlsx_edit.py" --drive-id <盘> --file-id <file_id> --set "数据!B2=99" --set "数据!D2==B2*C2" --node "<node executable>" --lo-cli "<LibreOffice CLI>" --backup-dir "{tmp}\bk" --cli "{cli}"

--set 按内容推断类型：123 是数字、true 是布尔、以 = 开头的是公式，其余按字符串处理。公式值示例需写两个等号，使传入公式以一个等号开头。不要把本地 dry-run 或转换预览描述成云端写入验证。

## 底层路线与硬约束

读：`.docx` 走 `GET /v7/drives/{drive_id}/files/{file_id}/content?format=markdown&include_elements=all` → `data.markdown`；`.xlsx` 默认本地解析，或 `GET /v7/sheets/{file_id}/worksheets[/{sheet_id}/range_data]`
写：`POST .../request_upload` → `PUT` 实体 → `POST .../commit_upload`

1. **`hashes` 必填，且只能用 `md5` / `sha256`；`sha1` 会被拒。** 尽管 `enum_hash_type` 里列着 `sha1`，`request_upload` 一律回 `400000004 请求参数不支持`。
2. **别把下载接口给的哈希喂给上传接口**：`drive file download --with-hash` 只返回 **sha1**，直接拿去 `request_upload` 必然 400。这个 400 **与权限无关，别误判成"没权限 / 接口不可用"**（我为此白跑 8 次变体，还把错误结论写进过本文件）。
3. `upload_scene` 可选，不是必需。
4. **上传与当前版本完全相同的字节不产生新版本**（按内容哈希去重）—— 幂等重写不会污染版本历史。
5. **`include_elements=all` 是关键**：不传时（默认只含段落）**表格整块静默消失**、不报错；`format=plain` 同样丢表格。可选值 `para / table / component / textbox / all`。精装命令 `drive file-content get` **没暴露这个参数**，要带表格只能走 `api`。
6. 下载原件**必须带 delegated token**（脚本已处理），不带返回 `{"result":"userNotLogin"}`（与 .otl 附件相反，那些匿名可下）。
7. 两个 `api post` 与 PUT 必须同一身份；不显式传 `--token-type delegated` 时默认 app 身份会因缺 `kso.file.readwrite` 报 403。
8. `drive file download --with-hash` 的哈希**与下载字节一致（可信）**，可作回读校验（与 `attachment_list[].hash.sum` 正好相反）。
9. **回滚只能靠自己的本地备份**：spec 里有 `POST /v7/drives/{drive_id}/files/{file_id}/versions/{version_num}/revert` 与 `versions/{version_num}/download`，但**网关实测两条都是 `404 page not found`**（同族的 `GET .../versions` 与 `.../versions/{n}/meta` 正常返回 `code 0`，所以不是路径写错）。CLI 也只包了 `file-version list` / `get`，没有 revert 命令。**结论：改重要文件前一定加 `--backup-dir`**，要回滚就把备份字节用 `file_write.py --file 备份 --expect-version <当前版本>` 传回去 —— 实测可行（v1 备份传回后版本号自增到 v4，内容与 v1 逐字节一致）。
10. **`file_write.py --md` 生成 docx 的真实能力**（实测）：`#`/`##` → Heading 1/2 ✅、`- ` → `List Bullet` ✅、表格 → 真 Word 表格 ✅、`**加粗**` → run `bold=True` ✅；但 **`行内代码` 不带任何代码样式/等宽字体**、**`[文本](url)` 会以 `[文本](url)` 字面文本留在文档里**（不生成超链接）、**`>` 引用块落到 `Normal` 样式**（模板里有 `Intense Quote`，只是没用上）。需要这三种效果时，走"下载 → python-docx 改 → 传回"，或接受降级。

## .xlsx 的接口写入（仅作补充，不推荐当主路径）

`.xlsx` 确实有单元格写接口，但限制很大，只在"不想开文件、精确改几个格"时用：

- `POST /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data/batch_update`，body 的 `sheets_update_range_data` **只有 `cell_pic_info / formula / merge_type / op_type / xf` + 行列范围，没有任何字面值字段** —— 传 `cell_text` / `value` / `original_cell_value` 一律 `400000003`。**写值只能走公式**：数字用 `=999`，文本用 `="你好"`。
- 可用 `op_type`：`cell_operation_type_formula`（写公式）/ `_format`（格式，走 `xf`；颜色对象是 `{tint,type,value}`，**不是** rgb）/ `_merge`（合并）/ `_picture`。实测合并会落盘为 `<mergeCell ref="A22:C22"/>`。
- `POST /v7/sheets/{file_id}/worksheets` **可以新建工作表**：`{"name":"…","position":{"end":true}}`。（同一个路径下同时有 GET 和 POST，只看 GET 会误以为没有。）
- 其他可用端点：`worksheets/{id}/update`（改名/移动）、`worksheets/{id}/copy`、`worksheets/batch_delete`、`range_data/batch_delete`、`range_data/find`、`worksheets/{id}/rows`（**追加行**，不是插入）、`filters`、`data_validations`、`protection_ranges`。
- **确实没有**插入/删除行列的接口，也**没有修订痕迹（track changes）接口**。
- ⚠️ **API 写入后二进制是异步落盘的**：写入立刻体现在 API 读取（`active_area`、`worksheets` 列表）上，但 `drive file download` 拿到的字节要等**文件版本号自增**（实测 v1→v5，每次间隔 20–30 秒）。**写完不要立刻下载校验**，否则会误判成"没保存成功"。注意这个现象**不是每次必现**（2026-10-14 实测有一次上传后立刻下载就是新字节、sha1 与版本号都对），但别赌 —— 校验前先确认版本号自增了。

## 本地编辑的两个坑

1. **openpyxl 保存会丢掉公式的缓存值**（data_only=True 读出 None，已实测）。Excel/WPS 打开会重算，但下游若按缓存值读就会拿到空。一次 DSH 环境实测中，随附的 LibreOffice Kit 重算恢复了缓存值；Codex 不保证包含该 Kit。改完 .xlsx 后用当前可用的 spreadsheet 能力或用户明确提供的重算工具处理，再分别检查公式原文与计算结果，确认后才上传。
2. **WPS 生成的精简 docx 只有 `Normal` + `Heading 1-9`**，没有 `List Bullet`；`add_paragraph(style="List Bullet")` 会抛 `KeyError`。先探测文档实际样式，缺失时降级为手写项目符号（`file_write.py --md` 已内置该降级）。

## 相关能力

- Smart-document writes use otl_write.py, backed by the AirPage blocks convert/create endpoints.
- Ordinary Office reads and writes use file_fetch.py and file_write.py, with a saved local backup and explicit hash/version baseline.
- For .xlsx, use fetch, an available spreadsheet capability to edit and recalculate, and file_write.py with expected hash and version. Do not assume xlsx_edit.py has its DSH runtime.
- If only a sharing link is available, resolve /v7/links/{link_id}/meta first.
