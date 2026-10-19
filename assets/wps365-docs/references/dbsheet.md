# 多维表（.dbt）

`wps365-docs` 的第三条路线：**多维表是数据库，不是文档，也不是文件夹**。正文/记录走 dbsheet 接口，由 `{skillDir}/scripts/dbsheet.py` 封装；建表、改字段这类结构变更不在脚本里，直接用 `{cli} dbsheet ...`。

| 类型 | 读 | 写 |
|---|---|---|
| `.otl` 智能文档 | `otl_fetch.py` | `otl_write.py`（块级） |
| `.docx/.xlsx/.pptx` | `file_fetch.py` | `file_write.py`（整文件传回） |
| `.dbt` 多维表 | `dbsheet.py schema/list/get` | `dbsheet.py create/update/delete` |

## 15 条硬约束（按实测，v0.3.6）

1. **`.dbt` 不是文件夹，也不能抽正文**：`drive file list <drive> <file_id>` 返回空；`drive file-content get` 报 `400008018 文档内容抽取失败`。读结构只能 `GET /v7/coop/dbsheet/{file_id}/schema`。
2. **`record list` 默认 `page_size=1`**：不传 `--page-size` 只回 1 条。脚本固定 100 并循环 `page_token`；手写请求也要显式翻页，别把“只有一条”当成数据少。
3. **`fields_value` 是 JSON 字符串**：创建/更新的 `records[].fields_value` 要传 `"{\"字段ID\":...}"` 这样的字符串，不是对象；响应里的 `fields` 同样是字符串，先 `json.loads`。
4. **`prefer_id=true` 时字段用 ID，Link 过滤却用显示名**：记录 key、`filter.criteria[].field` 都是字段 ID；但 Link 字段的 `values` 必须写目标记录的**显示名**，写记录 ID 会得到空结果。
5. **`text_value=text` 与 raw 是两种视图**：text 模式 SingleSelect 给选项名、MultipleSelect 给逗号串、Number 给 `"42.00"`、Checkbox 给 `"1"`、Link 给 `"陈澍,芦宇轩"`、Attachment 只剩文件名；**附件 `uploadId` 只在 raw 里**。要写回或下载附件就读 raw。
6. **Form 视图不承载记录列表**：表单视图 `records_count=0`，`list --view <表单>` 返回空。读底层数据表；表单字段顺序/隐藏状态在 `views[].fields_attribute` 的 `{field, hidden}` 里。
7. **FlexPaper 工作表读不了**：`sheet_type=xlEtFlexPaperSheet` 只有 `content_id`，没有公开的内容接口，`fields` 为空、也没有记录。schema 里照实说“不可读”，不要编造正文。
8. **附件下载要 `uploadId` + 接口换 URL**：`GET /v7/coop/dbsheet/{file_id}/attachments/{uploadId}?type=<mime或扩展名>`，URL 在 `data.attachment.value.download_url`，大小在 `value.file_size`。不要拿 `fileName` 拼 URL。
9. **创建非幂等，批量全成全败**：`records/create` 超时/网络错误时可能已提交；**不要直接重试**，先用 filter/list 查是否已创建。一批里任意一条失败，整批都不生效。
10. **没有记录级乐观锁**：接口没有单条 version/etag。并发安全只能“先 `snapshot` 存基线 → 写时 `--expect-snapshot` 比对 → 写后读回”。`record update` 是**部分更新**（只传的字段变），`batch_delete` 必须比全部字段。
11. **这些字段不能手写**：Attachment、Formula、Lookup、AutoNumber、CreatedTime/By、LastModifiedTime/By、OneWayLink、Contact 都是只读；Cascade/Location 结构复杂，脚本直接拒绝，确知格式才用 `--set-json`。
12. **Link 写入用记录 ID，不是显示名**：写 `["陈澍"]` 不会报错，而是**静默清空**该字段。脚本先把显示名映射成目标表主字段的记录 ID，再写、再读回；重名/找不到会直接失败。过滤时反过来用显示名。
13. **Date 先校验格式**：接受 `YYYY/MM/DD`、`YYYY-MM-DD`（可带 `HH:MM[:SS]`）；非法值接口返回 `400000003 ... field: xx`。字段格式只有日期时会丢掉时间部分，读回按字段格式归一化。
14. **单请求最多 1024 条，限流约 10 次/秒**：脚本单批上限 100 条并分页读；大批量写入自己分批、留间隔，别并发轰接口。
15. **附件只能下载，不能上传**：没有公开的附件上传接口；`Attachment` 字段也不能用 `--set`/`--set-json` 直接写。要新增附件请在 WPS 界面操作，脚本只负责把已有附件拉下来。

## 读：先 schema，再记录

```
{python} {skillDir}/scripts/dbsheet.py schema --file-id <file_id> --out {tmp}/dbt_schema.json
{python} {skillDir}/scripts/dbsheet.py list --file-id <file_id> --sheet 12 --view 周报明细 --fields "汇报日期,姓名,组别" --max-records 20
{python} {skillDir}/scripts/dbsheet.py get --file-id <file_id> --sheet 12 --record <record_id>
```

- `schema` 打印每张表的类型、记录数、主字段、视图、字段类型/选项/Link 目标；`--out` 存完整 JSON。**先看 `sheet_type`**：FlexPaper 直接放弃读记录。
- `list` 默认 50 条、每页 100；`--max-records 0` 读全部。`--format md|cards|json|jsonl`，`--raw` 保留原始值（Link 是 ID）。
- 过滤条件：`--filter '{"mode":"And","criteria":[{"field":"项目名称","operator":"Intersected","values":["引隆补水工程信息化项目"]}]}'`，字段可写显示名（脚本转 ID），Link 的 `values` 写显示名。`--filter-file` 读文件，`-` 读 stdin。
- Form 视图会打印警告并自动改读底层表；表单字段顺序看 `schema` 输出里的「表单 … 可见字段」。
- 长文本默认截断；要全文用 `--no-truncate` 或 `--format json` + `--out`。

## 写：快照 → 更新 → 读回

```
# 1. 先存基线（记录 ID 或过滤条件二选一）
{python} {skillDir}/scripts/dbsheet.py snapshot --file-id <file_id> --sheet 12 --record <record_id> --out {tmp}/snap.json

# 2. 部分更新；只比本次要改的字段
{python} {skillDir}/scripts/dbsheet.py update --file-id <file_id> --sheet 12 --record <record_id> --set "备注=已电话确认" --expect-snapshot {tmp}/snap.json

# 3. 删除；比全部字段
{python} {skillDir}/scripts/dbsheet.py delete --file-id <file_id> --sheet 12 --record <record_id> --expect-snapshot {tmp}/snap.json
```

- `create`：`--set "姓名=陈澍"` 可重复；批量用 `--records-file records.json`（数组，元素含 `fields`）。写前 `--check-duplicate "姓名,汇报日期"` 自动查重，命中默认放弃，`--allow-duplicate` 放行。**创建非幂等**，超时先查再决定。
- 查重查询失败会停止创建，`--allow-duplicate` 也不能绕过接口错误；Link 查重会把记录 ID 转为显示名再过滤。批量更新文件中的记录 ID 必须唯一，重复 ID 会直接拒绝。
- `update` / `delete` 不给 `--expect-snapshot` 会直接拒绝；确认覆盖才加 `--force`。`--force` 只跳过冲突检查，不跳过读回校验。
- 写后脚本会按 ID 读回，逐字段比对；Link/MultipleSelect 比集合，Number 按数值，Date 按格式归一化，Checkbox 按布尔。不一致以退出码 1 报出“写入值 vs 读回值”。
- 日期字段格式包含秒时，快照和读回比较会保留秒；只有分钟精度的字段才忽略秒。Checkbox 的文本 `"0"`/`"false"` 按未勾选处理，不按非空字符串判断。
- `--set-json '状态=""'` 是高级逃生口：值原样发，适合清空选项或确知接口格式的复杂类型；普通写入优先 `--set`，它会做类型校验和 Link 解析。
- `--dry-run` 只打印请求体，不发送；创建/更新的预演也会做字段校验和 Link 解析。

## 字段写入规则

| 类型 | 写法 |
|---|---|
| 文本 / 电话 / 邮箱 / URL | 字符串 |
| Number / Currency / Percentage / Rating | 数字；`Percentage` 支持 `"12%"`（转 0.12）；不接受 NaN/Infinity |
| Checkbox | `true/false`、`1/0`、`是/否` |
| Date | `YYYY/MM/DD` 或 `YYYY-MM-DD`，可带 `HH:MM[:SS]` |
| SingleSelect | 选项**显示名**（也接受选项 ID）；不在选项里直接失败，不会静默清空 |
| MultipleSelect | 数组或逗号串；逐项校验选项 |
| Link | 目标表主字段的**显示名**（脚本转记录 ID），或记录 ID；重名/找不到失败 |
| Attachment / Formula / Lookup / … | 只读，拒绝写入 |
| Cascade / Location / 未知类型 | 默认拒绝；确知格式用 `--set-json` |

Link 的 `values` 在过滤条件里是显示名，在写入时是记录 ID —— 这是两套语义，别互相套用。

## 附件与图片

```
# 先列元信息，不下载
{python} {skillDir}/scripts/dbsheet.py attachments --file-id <file_id> --sheet 12 --record <record_id> --out {tmp}/dbt_att --list-only

# 只下载图片；下载后按内容判断真实格式
{python} {skillDir}/scripts/dbsheet.py attachments --file-id <file_id> --sheet 12 --filter '...' --out {tmp}/dbt_att --images-only
```

- 附件字段必须是 raw 记录；`--field 相关附件` 可限定字段，`--images-only` 只看图片。
- 文件名会去掉路径/非法字符；同一记录同一 `uploadId` 只下一次。下载后读文件头：服务器可能把 PNG 转成 JPEG（实测 `.png` 实际是 JPEG），脚本会按真实内容改扩展名并打印说明，**不要只看 `fileName` 后缀**。
- 清单里的 `read_image("<路径>")` 只在当前模型支持图片输入时可用；不支持就改用文件大小/尺寸/格式等客观信息，别假装看过图。
- `--show-urls` 会打印带签名的下载 URL，别把它写进公开文档。

## 结构变更（不在脚本里）

```
{cli} dbsheet schema get <file_id> -o json
{cli} dbsheet sheet create <file_id> --name "新表" --fields-body '[...]' --views-body '[...]'
{cli} dbsheet field create <file_id> <sheet_id> --fields-body '[...]'
{cli} dbsheet field delete <file_id> <sheet_id> --fields "Dr"
```

建 Link 字段会**同时**在目标表生成反向关联字段；删掉源表/源字段后，反向字段可能残留（实测删掉 scratch 表后目标表仍留着 `Dr __dbsheet_probe__`），要再 `field delete` 清掉。结构变更前先 `schema get` 备份，改完再核对。

## 常见错误

| 现象 | 原因 / 处理 |
|---|---|
| `文档内容抽取失败` | 把 `.dbt` 当普通文件；改用 dbsheet schema/records |
| 列表只有 1 条 | 没传 `page_size`；传 100 并翻页 |
| 写入成功但字段空了 | SingleSelect/MultipleSelect/Link 值非法，接口静默清空；先校验、写后读回 |
| Link 过滤为空 | `values` 写成了记录 ID；改成显示名 |
| Link 写入后为空 | 写的是显示名；改成记录 ID（脚本会转） |
| `field: xx` 400 | Date 等类型值非法；先按格式校验 |
| 表单视图没有记录 | Form 视图不承载记录；读底层表 |
| FlexPaper 表读不到 | 文档型工作表无公开内容接口；明确告知用户 |
| 删除/更新被拒 | 快照与当前不一致；重新快照、合并改动，确认覆盖才 `--force` |
