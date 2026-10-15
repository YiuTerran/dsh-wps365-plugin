# 普通文件（.docx / .xlsx / .pptx）：下载 → 本地改 → 传回

> 本文件属于 `wps365-docs` 技能，配合 `{skillDir}/SKILL.md` 使用。

这类文件**没有块级正文 API**，`.xlsx` 的单元格接口甚至写不了字面值（只能写公式）。所以唯一好用的姿势是：

> **`file_fetch.py` 下载原件 → 本地用 `office-docx` / `office-xlsx` / `office-pptx` 技能编辑 → `file_write.py` 原地传回**

好处是没有任何接口限制：公式、格式、合并单元格、多工作表、图片全部原样保留。实测（.xlsx）本地改了值 + 加行 + 加工作表后传回，原有的 `=B2*C2` / `=SUM(D2:D3)`、合并区 `A9:B9`、双表全部完好。

## 读取

```
{python} {skillDir}/scripts/file_fetch.py --file-id <file_id> --out {tmp}/wps_<短名> [--download] [--csv] [--link <分享链接>]
```

| 产物 | 内容 |
|---|---|
| `<out>/doc.md` | `.docx` → 正文 Markdown（**含表格**）；`.xlsx` → 逐表 Markdown + **公式清单** |
| `<out>/meta.json` | name / version / size / link_url / parent_id |
| `<out>/original.<ext>` | 原件字节（`--download`；`.xlsx` 默认就下载） |
| `<out>/<sheet>.csv` | 每个工作表一份（`--csv`） |

`.xlsx` 默认走**本地 openpyxl 解析**（要用 `load_workspace_dependencies` 给的 Python 跑，才有 openpyxl）—— 这是**唯一能拿到公式原文**的方式。`--via-api` 改走 sheets 接口，不下载即可取值，但有两个固有缺陷：拿不到公式原文（`=B2*C2` 只回 `16.5`）、`cell_text` 是显示值会丢精度（`1234567890123` → `1.23457E+12`，脚本一律改用 `original_cell_value`）。

## 写入

```
# 原地替换：file_id 与分享链接不变，版本历史保留（但 API 回滚不了，见下面第 9 条）
{python} {skillDir}/scripts/file_write.py --drive-id <盘> --file-id <file_id> --file 本地文件 [--backup-dir {tmp}/bk]

# 新建文件（--md 先生成 docx：标题/无序有序列表/表格/加粗可靠；引用、行内代码、链接会降级，见下）
{python} {skillDir}/scripts/file_write.py --drive-id <盘> --parent-id 0 --md 内容.md --name 新文档.docx

# 只做连通性测试：只申请上传，不 PUT 不提交，**不产生任何文件**
{python} {skillDir}/scripts/file_write.py --drive-id <盘> --file-id <file_id> --file x.docx --dry-run
```

上传是**字节透传**，`.docx` / `.xlsx` / `.pptx` 通用。

⚠️ 但"字节传上去了"不等于"云端内容改了"：**有服务端内容模型的在线表格不采纳字节覆盖** —— 版本号与字节都会更新，在线内容却仍是旧的。见硬约束第 10 条；`xlsx_edit.py` 现在会读回验证，遇到这种情况**以退出码 1 明确失败**，不再报成功。

## 改 .xlsx：一条龙（推荐）

`xlsx_edit.py` 把"下载 → 改 → 重算 → 传回"串成一步，并**自动补上 openpyxl 会抹掉的公式缓存值**：

```
# 直接改单元格（值以 = 开头即公式，注意是两个等号）
{python} {skillDir}/scripts/xlsx_edit.py --drive-id <盘> --file-id <file_id> --set "数据!B2=99" --set "数据!D2==B2*C2" --backup-dir {tmp}/bk

# 复杂改动用编辑脚本：以 wb / sheet() 为上下文执行，可给 changes 列表报改动
cat > edit.py <<'PY'
ws = sheet("数据")
ws["A14"] = "脚本写入"
wb.create_sheet("新表")["A1"] = "内容"
changes = ["数据!A14 = 脚本写入"]
PY
{python} {skillDir}/scripts/xlsx_edit.py --drive-id <盘> --file-id <file_id> --py edit.py

# 只改本地不传回
{python} {skillDir}/scripts/xlsx_edit.py --drive-id <盘> --file-id <file_id> --set "数据!A1=x" --dry-run
```

`--set` 按内容推断类型（`123`→数字、`true`→布尔、`=…`→公式、其余字符串）。重算依赖 LibreOffice kit，脚本会自动探测：先看应用资源目录（macOS 的 `.app` 与 Windows 的 `resources\` 都按同一组相对路径拼），再在 `DSH_HOME` 的 `dsh-runtimes/*` 与 `PATH` 里找；也可用 `--lo-cli` / `--node` 或环境变量 `DSH_LIBREOFFICE_CLI` / `DSH_NODE` 显式指定；**找不到时会跳过并明确告警**，不会静默丢缓存值。

实测一条龙（2026-10-14，本机）：新建的 xlsx 上传（v1，openpyxl 生成的公式**无缓存值**）→ `--set "数据!B2=99" --set "数据!D2==B2*C2*2" --set "说明!A2=脚本写入"` → 输出 `已重算：3 个公式，3 个有缓存值` → 上传 v2，回读得到 `=B2*C2*2 → 792`、`=SUM(D2:D3) → 804`，合并区 `A6:B6` 与 13 位大数字 `1234567890123` 均无损。**LibreOffice 重算在本机是可用的**（不是跳过路径）。

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
9. **回滚只能靠自己的本地备份**：spec 里有 `POST /v7/drives/{drive_id}/files/{file_id}/versions/{version_num}/revert` 与 `versions/{version_num}/download`，但**网关实测两条都是 `404 page not found`**（同族的 `GET .../versions` 与 `.../versions/{n}/meta` 正常返回 `code 0`，所以不是路径写错）。CLI 也只包了 `file-version list` / `get`，没有 revert 命令。**结论：改重要文件前一定加 `--backup-dir`**，要回滚就把备份字节用 `file_write.py --file 备份 --expect-version <当前版本>` 传回去 —— 实测可行（v1 备份传回后版本号自增到 v4，内容与 v1 逐字节一致）。2026-10-15 复测补充：列表接口**必须带 `page_size`**，否则 400 `请求参数不支持`（看起来像"没有这个接口"，其实只是漏了必填 query）；`versions/{n}/download` 仍 404；`revert` 在 CLI 里连 GET 操作都没有；`versions/{n}/meta` 在版本号不存在时回 403 `资源不存在`。**spec 里列着 ≠ 这个租户能用**。
10. **`file_write.py --md` 生成 docx 的真实能力**（实测）：`#`/`##` → Heading 1/2 ✅、`- ` → `List Bullet` ✅、表格 → 真 Word 表格 ✅、`**加粗**` → run `bold=True` ✅；但 **`行内代码` 不带任何代码样式/等宽字体**、**`[文本](url)` 会以 `[文本](url)` 字面文本留在文档里**（不生成超链接）、**`>` 引用块落到 `Normal` 样式**（模板里有 `Intense Quote`，只是没用上）。需要这三种效果时，走"下载 → python-docx 改 → 传回"，或接受降级。
11. ⚠️ **`.xlsx` 的字节覆盖对「在线表格」不生效**（2026-10-15 双方各自实测一致）：把 B1 从 `1` 改成 `42` 后按字节上传，`version` 1→2 且云端字节与本机一致，但 `GET /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data` **读回仍是 1**。区别在于这个文件有没有服务端内容模型：**纯字节上传**的文件模型就来自字节，改了就是改了（同一次实验里 B2=42 立刻读回一致）；被**在线编辑过**的文件（在网页里改过、或用 sheets 接口建过工作表）则以服务端模型为准，你的字节被存下来、内容却不采纳。所以 `xlsx_edit.py` 上传后会**读回自己用 `--set` 写过的字面值单元格**，不一致就打印"上传成功但云端没有采纳"并**以退出码 1 结束** —— 只信 `version` 自增会得到假成功（那位同学的原话：「version 增加了，看着像成功」）。`--py` 改动与公式改动无法用接口核对（接口不回公式原文），脚本会明确说跳过验证。另有报告称随后任何一次在线写入会按服务端模型重新序列化、覆盖上传的字节；我这次复现里没出现（版本号与字节都没变），按"取决于场景"看待。**要改在线表格的内容，走下一节的单元格/公式接口，或让用户在线手动粘贴。**

## .xlsx 的接口写入（对**在线表格**才是可靠路径；对纯字节文件仍推荐 `xlsx_edit.py`）

`.xlsx` 确实有单元格写接口，但限制很大，只在"不想开文件、精确改几个格"时用（另见第 11 条：在线表格的字节覆盖不被采纳）：

- `POST /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data/batch_update`，body 的 `sheets_update_range_data` **只有 `cell_pic_info / formula / merge_type / op_type / xf` + 行列范围，没有任何字面值字段** —— 传 `cell_text` / `value` / `original_cell_value` 一律 `400000003`。**写值只能走公式**：数字用 `=999`，文本用 `="你好"`。
- 可用 `op_type`：`cell_operation_type_formula`（写公式）/ `_format`（格式，走 `xf`；颜色对象是 `{tint,type,value}`，**不是** rgb）/ `_merge`（合并）/ `_picture`。实测合并会落盘为 `<mergeCell ref="A22:C22"/>`。
- `POST /v7/sheets/{file_id}/worksheets` **可以新建工作表**：`{"name":"…","position":{"end":true}}`。（同一个路径下同时有 GET 和 POST，只看 GET 会误以为没有。）
- 其他可用端点：`worksheets/{id}/update`（改名/移动）、`worksheets/{id}/copy`、`worksheets/batch_delete`、`range_data/batch_delete`、`range_data/find`、`worksheets/{id}/rows`（**追加行**，不是插入）、`filters`、`data_validations`、`protection_ranges`。
- **确实没有**插入/删除行列的接口，也**没有修订痕迹（track changes）接口**。
- ⚠️ **API 写入后二进制是异步落盘的**：写入立刻体现在 API 读取（`active_area`、`worksheets` 列表）上，但 `drive file download` 拿到的字节要等**文件版本号自增**（实测 v1→v5，每次间隔 20–30 秒）。**写完不要立刻下载校验**，否则会误判成"没保存成功"。注意这个现象**不是每次必现**（2026-10-14 实测有一次上传后立刻下载就是新字节、sha1 与版本号都对），但别赌 —— 校验前先确认版本号自增了。

## 本地编辑的两个坑

1. **`openpyxl` 保存会丢掉公式的缓存值**（`data_only=True` 读出 `None`，已实测）。Excel/WPS 打开会重算，但下游若按缓存值读就会拿到空。改完 `.xlsx` 建议用 LibreOffice kit 的 `recalculate` 重算一遍再上传（实测能重新写入缓存值）。`xlsx_edit.py` 会自己探测 kit（依次是 `--node` / `--lo-cli` 参数、`DSH_NODE` / `DSH_LIBREOFFICE_CLI` 环境变量、已知路径、`PATH` 里的 `node`），手工跑时用：
   ```
   node <libreofficeKit.node> <libreofficeKit.cli> recalculate --input 改完.xlsx --output 重算.xlsx
   ```
   再分别用 `data_only=False` / `True` 复读校验公式与计算值。
2. **WPS 生成的精简 docx 只有 `Normal` + `Heading 1-9`**，没有 `List Bullet`；`add_paragraph(style="List Bullet")` 会抛 `KeyError`。先探测文档实际样式，缺失时降级为手写项目符号（`file_write.py --md` 已内置该降级）。

## 相关能力

- 只想拿正文文本、不需要图：也可以用 `mcp__wps365__airpage_block_get`，但注意它**没有**图片 URL
- 智能文档写入：用上面的 `otl_write.py`（底层是 `/v7/airpage/v2/{file_id}/blocks/convert` + `blocks/create`）
- 普通文件读写：用 `file_fetch.py` / `file_write.py`（见上文）
- 改云端 `.xlsx`：优先 `xlsx_edit.py`（一条龙，含公式重算）
- 编辑 `.docx` / `.xlsx` / `.pptx` 的**内容**：先 `file_fetch.py` 下载，再用 `office-docx` / `office-xlsx` / `office-pptx` 技能改，最后 `file_write.py` 传回
- 只有分享链接、不知道 file_id 时，走 `/v7/links/{link_id}/meta`
