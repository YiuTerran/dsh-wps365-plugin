# Changelog

本文件记录面向使用者的变更；每个版本的细节也同步写在 `README.md` 的「更新记录」一节。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

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

[0.1.1]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.1
[0.1.2]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.2
[0.1.0]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.0
