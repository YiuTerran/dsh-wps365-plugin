# Changelog

本文件记录面向使用者的变更；每个版本的细节也同步写在 `README.md` 的「更新记录」一节。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [未发布]

### 新增

- **Windows 支持**。技能之前只在 macOS / Linux 上验证过，虽然它从不真的依赖 bash（脚本是纯 Python、每次只跑一条命令），但有几处 POSIX 假设会让 Windows 用户直接失败：
  - **CLI 发现**：新增 `src/cli-locate.js` 与 `assets/wps365-docs/scripts/_wps_cli.py`。Windows 上可执行文件是 `wps365-cli.exe`，用裸名 `statSync('…/wps365-cli')` 会误报"未找到"。现在按 `PATHEXT` 逐个拼写探测，并且会先在安装目录、再在 `PATH` 里查找（PowerShell 安装器会把 CLI 加进 `PATH`）。
  - **路径展开**：`~`、`~\`、`%NAME%`（Windows 拼写）、`${NAME}` / `$NAME` 都会被展开，且 `~\` 保持原生反斜杠；配置里可以写 `%USERPROFILE%\.local\bin\wps365-cli.exe`。
  - **技能正文改为占位符 + 单行命令**：`python3 …` → `{python} …`、`/tmp/…` → `{tmp}/…`、直接调 CLI 处 → `{cli}`，由插件在加载时注入本机真实值（解释器、临时目录、CLI 路径）；命令不再使用 `\` 续行，也不再标 `bash` 围栏，因此 bash / PowerShell / cmd 都能直接粘贴执行。注入块还会说明本机平台与引号规则。
  - 脚本侧同样平台化：`_wps_cli.py` 优先用 `shutil.which`（Windows 上自动尊重 `PATHEXT`），并支持 `WPS365_INSTALL_DIR` 覆盖安装目录。
- 测试新增 `test/cli-locate.test.mjs`：在 macOS 上覆盖 Windows 语义（扩展名探测、`PATHEXT`、`;` 分隔的 `PATH`、`%USERPROFILE%`、原生分隔符拼接），全部用注入的假文件系统，无需真机。

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
[0.1.0]: https://github.com/YiuTerran/dsh-wps365-plugin/releases/tag/v0.1.0
