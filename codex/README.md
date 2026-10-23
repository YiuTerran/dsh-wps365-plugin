# Codex WPS 365 文档插件

这是给 Codex 本地执行环境使用的 WPS 365 文档插件。内容基于仓库里的 DSH 插件 0.2.5，复用其 wps365-docs 工作流与参考资料；Codex 版本是独立的 skills-only 插件，不加载 DSH 的 JS 运行时注入。

## 运行条件

插件需要本地执行器，以便调用 Python 脚本和用户自己安装的 wps365-cli。它不携带 WPS 凭据，也不负责安装 CLI 或验证用户的 WPS 账号。授权过期时脚本会自动刷新 delegated token；refresh token 也失效时运行 `auth login --device`（打开浏览器）并重试一次，设置 `WPS365_NO_AUTO_LOGIN=1` 可关闭自动登录。Python 依赖按任务选择：读写 .xlsx 需要 openpyxl，使用 python-docx 创建或处理 DOCX 时需要 python-docx；可用时优先使用 Codex 提供的工作区 Python 运行时。

技能通过当前 Codex 技能目录解析资源路径，使用随插件打包的 _wps_cli.resolve() 定位 CLI，并用 tempfile 创建临时目录。CLI 缺失时技能只说明官方安装入口和本机授权要求，不会自动安装；CLI 存在但 delegated token 过期时，脚本会刷新或启动设备码授权（`auth.py ensure` 也可手动调用）。access token 只有 2 小时，但 refresh token 有效 365 天并轮换，正常情况下无需每 2 小时登录。任何真实云端写入都要求先留存备份及快照/哈希版本基线；冲突必须停下交由用户处理。

Office 文件编辑依赖已安装的 documents、spreadsheets 或 presentations 能力。xlsx_edit.py 是可选兼容脚本；Codex 技能不假设存在 DSH LibreOffice Kit 或 Node 运行时。写入前后需要区分本地命令结果、云端版本元数据和实际读回结果。构建与静态内容检查不能证明用户设备的 CLI 授权、Office 格式保真或云端读写成功。

## 构建

在仓库根目录运行：

    npm --prefix codex run build
    npm --prefix codex test

构建输出：

- codex/dist/wps365-docs/：可检查的插件目录，包含 manifests、技能内容、参考资料、随包图标和从原始 assets/wps365-docs/scripts 复制的 Python 脚本。
- codex/dist/wps365-docs-<版本>.tar.gz：同一插件的归档包（文件名跟随当前版本，例如 `wps365-docs-0.2.5.tar.gz`）。

Codex 两份 manifest 的版本号跟随仓库根目录 `package.json`；后续发版时同步更新两份 manifest，构建会在版本不一致时失败。

构建脚本只从原始 assets/wps365-docs/scripts 读取脚本，codex/ 源文件不会维护 Python 副本。源码目录为 codex/wps365-docs；生成内容集中在 codex/dist/。

### 共享段落校验

`codex/wps365-docs/skills/wps365-docs/references/*.md` 是与 DSH 原版**平行维护**的改编副本（脚本是单源复制的，文档不是）。`npm --prefix codex test`（CI 也会跑）会比对两边**共享的编号条文与整节段落**：

- 只改一侧的条文 → 报 `第 N 条两侧不再一致`，并给出两侧的短哈希；
- 一侧新增或删除条目 → 报 `codex 缺第 N 条` / `codex 多出第 N 条`；
- 新增条文必须先在 `checks/validate.mjs` 的 `SHARED_DOCUMENTS` 里登记：`shared`（两边必须逐字一致）或 `adapted`（有意为 Codex 改写，只校验存在性与条目数），否则报 `既未声明为 shared 也未声明为 adapted`。

也就是说：以后在 DSH 侧修正一条实测结论时，如果 Codex 侧没跟上，CI 会直接失败，而不是留下两份互相矛盾的说法。

## 开发与安装

可直接审阅 codex/wps365-docs/plugin.json、codex/wps365-docs/.codex-plugin/plugin.json 和 codex/wps365-docs/skills/wps365-docs/ 下的技能源文件。此目录可作为本地插件源码在 Plugin Creator 中导入和审阅；完成后可保存到个人账号。具体入口按当前 Creator 界面操作，这里不记录未经确认的 CLI 安装命令。

根目录的 DSH 插件仍可独立使用；Codex 构建只读取它的技能脚本和文档，不会修改 DSH 包、运行时或原有发布流程。
