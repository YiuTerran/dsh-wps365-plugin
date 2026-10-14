# dsh-wps365-plugin

把 **`wps365-docs` 技能**打包成 DeepSeek Harness（DSH）插件的 bundle：装进 profile 后，任何会话都能加载这个技能，读取与写入 WPS 365 云文档。

- **技能**：`wps365-docs`（智能文档 `.otl` / 普通文件 `.docx` `.xlsx` `.pptx` 的读、写、改、并发比对）
- **随包资源**：`SKILL.md` + 3 份按需读取的 `references/` + 5 个 Python 脚本
- **零依赖**：`package.json` 里没有 `dependencies` / `peerDependencies`，安装时不解析任何 npm 包
- **Host 侧只注册技能**：脚本不被插件执行，由模型按技能说明通过 `bash` 工具调用

---

## 组成

```
.
├── index.js                      # Host 插件：向 ctx.skills 注册 wps365-docs
├── src/
│   ├── plugin-config.js          # 手写 Standard Schema v1 配置（零依赖）
│   └── skill-catalog.js          # 纯函数：解析 frontmatter、构造 catalog entry、渲染资源块
├── cordis.patch.yml              # bundle patch：插入 skill-wps365-docs 行
├── assets/wps365-docs/
│   ├── SKILL.md                  # 常驻正文（约 3.4k 字符，远低于 8192 的修剪阈值）
│   ├── references/
│   │   ├── otl.md                # 智能文档：读取/写入/分享链接/17 条硬约束
│   │   ├── office-files.md       # 普通文件：下载→本地改→传回、接口写入、本地编辑坑
│   │   └── concurrency.md        # 写入前并发比对、块级/整文件哈希、实测语义
│   └── scripts/                  # otl_fetch.py / otl_write.py / file_fetch.py / file_write.py / xlsx_edit.py
├── locale/{en,zh}.json           # 插件卡片标题与描述
├── icon.svg
└── test/                         # 离线测试（node --test，无需联网）
```

### 为什么把技能拆成 `SKILL.md` + `references/`

标准 preset 的 `tool-result-pruner`（`thresholdChars: 8192`）会把超过 8192 字符的工具结果**从中间剪掉**，只留头 4096 + 尾 1024。原始单文件 `SKILL.md` 约 24 KB，一旦被加载就会丢掉大半内容。所以这里按 DSH 自带技能的惯例拆开：

- `SKILL.md` 只留**必须立刻知道**的东西：两类文档的两条路线、写入前并发比对（硬性规则）、读写命令骨架、三条硬规矩；
- 细节（17 条硬约束、实测数据、接口细节）放进 `references/`，由模型按需 `read` 单个文件，每个文件都在阈值内。

正文里的路径统一写作 `{skillDir}` 占位符：插件在加载技能时把真实绝对路径作为一个「本机资源」区块**追加**到技能正文末尾，因此文档本身与安装位置无关（profile bundle、工作区 bundle、打包应用都能用）。

## 安装

推荐用 `plugin_manager`（在 DSH 里让 agent 调用）：

```
plugin_manager { action: "install_bundle", target: "/绝对路径/dsh-wps365-plugin" }
```

它会完成包安装与 bundle 选择，**不要**手改 profile 的 `package.json` / `cordis.patch.yml`，也不要在 profile 目录里跑 pnpm。

命令行等价写法：

```bash
dsh plugin --profile desktop add /绝对路径/dsh-wps365-plugin
```

安装后重启（或让 HMR 生效）即可在技能目录里看到 `wps365-docs`。卸载：

```
plugin_manager { action: "remove_bundle", target: "dsh-wps365-plugin" }
```

## 配置

行 id 是 `skill-wps365-docs`，三个字段都有默认值，**不写 `config` 也能用**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `assetRoot` | string | 包内 `assets/wps365-docs/` | 绝对路径；打包应用把资源复制到别处时用 |
| `cli` | string \| false | `~/.local/bin/wps365-cli` | `wps365-cli` 可执行文件路径；`false` = 不向模型报告 CLI |
| `python` | string \| false | 未设置 | 带 openpyxl / python-docx 的解释器绝对路径；不设置则让模型用 `load_workspace_dependencies` 返回的 Python |

在 profile 自己的 `cordis.patch.yml` 里改（**patch 是整体替换 `config`，要写全**）：

```yaml
- id: skill-wps365-docs
  name: './index.js'
  config:
    cli: /opt/wps365/bin/wps365-cli
    python: /opt/python/bin/python3
```

字段名写错会在激活时直接报错（`unknown config field "..."`），不会静默忽略。

## 前置条件

1. **`wps365-cli`**：本插件不安装它，只使用它。默认路径 `~/.local/bin/wps365-cli`；没授权就先
   `wps365-cli config init` 和 `wps365-cli auth login --device`。路径不同请配 `cli`，或让模型给脚本传 `--cli`。
   技能可用不代表 CLI 已授权 —— CLI 缺失/未授权时插件仍会正常加载，只是把「⚠️ 未找到 wps365-cli」写进技能正文。
2. **Python + openpyxl**：`file_fetch.py`（`.xlsx` 公式解析）和 `xlsx_edit.py` 需要 openpyxl。
   技能说明里让模型用 `load_workspace_dependencies` 返回的解释器（该环境自带 openpyxl / python-docx）；
   也可以配 `python` 固定一个。
3. **`bash` 工具**：脚本由模型通过 `bash` 跑（技能不注册任何 Host 工具）。技能还需要文件读写工具来分页读 `doc.md`、`read_image` 看图。
4. **并发比对用的快照/备份目录**（如 `/tmp`）：技能要求写入前先导出备份。

## 与 `~/.dsh/skills/wps365-docs` 的关系

两者可以共存，但**同名技能在同一个 layer 里按 rank 取小者**：

| 来源 | rank |
|---|---|
| `<项目>/.dsh/skills` / `.agents/skills` | 100 / 200 |
| 用户自定义目录 / `~/.dsh/skills` / `~/.agents/skills` | 300 / 400 / 500 |
| **本插件（bundled）** | **600** |

也就是说 `~/.dsh/skills/wps365-docs` 会**盖住**插件里的版本。想让插件版本生效，删掉（或改名）那份手动安装的副本：

```bash
rm -rf ~/.dsh/skills/wps365-docs
```

`dsh-skill-filesystem` 会 watch 技能根目录，删除后无需重启。

## 验证

离线（无需 Harness、无需联网、无需安装）：

```bash
node --test          # 30 个用例：catalog 纯函数 + bundle/补丁结构 + apply() 契约 + 配置校验
node --check index.js
python3 -m py_compile assets/wps365-docs/scripts/*.py
```

（其中 1 个用例在能解析到 Host 的 `yaml` 包时才会真正解析补丁，否则跳过 —— 仓库本身不引入依赖。）

装进 profile 后：

1. 用 `cordis_inspect_query`（Host）确认多出 `skill-wps365-docs` 行且已激活；`plugin_manager list_plugins` 也可，但要权限。
2. 新开一个会话问「读一下这个 WPS 文档 <file_id>」，确认模型调到 `wps365-docs` 技能、且技能正文里出现了「本机资源（由 dsh-wps365-plugin 注入）」区块。
3. 若 CLI 未授权，技能正文应出现 ⚠️ 提示，且调用脚本时 CLI 报未登录 —— 这是环境问题，不是插件问题。

## 已知限制

- 安装/替换**已安装包的新版本**需要重启 Harness 才能加载新的 JS 模块代（配置改动可以走 HMR）。
- 技能正文里的 `{skillDir}` 只在插件注入的区块里定义；如果绕过技能系统直接读 `assets/wps365-docs/SKILL.md`，需要自己把占位符换成实际路径。
- 脚本原样随包分发，其实现细节（参数、行为、数值）以脚本本身为准；`references/` 保留的是撰写时的实测结论。
- 本仓库把包标为 `"private": true`，避免误发 npm；要发布时删掉该字段并补 `repository` / `publishConfig`。

## 开发

- 只改技能文档：编辑 `assets/wps365-docs/**`，下次加载技能时即生效（`get()` 每次现读 SKILL.md），**不需要**重装插件。
- 改了 `index.js` / `src/**`：需要重装 bundle 并重启（见「已知限制」）。
- 新增脚本：加进 `assets/wps365-docs/scripts/`，再在 `src/skill-catalog.js` 的 `SKILL_SCRIPTS` 里登记 —— 激活时会校验它是否存在。

## 许可

MIT
