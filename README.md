# @tryao/dsh-wps365-plugin

给你的 DSH 装上 **WPS 365 云文档**能力。

本仓库同时支持 DSH 插件和 Codex 本地插件适配，二者版本同步；安装方式分别说明。Codex 版本的源码与构建说明见 [`codex/`](codex/README.md)。

装好之后不用再手动导出、复制粘贴：你可以直接让 agent 读一篇 WPS 智能文档、把内容写进云文档、改云端表格里的某个单元格，它都能真的去读写你的 WPS 365 文档 —— 包括别人通过分享链接给你的文档。

> ⚠️ **本插件仅适用于 WPS 365 企业版账号，个人版账号请勿使用。**
>
> 它调用的是 WPS 365 企业版提供的云文档接口，`wps365-cli` 也必须用企业版账号完成授权。WPS 个人版账号没有这些能力，装上也读写不了任何文档；个人版用户请不要安装，也不要尝试用它处理本地文件。

> 插件本体只提供技能（`wps365-docs`）与配套脚本，**不含**任何 WPS 账号信息；所有云文档操作都由你自己安装的 `wps365-cli` 完成。

---

## 它能帮你做什么

| 你可以这样说 | 它会做什么 |
|---|---|
| "把这篇 WPS 文档总结一下" / "这篇智能文档里有哪些待办" | 读智能文档（`.otl`）：还原成 Markdown（标题、列表、**表格**、引用、加粗、代码块），长文自动分页读，不把全文塞进上下文 |
| "文档里的那张架构图给我看看" | 下载文档里**所有**内嵌图片到本地（含表格单元格里的图、processon 图），只按需把你要看的那几张读进上下文 |
| "把这份 Markdown 写进这篇智能文档" | 写入智能文档：整篇替换 / 追加到末尾 / 插到最前；写入前可先预演块结构 |
| "把文档里第二段改掉" | 逐段原地编辑智能文档（块级 API），不用整篇重写 |
| "读一下这个 .docx / .xlsx / .pptx" | 下载原件到本地，并抽出正文 Markdown、表格、**公式原文**、每个工作表一份 CSV |
| "把附件里的这个表格改一下再传回去" | 本地编辑后整文件传回：`file_id` 与分享链接不变，版本历史保留、可回滚；也能新建文件 |
| "把云端表格里 B2 改成 99，D2 加上公式" | 云端 `.xlsx` 一条龙：下载 → 改 → 重算公式缓存值 → 传回，一条命令完成 |
| "把这个多维表里的周报列出来" / "按项目筛一下" | 读多维表（`.dbt`）：先看 schema，再分页/过滤读记录；支持视图、字段选择、附件清单，Form 视图会提示改读底层表 |
| "把这条记录的备注改掉" | 快照 → 部分更新 → 读回校验；Link/选项非法值会被接口静默清空，脚本会先校验、写后比对并明确报错 |
| "把周报里那张图下载下来" | 从 raw 记录取 `uploadId` 下载附件；按文件头纠正扩展名（实测服务器会把 PNG 转成 JPEG） |
| "这是别人分享给我的链接，帮我读一下" | 从 kdocs / plus 分享链接解析出文档并正常读取（无需"接收文件"这类前置动作） |
| "帮我把这份文档改一下" | **写入前先比对云端是否已被他人修改**；发现冲突就停下、告诉你冲突在哪，绝不静默覆盖 |

它内置了这套流程里踩过的坑（17 条智能文档硬约束、15 条多维表硬约束、普通文件接口细节、并发写入的实测语义），所以遇到"表格整块消失""公式变成空值""代码块语言被降级""写完下载还是旧内容""多维表写入成功但字段空了"这类问题，它知道该怎么绕。

## 需要先准备什么

1. **WPS 365 企业版账号（必须）**
   本插件只适用于 WPS 365 企业版账号；**WPS 个人版账号不受支持，请勿安装和使用**。`wps365-cli` 需要先用企业版账号授权，本文提到的智能文档、云文档读写与分享链接解析能力才会生效。

2. **`wps365-cli`（运行本插件的必要条件；本插件不安装它，只使用它）**
   所有云文档操作都由这个官方 CLI 完成；没装它，插件读写不了任何文档。[`wps365-open/cli`](https://github.com/wps365-open/cli) 是它的源码与文档仓库，用官方安装脚本安装：

   **macOS / Linux**

   ```bash
   curl -fsSL https://open-docs.wpscdn.cn/cli/install.sh | bash
   ```

   **Windows（PowerShell）**

   ```powershell
   irm https://open-docs.wpscdn.cn/cli/install.ps1 | iex
   ```

   **Windows（Git Bash）**

   ```bash
   curl -fsSL https://open-docs.wpscdn.cn/cli/install.sh | bash
   ```

   macOS / Linux 默认装到 `~/.local/bin`（无需 sudo），Windows 上是 `~\.local\bin\wps365-cli.exe`。要指定版本或安装目录，在管道末尾的 `bash` 前加环境变量：`... | WPS365_VERSION=v0.3.6 bash`、`... | WPS365_INSTALL_DIR=~/.local/bin bash`；不想用脚本就去 [Release 页面](https://github.com/wps365-open/cli/releases) 下载对应平台的二进制文件。

   装好后完成应用绑定与授权（只需一次）：

   ```bash
   wps365-cli config init        # 新建/绑定应用，在浏览器里完成
   wps365-cli auth login --device
   wps365-cli user me            # 确认当前登录身份
   ```

   `config init` 会打开浏览器完成应用创建/绑定与授权确认；企业管理员建议预先设置 CLI 应用免审规则，手动创建应用、配置回调地址与申请权限的完整流程见官方[前置准备：创建应用与权限配置](https://github.com/wps365-open/cli/blob/main/docs/prerequisites.md)。装在别处就在配置里指定 `cli`（见下；支持 `~` 与 `%NAME%`/`${NAME}` 展开，也可以直接写裸命令名让它在 `PATH` 里找）。**CLI 缺失或未授权不会让插件失效**：技能照常可用，只是会在技能正文里明确告诉你 CLI 没找到。

   **授权有效期**：access token 只有 2 小时，但 CLI 会在过期前自动刷新、401 透明重试；delegated refresh token 有效 **365 天**且每次刷新轮换。正常情况下 `auth login --device` 一年一次，不必每 2 小时重新登录。refresh token 过期/被撤销、token 存储丢失，或 CLI 回退到 app 身份后报 `required delegated scopes` 时，插件脚本会自动 `auth refresh --delegated`，失败则运行 `auth login --device` 打开浏览器并重试一次；不想自动弹窗可设 `WPS365_NO_AUTO_LOGIN=1`。直接调用 CLI 前也可手动执行 `python3 <技能目录>/scripts/auth.py ensure --cli <cli>`。不要设置 `WPS365_ACCESS_TOKEN`，它会绕过刷新，2 小时后必然失效。官方依据：[用户授权流程](https://open.wps.cn/documents/app-integration-dev/wps365/server/certification-authorization/user-authorization/flow.html)（refresh_token 365 天、刷新时轮换）与 [CLI 认证说明](https://github.com/wps365-open/cli#认证)（自动刷新与 401 重试）。刚登录后 `refresh_token_expires_at` 可能显示 `0001-01-01`（Go 零值/未提供），这不是过期；`auth.py` 会显示为“未知”，不会误触发刷新或重登。

3. **Python（带 openpyxl）**
   读 `.xlsx` 的公式、以及云端改表格需要 openpyxl。默认让 agent 用 `load_workspace_dependencies` 返回的解释器（该环境自带 openpyxl / python-docx）；想固定一个就在配置里指定 `python`。

4. **会话里有 shell 工具**
   脚本由模型通过 shell 执行（macOS / Linux 是 `bash`，Windows 是 PowerShell 或 `cmd`）；另外还需要文件读取工具分页读导出的 `doc.md`、`read_image` 看图片。

5. **平台差异（Windows）**
   技能正文里的命令一律是单行、占位符由插件注入真实值，所以两个平台都能直接照抄；注入的「本机资源」一节会告诉你本机平台、Python 解释器、`wps365-cli` 路径与临时目录。Windows 上另需注意：路径含空格时用双引号包住。

6. **删掉手动安装的同名技能（重要）**
   如果你之前把这份技能手动放在 `~/.dsh/skills/wps365-docs`，同名技能里**它优先级更高**（用户目录里的技能盖住插件自带的），插件的版本就不会被用上。想让插件版本生效：
   ```
   rm -rf ~/.dsh/skills/wps365-docs
   ```
   技能目录是被 watch 的，删完不用重启。

## 安装

**从 npm 装（推荐）** —— 已发布到 npm，按包名安装即可：

```bash
dsh plugin --profile desktop add @tryao/dsh-wps365-plugin
```

也可以在 DSH 的插件管理器界面里搜索 `@tryao/dsh-wps365-plugin` 安装。

**从本地目录装**（改插件时用，软链到仓库，改完即时生效；这里的路径是**仓库目录**，与 npm 包名无关）：

```bash
dsh plugin --profile desktop add /绝对路径/dsh-wps365-plugin
```

或让 agent 调用：

```
plugin_manager { action: "install_bundle", target: "@tryao/dsh-wps365-plugin" }
```

装好后新开一个会话，问一句"读一下这篇 WPS 文档 `<file_id>`"。确认生效的方法：加载进来的技能正文**末尾**会附带一段「本机资源（由 @tryao/dsh-wps365-plugin 注入）」，里面给出脚本目录、`wps365-cli` 路径和该用哪个 Python。

卸载：

```
plugin_manager { action: "remove_bundle", target: "@tryao/dsh-wps365-plugin" }
```

> ⚠️ DSH 插件请使用 **0.1.1 或更高版本**。DSH 的 0.1.0 有一个会让 Harness 打不开的严重问题，它从未发布到 npm；详见 [CHANGELOG.md](CHANGELOG.md)。

## 在 Codex 中使用

Codex 版本由 `codex/` 中的源码构建，和上面的 DSH 插件分别安装。Codex 版本同样**仅适用于 WPS 365 企业版账号**，个人版账号请勿使用。克隆仓库后，在仓库根目录构建并运行打包检查：

```bash
git clone https://github.com/YiuTerran/dsh-wps365-plugin.git
cd dsh-wps365-plugin
npm --prefix codex run build
npm --prefix codex test
```

构建需要 Node.js 22 或更高版本；生成 `.tar.gz` 归档还需要系统提供 `tar` 命令。无需运行 `npm install`，构建和检查使用 Node.js 内置模块。构建会生成完整插件目录 `codex/dist/wps365-docs/` 和归档 `codex/dist/wps365-docs-0.2.6.tar.gz`。请把**构建生成的目录**注册并安装；`codex/wps365-docs/` 是源码，不含构建复制进去的共享 Python 脚本，不能直接作为完整插件安装。

在 Codex 中可以这样请求安装：

> 将当前仓库构建得到的 `codex/dist/wps365-docs` 注册到我的个人本地插件市场，并帮我安装。保留已有市场配置。

Codex 的本地插件市场由本机配置发现，市场清单中的插件路径相对于市场根目录。安装到个人本地市场只对当前用户和设备生效，不会发布给其他用户；每位使用者都要安装并授权自己的 `wps365-cli`（安装与授权步骤见上文「需要先准备什么」第 2 条）：

```bash
wps365-cli config init
wps365-cli auth login --device
```

可参考 [Codex 官方插件构建说明](https://developers.openai.com/plugins/build/plugins)。如果安装后插件没有显示，重启 Codex 桌面应用；插件可用后新开一个聊天，再提出文档任务，例如：

> 读取这篇 WPS 文档并概括重点：<分享链接或 file_id>

Codex 使用本地执行环境运行 Python 脚本和 `wps365-cli`。处理 `.xlsx` 需要 `openpyxl`，用 `python-docx` 创建或处理 DOCX 需要 `python-docx`；有 Codex 提供的 `load_workspace_dependencies` 时可使用其中的 Python，否则本机需有相应 Python 依赖。Excel 编辑和公式重算使用当前可用的 Office 技能能力；Codex 版本不依赖 DSH 附带的 LibreOffice Kit。

## 配置（可选）

插件行的 id 是 `skill-wps365-docs`，三个字段都有默认值，**不配也能用**：

| 字段 | 默认 | 作用 |
|---|---|---|
| `cli` | `~/.local/bin/wps365-cli` | `wps365-cli` 的位置；设成 `false` 表示不注入 CLI 路径 |
| `python` | 未设置 | 指定跑脚本的解释器（需带 openpyxl / python-docx）；不设则用 `load_workspace_dependencies` 的 |
| `assetRoot` | 插件自带资源目录 | 绝对路径，仅当技能资源被搬到别处时需要 |

在 profile 自己的 `cordis.patch.yml` 里覆盖（patch 是整体替换 `config`，要写全）：

```yaml
- id: skill-wps365-docs
  name: './index.js'
  config:
    cli: /opt/wps365/bin/wps365-cli
    python: /opt/python/bin/python3
```

字段名写错会在激活时直接报错，不会静默忽略。

> ⚠️ 这个配置是手写的（为了让包保持零依赖），不是 DSH 原生的 schemastery 图，所以 DSH 的配置检查与设置界面**不会**为这一行渲染配置表单（`cordis_inspect_query` 里该行状态显示 `unsupported`）。这不影响插件工作 —— 行照常挂载、技能照常加载、字段校验照常生效，只是改配置要按上面的 YAML 手写。默认值在你的机器上就是对的，通常不用改。

## 使用时的注意事项

- **云文档没有本地撤销**：插件会按技能要求先导出备份，但重要文档建议你自己也留一份。
- **写入超时不等于写入失败**：智能文档的写入接口、多维表的创建接口都可能已在服务端提交只是响应没回来，所以它重试前会先查块数/记录数，避免写两遍。
- **写入前一定要看冲突结论**：它报冲突时，正确做法是重新拉最新版本、合并你的改动后再写；确实要覆盖才会用强制参数。
- **规模上限**：智能文档单次写入 1000 块（脚本自动分批）；多维表单请求最多 1024 条、限流约 10 次/秒（脚本单批 100）；代码块语言只有接口枚举内的才保留（写 `powershell` 会被静默降级成纯文本，它知道这件事）。
- **普通文件走"下载 → 本地改 → 传回"**：`.docx` / `.xlsx` / `.pptx` 没有可用的逐段写接口，公式、格式、合并、多工作表全靠整文件传回保住。云端 `.xlsx` 的接口写入是异步落盘的，写完不要立刻下载校验。
- **多维表是数据库，不是文档/文件夹**：`drive file list` 会返回空、`file-content` 会报"文档内容抽取失败"；只能走 dbsheet schema/records。FlexPaper 工作表读不了正文/记录，Form 视图不承载记录。
- **授权过期会自动恢复**：脚本检测到 token 过期/CLI 回退 app 身份时，会先刷新；刷新 token 也失效时运行 `auth login --device` 打开浏览器，成功后重试原命令。没有比 refresh token 更长的用户授权；`WPS365_AUTH=app` 可无人值守，但不能替代用户态文档权限。
- **权限边界**：它用你本机 `wps365-cli` 的身份和权限操作云文档，能读写的就是那个账号能读写的东西；插件自己不保存、也不接触凭据。
- **账号边界**：只支持 WPS 365 企业版账号；WPS 个人版账号请勿使用，接口不可用，插件无法工作。
- **上下文开销**：技能正文保持精简，细节（17 条智能文档 + 15 条多维表硬约束、接口细节、并发实测语义）放在四份按需读取的参考文档里，只有真需要时才读。
- **升级**：换版本需要重启 Harness 才会加载新的 JS；只改技能文档（`assets/**`）的话，下次加载技能即生效。
- 本插件**不注册**任何新工具、也不改动你 profile 里的 MCP 配置；`mcp__wps365__*`（如果你配了 WPS 的 MCP）与它是两套独立能力。

## 验证安装

离线自查（不需要 Harness、不需要联网、不会碰你的云文档）：

```bash
node --test                        # Node 侧用例：技能解析/资源预算、bundle 与补丁结构、apply() 契约、配置校验、CLI 定位
python3 test/scripts_test.py       # Python 侧契约用例：CLI 定位、管道解码、LibreOffice kit 探测（纯标准库）
python3 test/dbsheet_test.py       # 多维表纯函数与离线下载用例（本地 http.server，不碰云）
node --check index.js
python3 -m py_compile assets/wps365-docs/scripts/*.py
```

`npm test` 会依次跑这三条；CI 在 Linux 上跑（Windows 语义由注入的假环境覆盖，不依赖真机）。

真实验证：让 agent 读一篇你确定有权限的文档，问它"这篇文档的标题是什么、有几个表格"，确认拿到的是真实内容；再让它用 `--dry-run` 写一次（只做转换、不写入、不产生文件），看块结构与比对输出是否符合预期，然后才动真文档。

## 仓库结构（想改插件的人看）

```
index.js                      # Host 插件：向 ctx.skills 注册 wps365-docs
src/plugin-config.js          # 配置校验（手写 Standard Schema v1，零依赖）
src/skill-catalog.js          # 纯函数：解析技能 frontmatter、构造 catalog entry、渲染本机资源块
src/cli-locate.js             # 纯函数：跨平台定位 wps365-cli（PATHEXT、PATH、~ / %VAR% 展开）
cordis.patch.yml              # bundle patch：插入 skill-wps365-docs 行
assets/wps365-docs/
├── SKILL.md                  # 常驻正文（约 5k 字符，控制在 8192 的修剪阈值内）
├── references/               # otl.md / office-files.md / dbsheet.md / concurrency.md，按需读取
└── scripts/                  # otl_fetch.py otl_write.py file_fetch.py file_write.py xlsx_edit.py dbsheet.py auth.py
                              # _wps_cli.py：七个脚本共用的 CLI 定位、UTF-8 管道与授权恢复
locale/{en,zh}.json  icon.svg  test/   # test/scripts_test.py + test/dbsheet_test.py，npm test 会跑
scripts/check-package.mjs     # 校验 npm 打包内容（CI 也会跑）
scripts/clean-bytecode.mjs    # prepack：清掉跑脚本留下的 __pycache__
.github/workflows/            # ci.yml 跑测试与打包检查；release.yml 打 tag 时发布 npm
CHANGELOG.md  RELEASING.md    # 版本变更与发布流程
codex/                        # Codex 本地插件适配；构建与安装说明见 codex/README.md
```

技能正文里的路径统一写成 `{skillDir}` 占位符，加载时由插件注入真实绝对路径，所以同一份资源放在 profile bundle、工作区 bundle 或打包应用里都能用。

包已发布到 npm（`publishConfig.access: public`），发布流程与注意事项见 [RELEASING.md](RELEASING.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。刻意**不声明任何 `dependencies` / `peerDependencies`**：profile 目录解析不到 `@deepseek-ai/*`（它们在 app 包内），而声明 peer 范围会被 DSH 的兼容性检查用来拒绝安装。

## 更新记录

- **0.2.6** — 受限沙盒中首次认证或云端调用就申请沙盒外执行，后续云端调用保持相同获准权限，避免浏览器授权后才发现无法保存。凭据目录可写探测作为兜底，增加失败阶段标记与 7 项回归。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.5** — 将业务命令的权限错误检测限定于失败退出，避免成功读回的正文包含错误文字时误触发；新增回归并再次通过全量检查。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.4** — 区分凭据存储权限、服务端拒绝 refresh token 与网络/服务端故障。沙盒阻止保存时立即停止并提示申请权限，不重复登录；登录继续实时显示验证提示，新增 15 项认证回归及假 CLI 流程验证。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.3** — 强制直接 CLI 调用前执行 `auth.py ensure`，补全共享链接示例；明确 app 回退后先恢复用户身份再重试、`status` 不刷新、禁用自动登录仍允许 refresh。新增 `ensure --force-refresh` 处理远端拒绝但本地仍有效的 token，并修复 GitHub Release 工作流。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.2** — 修复刚完成设备码登录时 `refresh_token_expires_at` 显示为 `0001-01-01` 被误读为过期的问题：缺失/零值/畸形值统一视为“未知”，`auth.py status` 显示为未知，恢复逻辑不会因此刷新或重登。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.1** — 新增授权过期自动恢复：脚本遇到 access token 过期、refresh token 失效或 CLI 回退 app 身份时，自动刷新或运行 `auth login --device` 打开浏览器，并重试原命令一次；`WPS365_NO_AUTO_LOGIN=1` 可关闭弹窗。远端已拒绝授权时，即使本地 token 未过期也会尝试刷新；显式应用身份（环境变量或命令行）不会误触发用户登录，命令行身份优先。文档说明 2 小时 access token / 365 天 refresh token 的实际生命周期，以及为什么正常情况下不需要每 2 小时登录。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.2.0** — 新增**多维表（`.dbt`）**支持：`dbsheet.py` 提供 schema / 分页过滤读 / 单条读 / 创建 / 快照 / 部分更新 / 删除 / 附件下载，写入前按记录级快照比对、写后逐字段读回；Link 显示名自动映射成记录 ID，非法选项/日期先校验，附件按 `uploadId` 换取签名 URL 并按文件头纠正扩展名（实测 PNG 实为 JPEG）。同步补上 15 条多维表硬约束与 Form/FlexPaper 限制，Codex 适配、打包清单和 README 均已更新。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.1.4** — 修正 `.xlsx` 写回读回校验：重复设置同一单元格时只校验最后一次赋值，布尔值、文本引号与空白按实际写入类型比较，清空单元格时正确接受稀疏空响应。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.1.3** — 修掉一个**会静默骗人的写入失败**：`.xlsx` 的字节覆盖对「在线表格」不生效（版本号与字节都更新了，线上单元格却仍是旧值）。`xlsx_edit.py` 现在上传后会读回自己用 `--set` 写过的单元格，不一致就以退出码 1 明确失败并说明下一步；纯字节文件不受影响。文档同步补上这条硬约束（含实测数据）与 `versions` 接口必须带 `page_size` 等复测结论；Python 侧测试增到 34 例。详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.1.2** — 包含 Windows 支持与修复：CLI 按平台发现、脚本统一使用 UTF-8 管道与输出，Windows 上可定位 LibreOffice kit 和 Node，并给伪装成 `.xlsx` 的 `.xls` 提供可操作提示。Python 侧测试现为 27 例。另修复并发写入未检测块顺序变化、默认配置漏查 `PATH`，以及 Node 缺失时误用 Python 解释器启动的问题；详见 [CHANGELOG.md](CHANGELOG.md)。
- **0.1.1** — 修复一个**会导致 Harness 打不开**的严重问题。0.1.0 的手写 Config 上带了一个 `toJSON()` 方法，而 DSH 的 settings 服务用 `"toJSON" in schema` 这个鸭子类型挑选要渲染的 schema，选中后紧接着读 `schema.meta.volatile` —— 手写对象没有 `meta`，于是抛 `TypeError`，`settings/describe` 失败，桌面端 welcome RPC 报 `Web RPC failed`，窗口起不来。删掉 `toJSON()` 即可（`~standard.validate` 不受影响，配置校验照常），并加了回归测试锁住这个形状。同一版本起开始发布到 npm。
- **0.1.0** — 首个版本：把 `wps365-docs` 技能打包成 DSH bundle。**请勿使用**，未发布到 npm。

## 许可

MIT
