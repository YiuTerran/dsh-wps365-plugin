# Changelog

本文件记录面向使用者的变更；每个版本的细节也同步写在 `README.md` 的「更新记录」一节。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [0.2.3] - 2026-10-23

### 修复

- **直接调用 CLI 前必须检查并恢复用户授权。** Codex 与 DSH 指引统一要求先执行 `auth.py ensure`，共享链接元数据示例同步补上前置步骤。明确直接 CLI 调用不会经过脚本的自动恢复封装，`status` 只检查、不刷新，`has_refresh: true` 不代表刷新成功。
- **明确 app 回退后的处理顺序。** 遇到用户 token 失效或 `delegated token unavailable, using app identity` 后，即使随后返回 `403 invalid_scope`，也先恢复 delegated 身份、成功后重试原请求一次，再判断实际权限。明确凭据沙盒权限的处理，以及恢复尚未尝试时不能转向浏览器或断言用户没有权限。
- `auth.py ensure` 新增 `--force-refresh`，用于远端拒绝授权但本地 token 仍显示有效的场景；`status --force-refresh` 会报参数错误，不修改凭据。
- 澄清资源发现阶段不登录与认证恢复阶段允许设备码登录的边界；禁止 agent 自行设置禁用自动登录的环境变量。用户已有的 `WPS365_NO_AUTO_LOGIN=1` / `WPS365_AUTO_LOGIN=0` 仍只禁用登录弹窗，不阻止 refresh。
- 修复 GitHub Release 工作流的多行正文 YAML 缩进错误，使用环境变量和正文文件传递 release notes，并为已有 Release 检查配置认证。

### 验证

- 新增本地模拟回归：app 回退后 `403 invalid_scope` 在禁用登录时仍刷新并重试；命令入口强制刷新、本地有效凭据快速返回、只读状态、非法参数与恢复失败退出。测试不使用真实 WPS 凭据，不证明云端授权恢复成功。

## [0.2.2] - 2026-10-21

### 修复

- **把 `0001-01-01` 当作“未知”而不是“已过期”。** 刚完成 `auth login --device` 时，CLI 可能把 `refresh_token_expires_at` 返回为 Go 零值 `0001-01-01T00:00:00Z`（表示响应没有带有效期），但 refresh token 实际可用并会在 refresh 后显示一年后的日期。新增 `refresh_expiry()` / `refresh_expiry_label()`：缺失、零值和无法解析的值都视为未知，`_refresh_expired()` 返回 false，`ensure_auth()` 不会因此主动刷新或弹浏览器；`auth.py status` 把零值显示为“未知（登录响应未提供有效期，不代表已过期）”，不再显示 `0001-01-01`。

### 验证

- 用返回 `refresh_token_expires_at: "0001-01-01T00:00:00Z"` 的假 CLI 验证：`auth.py status` 显示“未知”，`auth.py ensure` 返回 0，且没有调用 refresh/login。
- `test/scripts_test.py` 增到 53 例，新增未知/空/畸形 refresh 过期时间不触发恢复的回归用例。

## [0.2.1] - 2026-10-21

### 新增

- **授权过期自动恢复**：新增 `assets/wps365-docs/scripts/auth.py`（`status` / `ensure`）。`_wps_cli.run_cli` 遇到 access token 过期、refresh token 失效、CLI 回退 app 身份导致的 `required delegated scopes` 等错误时，先运行 `auth refresh --delegated`；刷新失败则运行 `auth login --device`（CLI 自动打开浏览器请求授权），成功后重试原命令一次。`WPS365_NO_AUTO_LOGIN=1` 可禁用自动弹窗；`WPS365_ACCESS_TOKEN` 注入模式会明确提示无法自动恢复。
- **授权生命周期说明**：access token 有效期 2 小时，但 CLI 会在过期前 10 秒自动刷新、401 透明重试；delegated refresh token 有效期 365 天且每次刷新轮换，正常情况下设备码登录约一年一次。当前没有比 refresh token 更长的用户授权；`WPS365_AUTH=app` 的 client_credentials 适合无人值守但不能替代用户态文档权限。文档同时说明如何用 `auth status` / `auth refresh` / `auth.py` 排查“每 2 小时就要登录”。
- 版本号升到 `0.2.1`；`check-package.mjs` 的必需路径从 25 条增到 26 条（新增 `auth.py`）。

### 修复

- 业务请求已被远端拒绝授权时，强制尝试刷新；即使本地 token 仍显示未过期，也不会直接拿原凭据重试。刷新失败继续走设备码登录，主动 `auth.py ensure` 检查仍保留有效授权的快速返回。
- 显式应用身份同时识别 `WPS365_AUTH=app`、`--token-type app` 和 `--token-type=app`，避免无效地启动用户登录；命令行显式 `delegated` 优先于环境变量默认身份。

### 验证

- 用假 CLI 做端到端恢复验证：业务命令先返回 `access_token expired`，脚本识别后刷新失败、调用 `auth login --device`，再重试原命令成功；`WPS365_ACCESS_TOKEN` 注入和 `WPS365_NO_AUTO_LOGIN=1` 两条禁用路径也不会误弹浏览器。
- `test/scripts_test.py` 增到 52 例，覆盖授权错误识别、刷新后重试、刷新失败转设备码登录、refresh token 过期、权限错误不误判、关闭自动登录，以及本地未过期但远端拒绝授权、应用身份环境变量和命令行覆盖等回归场景。

## [0.2.0] - 2026-10-19

### 新增

- **多维表（`.dbt`）记录级读写**：新增 `assets/wps365-docs/scripts/dbsheet.py`，提供 `schema` / `list` / `get` / `create` / `update` / `delete` / `snapshot` / `attachments` 八个子命令。`list` 固定每页 100 条并翻页（接口默认只回 1 条），支持视图、字段选择、`--filter/--filter-file`、`md|cards|json|jsonl` 与 `--raw`；`create` 支持 `--set`、`--records-file`、查重与 `--dry-run`。
- **写入安全**：`update`/`delete` 必须先 `snapshot` 再传 `--expect-snapshot`；更新只比本次要改的字段，删除比全部字段，schema 变过要求重新快照。写后按 ID 读回，Link/MultipleSelect 比集合、Number 按数值、Date 按格式归一化、Checkbox 按布尔；不一致以退出码 1 报出“写入值 vs 读回值”。`--force` 只跳过冲突检查，不跳过读回。
- **字段校验与 Link 解析**：SingleSelect/MultipleSelect 先校验选项，非法值直接失败（接口本身会静默清空）；Link 写入把目标表主字段的显示名映射成记录 ID，重名/找不到失败，写后读回；`--set-json` 作为高级逃生口，适合清空选项或确知接口格式的复杂类型。
- **附件与图片**：从 raw 记录的 `uploadId` 调 `GET /v7/coop/dbsheet/{file_id}/attachments/{uploadId}` 换签名 URL，支持 `--field`、`--images-only`、`--list-only`、`--show-urls`、文件名清洗与去重。实测服务器会把 `.png` 转成 JPEG，脚本按文件头纠正扩展名并打印说明，避免只信后缀。
- **文档**：新增 `references/dbsheet.md`（15 条硬约束、命令、字段写入规则、附件、结构变更与常见错误）；`SKILL.md` 增加第三条路线、`.dbt` 读写命令、Form 视图与 FlexPaper 限制。Codex 侧同步新增英文 `references/dbsheet.md`，两份 manifest、构建脚本、打包清单与 README 同步到 0.2.0。
- 版本号升到 `0.2.0`；`package.json` 的 `test` 增加 `test/dbsheet_test.py`，`check-package.mjs` 的必需路径从 23 条增到 25 条。

### 修复

- 查重接口失败时停止创建；`--allow-duplicate` 仅放行已确认的重复记录，不能绕过查询错误。Link 查重将记录 ID 转成显示名再过滤。
- 批量更新拒绝重复记录 ID，避免后一项静默替换前一项字段；Checkbox 文本 `"0"`/`"false"` 正确显示为未勾选，并按布尔值比较。
- 日期字段格式含秒时，快照冲突检查和写后读回保留秒精度。

### 验证

- 2026-10-19 修复复测：在 `周报副本.dbt` 的两张临时表上验证创建/读回、Link ID 与显示名查重、实际 HTTP 400 查询错误阻止创建、重复批量 ID 拒绝、Checkbox 文本 `"0"` 显示为未勾选、秒级日期更新/读回/删除冲突，以及新快照允许正常删除。另以原周报表的 Contact 主字段关联做只读查重预演。临时表与记录已清理，原有 9 张表的完整 schema、1,994 条记录与测试前逐条一致。
- 在 `周报副本.dbt`（含表单、数据表、FlexPaper、附件）上实测：schema 读出 9 张表；`list` 分页、Form 视图回退、Link 显示名过滤、`get`、创建、快照、部分更新、冲突拒绝、`--force`、删除均按预期；附件 `rag系统架构图.png` 实际为 JPEG，脚本改名后字节完整，`xlsx`/`docx` 附件魔数正确。
- 测试用的临时表与反向 Link 字段已清理：删除 scratch 表后目标表仍残留反向字段（实测），已再用 `field delete` 删除，schema 恢复原状。这条“删源表可能留下反向字段”的坑写进了参考文档。
- 新增 `test/dbsheet_test.py` 48 例：schema/字段解析、filter 归一化、值转换与日期/选项校验、Link 解析、快照冲突、附件提取/文件名/魔数、分页、渲染、`--set-json` 与本地 `http.server` 下载，以及查重失败不写入、Link ID 查重、重复批量 ID、Checkbox 文本与秒级日期回归。`test/scripts_test.py` 增到 39 例（新增 dbsheet 加入共享 helper 契约、图片魔数测试）。

## [0.1.4] - 2026-10-16

### 修复

- 修正 `.xlsx` 写回读回校验：重复设置同一单元格时只校验最后一次赋值，布尔值、文本引号与空白按实际写入类型比较，清空单元格时正确接受稀疏空响应。

## [0.1.3] - 2026-10-15

### 修复

- **`.xlsx` 的字节覆盖对「在线表格」不生效 —— 脚本现在会读回验证，不再报假成功。** 同一手法对"已有服务端内容模型"的在线表格（在网页里改过、或用 sheets 接口建过工作表）实测：把 B1 从 `1` 改成 `42` 后按字节上传，`version` 1→2、云端字节与本机一致，但 `GET /v7/sheets/{file_id}/worksheets/{sheet_id}/range_data` **读回仍是 1**（等 10 分钟也不变）。也就是说版本号增加了、看着像成功，实际内容没变。`xlsx_edit.py` 因此在上传后**读回自己用 `--set` 写过的字面值单元格**：对不上就打印"上传成功但云端没有采纳"并**以退出码 1 结束**，同时说明原因与下一步。纯字节文件（模型就来自字节）不受影响，同一次实验里 `B2=42` 立刻读回一致；`--py` 改动与公式改动无法用接口核对（接口不回公式原文），脚本会明确说跳过验证。

### 文档

- 纠正 `references/office-files.md` 中"字节透传 `.docx` / `.xlsx` / `.pptx` 通用"的说法：新增硬约束第 11 条（在线表格不采纳字节覆盖，含 2026-10-15 两次独立实测数据、`xlsx_edit.py` 的读回验证行为、以及"随后在线写入会重新序列化覆盖"这条只在对方报告中出现、本次未复现的差异）；`.xlsx` 接口写入一节改标题说明它**对在线表格才是可靠路径**。
- 第 9 条补上 2026-10-15 复测结论：`versions` 列表**必须带 `page_size`**（否则 400 `请求参数不支持`，看起来像接口不存在）、`versions/{n}/download` 仍 404、`revert` 在 CLI 里连 GET 操作都没有、`versions/{n}/meta` 在版本号不存在时回 403 `资源不存在` —— spec 里列着不等于这个租户能用。
- `SKILL.md` 的"两类云文档"补上这个例外；Codex 侧同名的三份参考文档与 SKILL.md 同步更新（继承注记提升到 DSH 0.1.3）。

### 测试

- `test/scripts_test.py` 增 7 例（共 34 例）：`--set` 字面值提取（跳过公式与畸形项）、A1 → 0-based 坐标换算、读回值的宽容比较（`99` / `'99'` / `99.0`），以及四种云端响应的判定（读回旧值 → 报未采纳、已采纳 → 通过、工作表不存在 → 报未采纳、空区间 → 报未采纳）。

## [0.1.2] - 2026-10-15

### 新增

- **Windows 支持**。技能之前只在 macOS / Linux 上验证过，虽然它从不真的依赖 bash（脚本是纯 Python、每次只跑一条命令），但有几处 POSIX 假设会让 Windows 用户直接失败：
  - **CLI 发现**：新增 `src/cli-locate.js` 与 `assets/wps365-docs/scripts/_wps_cli.py`。Windows 上可执行文件是 `wps365-cli.exe`，用裸名 `statSync('…/wps365-cli')` 会误报"未找到"。现在按 `PATHEXT` 逐个拼写探测，并且会先在安装目录、再在 `PATH` 里查找（PowerShell 安装器会把 CLI 加进 `PATH`）。
  - **路径展开**：`~`、`~\`、`%NAME%`（Windows 拼写）、`${NAME}` / `$NAME` 都会被展开，且 `~\` 保持原生反斜杠；配置里可以写 `%USERPROFILE%\.local\bin\wps365-cli.exe`。
  - **技能正文改为占位符 + 单行命令**：`python3 …` → `{python} …`、`/tmp/…` → `{tmp}/…`、直接调 CLI 处 → `{cli}`，由插件在加载时注入本机真实值（解释器、临时目录、CLI 路径）；命令不再使用 `\` 续行，也不再标 `bash` 围栏，因此 bash / PowerShell / cmd 都能直接粘贴执行。注入块还会说明本机平台与引号规则。
  - 脚本侧同样平台化：`_wps_cli.py` 优先用 `shutil.which`（Windows 上自动尊重 `PATHEXT`），并支持 `WPS365_INSTALL_DIR` 覆盖安装目录。
- 测试新增 `test/cli-locate.test.mjs`：在 macOS 上覆盖 Windows 语义（扩展名探测、`PATHEXT`、`;` 分隔的 `PATH`、`%USERPROFILE%`、原生分隔符拼接），全部用注入的假文件系统，无需真机。

### 修复

- **中文 Windows 上 CLI 的输出会被整段丢掉**。五个脚本都用 `subprocess.run(..., text=True)` 读 CLI 输出，而 `text=True` 用的是*区域设置*编码：中文 Windows 上是 cp936，CLI 却永远写 UTF-8。于是读取线程在第一个非 ASCII 字节上抛 `UnicodeDecodeError`，调用方拿到的是 `stdout is None` —— 既不是数据、也不是错误信息（实测本机 9 条命令里 2 条整段丢失：`--help`、`drive --help`；凡响应里带中文文件名/错误文案的都会中招）。现在统一走 `_wps_cli.run_cli()`，固定 `encoding="utf-8", errors="replace"`，并把 `stdout` 兜底成空串，不会再出现 `None.strip()` 之类的次生崩溃。四个调 CLI 的脚本改为共用这一个封装，不再各自复制粘贴。
- **Windows 上有公式的 `.xlsx` 会静默跳过重算**。`xlsx_edit.py` 的 kit 探测只认识 macOS 的 `.app` 路径，`shutil.which("node")` 在 DSH 的 shell 里也找不到 node，于是 `find_kit()` 两个返回值都是 `None`，脚本按"找不到 kit"跳过重算 —— 上传的表格公式缓存值是空的，下游按值读会拿到空。现在按"应用资源目录 + 相对路径"在 macOS 与 Windows 两侧拼路径（`resources\` 下的 `app.asar.unpacked\...\libreoffice-kit` 与 `runtime\primary-runtime\dependencies\node` 都能命中），再退到 `DSH_HOME/dsh-runtimes/*`、`PATH`；Windows 上按 `PATHEXT` 拼 `node.exe`。实测本机 `find_kit()` 已能定位二者，`recalculate` 把 `=SUM(A1:A2)` 的缓存值算成了 5。

- **脚本自己打印的 ✅/⚠️/↳ 会在中文 Windows 上把脚本打死**。上一处是"读进来"的编码，这一处是"写出去"的：`sys.stdout` 在中文 Windows 上是 cp936，而五个脚本的汇总行里有 ✅、⚠️、↳、• 这类字符，`print()` 直接抛 `UnicodeEncodeError`。最坏的位置是**最后一行** —— 实测 `file_fetch.py` 已经把文件下载完、校验完、`doc.md` 也写好了，却在打印"✅ 一致"时崩掉并返回退出码 1，模型只会看到"失败"而不知道产物其实都在。现在每个脚本启动时调 `_wps_cli.use_utf8_stdio()`，把自身 stdout/stderr 固定成 UTF-8 + `errors="replace"`（仓库里 5 个脚本、共 13 个 cp936 编不出来的字符，逐个删字符治不了根）。
- **把 `.xls` 改名成 `.xlsx` 的文件会让 `file_fetch.py` 抛原始栈**。云盘上很常见（实测用户盘里就有一个 267 KB 的）：openpyxl 抛 `BadZipFile: File is not a zip file`，模型看到的是 openpyxl 的调用栈，既不知道原因也不知道下一步。现在按魔数（OLE2 `d0cf11e0a1b11ae1`）先判断，给出"名字是 .xlsx、内容是老式 .xls，请另存为 .xlsx；只想取值可加 `--via-api`"的提示。
- **发布产物不再可能夹带 Python 字节码**：跑过任一脚本后会在 `assets/wps365-docs/scripts/` 留下 `__pycache__`，而 `files` 白名单包含整个 `assets/`，所以从"刚跑过脚本"的工作树发布就会把它带上（曾发生过一次，52 KiB）。现在 `prepack` 会在打包前清理，`npm run check:pack` 也会在字节码从别的路径混进来时直接失败。
- **并发写入会检测一级块顺序变化**：全文替换与未声明依赖范围的检查会将块顺序变化按冲突处理，避免覆盖重排结果；显式依赖范围仍只检查依赖块的内容变化与删除，`--force` 仍可忽略冲突。
- **默认 CLI 配置会回退搜索 `PATH`**：未设置 `cli` 时，先检查安装目录，再查找 `PATH`；显式指定的路径仍按原值报告。
- **Excel 重算会正确发现 Node，且不再把 Python 当作 Node 启动**：在 DSH 运行时目录中按平台查找 `node` / `node.exe`；找不到 Node 时提示缺少重算工具并跳过重算。

### 测试

- 新增 `test/scripts_test.py`（27 例，纯标准库，`python3` 即可跑）：覆盖 `_wps_cli` 的默认路径拼写、`PATHEXT` 式裸名回退、显式路径不被悄悄替换；**管道必须按 UTF-8 解码**（含"输出解不出来时不能把 `None` 交给调用方"）；**子进程在 `PYTHONIOENCODING=gbk` 下打印 ✅ 不能失败**，且五个脚本都做了 stdio 引导；kit 探测用临时目录搭出 Windows 布局；OLE2 假 `.xlsx` 必须给出可照做的提示；并发检查覆盖块顺序变化与冲突处理。`npm test` 现在连它一起跑。
- 修掉两个在 Windows 上必然失败的断言（`ASSET_DIR` 只去掉 `/` 尾分隔符、期望 `/scripts/`）——它们是测试里的 POSIX 假设，不是实现缺陷；现在按本机实际分隔符断言，同时保留"不得出现重复分隔符"的检查。

## [0.1.1] - 2026-10-14

### 修复

- **一个会让 DeepSeek Harness 打不开的严重问题。** 0.1.0 的手写 Config 上带了一个 `toJSON()` 方法，而 DSH 的 settings 服务用 `'toJSON' in schema` 这个鸭子类型挑选要渲染的 schema，选中后紧接着读 `schema.meta.volatile`；手写对象不是 schemastery 图、没有 `meta`，于是抛 `TypeError`，`settings/describe` 失败，桌面端 welcome RPC 报 `desktop welcome: Web RPC failed`，窗口起不来。现在删掉了 `toJSON()`：settings 服务会跳过这个 entry，而 Cordis 仍通过 `~standard.validate` 校验行配置。
- 新增 `test/config-shape.test.mjs`，复刻 DSH 的鸭子类型判断，防止 `toJSON` / `meta` / `type` / `dict` / `inner` / `list` / schemastery 符号重新出现。

### 文档

- 修正 `references/office-files.md` 中与实测不符的说法：`file_write.py --md` 生成 docx 时行内代码没有样式、`[文本](url)` 会以字面文本留在文档里、引用块落到 `Normal` 样式；版本回滚不能用 API（`versions/{n}/download` 与 `revert` 实测 404），只能靠 `--backup-dir` 的本地副本；「上传后二进制异步落盘」是间歇性现象。
- 补充 `references/otl.md` 中实测到的转换器行为：任务列表勾选框会丢失并降级为有序列表项；表格表头会被写成加粗。

### 发布

- 发布到 npm 的包名是 **`@tryao/dsh-wps365-plugin`**（scoped）。最初按无 scope 的 `dsh-wps365-plugin` 发布时被 npm 的包名反垃圾过滤拦下（`403 Package name triggered spam detection`），改用账号自身的 scope 绕开。GitHub 仓库名仍然是 `dsh-wps365-plugin`，源码与文档里的 URL 不受影响。

## [0.1.0] - 2026-10-14

### 新增

- 首个版本：把 `wps365-docs` 技能打包成 DSH bundle —— 通过 `ctx.skills.registerProvider` 注册技能 provider，零 npm 依赖，脚本与参考资料随包分发。

### ⚠️ 请勿使用

- **该版本会让 Harness 无法启动**（原因见 0.1.1 的修复）。它从未发布到 npm；如果需要在配置里引用，请直接使用 0.1.1。

[0.2.2]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.2.2
[0.2.1]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.2.1
[0.2.0]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.2.0
[0.1.1]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.1
[0.1.2]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.2
[0.1.0]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.0
