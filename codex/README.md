# Codex WPS 365 文档插件

这是给 Codex 本地执行环境使用的 WPS 365 文档插件。内容基于仓库里的 DSH 插件 0.1.2，复用其 wps365-docs 工作流与参考资料；Codex 版本是独立的 skills-only 插件，不加载 DSH 的 JS 运行时注入。

## 运行条件

插件需要本地执行器，以便调用 Python 脚本和用户自己安装的 wps365-cli。它不携带 WPS 凭据，也不负责安装、登录或验证用户的 WPS 账号。Python 依赖按任务选择：读写 .xlsx 需要 openpyxl，使用 python-docx 创建或处理 DOCX 时需要 python-docx；可用时优先使用 Codex 提供的工作区 Python 运行时。

技能通过当前 Codex 技能目录解析资源路径，使用随插件打包的 _wps_cli.resolve() 定位 CLI，并用 tempfile 创建临时目录。CLI 缺失时技能只说明官方安装入口和本机授权要求，不会自动安装或登录。任何真实云端写入都要求先留存备份及快照/哈希版本基线；冲突必须停下交由用户处理。

Office 文件编辑依赖已安装的 documents、spreadsheets 或 presentations 能力。xlsx_edit.py 是可选兼容脚本；Codex 技能不假设存在 DSH LibreOffice Kit 或 Node 运行时。写入前后需要区分本地命令结果、云端版本元数据和实际读回结果。构建与静态内容检查不能证明用户设备的 CLI 授权、Office 格式保真或云端读写成功。

## 构建

在仓库根目录运行：

    npm --prefix codex run build
    npm --prefix codex test

构建输出：

- codex/dist/wps365-docs/：可检查的插件目录，包含 manifests、技能内容、参考资料、随包图标和从原始 assets/wps365-docs/scripts 复制的 Python 脚本。
- codex/dist/wps365-docs-0.1.0.tar.gz：同一插件的归档包。

构建脚本只从原始 assets/wps365-docs/scripts 读取脚本，codex/ 源文件不会维护 Python 副本。源码目录为 codex/wps365-docs；生成内容集中在 codex/dist/。

## 开发与安装

可直接审阅 codex/wps365-docs/plugin.json、codex/wps365-docs/.codex-plugin/plugin.json 和 codex/wps365-docs/skills/wps365-docs/ 下的技能源文件。此目录可作为本地插件源码在 Plugin Creator 中导入和审阅；完成后可保存到个人账号。具体入口按当前 Creator 界面操作，这里不记录未经确认的 CLI 安装命令。

根目录的 DSH 插件仍可独立使用；Codex 构建只读取它的技能脚本和文档，不会修改 DSH 包、运行时或原有发布流程。
