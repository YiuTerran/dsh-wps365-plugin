# @tryao/dsh-wps365-plugin

给你的 DSH 装上 **WPS 365 云文档**能力。

装好之后不用再手动导出、复制粘贴：你可以直接让 agent 读一篇 WPS 智能文档、把内容写进云文档、改云端表格里的某个单元格，它都能真的去读写你的 WPS 365 文档 —— 包括别人通过分享链接给你的文档。

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
| "这是别人分享给我的链接，帮我读一下" | 从 kdocs / plus 分享链接解析出文档并正常读取（无需"接收文件"这类前置动作） |
| "帮我把这份文档改一下" | **写入前先比对云端是否已被他人修改**；发现冲突就停下、告诉你冲突在哪，绝不静默覆盖 |

它内置了这套流程里踩过的坑（17 条智能文档硬约束、普通文件接口细节、并发写入的实测语义），所以遇到"表格整块消失""公式变成空值""代码块语言被降级""写完下载还是旧内容"这类问题，它知道该怎么绕。

## 需要先准备什么

1. **`wps365-cli`**（本插件不安装它，只使用它）
   默认路径 `~/.local/bin/wps365-cli`。没配过就先授权：
   ```bash
   wps365-cli config init
   wps365-cli auth login --device
   ```
   装在别处就在配置里指定 `cli`（见下）。**CLI 缺失或未授权不会让插件失效**：技能照常可用，只是会在技能正文里明确告诉你 CLI 没找到。

2. **Python（带 openpyxl）**
   读 `.xlsx` 的公式、以及云端改表格需要 openpyxl。默认让 agent 用 `load_workspace_dependencies` 返回的解释器（该环境自带 openpyxl / python-docx）；想固定一个就在配置里指定 `python`。

3. **会话里有 `bash` 工具**
   脚本由模型通过 `bash` 执行；另外还需要文件读取工具分页读导出的 `doc.md`、`read_image` 看图片。

4. **删掉手动安装的同名技能（重要）**
   如果你之前把这份技能手动放在 `~/.dsh/skills/wps365-docs`，同名技能里**它优先级更高**（用户目录里的技能盖住插件自带的），插件的版本就不会被用上。想让插件版本生效：
   ```bash
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

> ⚠️ 请使用 **0.1.1 或更高版本**。0.1.0 有一个会让 Harness 打不开的严重问题，它从未发布到 npm；详见 [CHANGELOG.md](CHANGELOG.md)。

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
- **写入超时不等于写入失败**：智能文档的写入接口可能已在服务端提交只是响应没回来，所以它重试前会先查块数，避免写两遍。
- **写入前一定要看冲突结论**：它报冲突时，正确做法是重新拉最新版本、合并你的改动后再写；确实要覆盖才会用强制参数。
- **规模上限**：智能文档单次写入 1000 块（脚本自动分批）；代码块语言只有接口枚举内的才保留（写 `powershell` 会被静默降级成纯文本，它知道这件事）。
- **普通文件走"下载 → 本地改 → 传回"**：`.docx` / `.xlsx` / `.pptx` 没有可用的逐段写接口，公式、格式、合并、多工作表全靠整文件传回保住。云端 `.xlsx` 的接口写入是异步落盘的，写完不要立刻下载校验。
- **权限边界**：它用你本机 `wps365-cli` 的身份和权限操作云文档，能读写的就是那个账号能读写的东西；插件自己不保存、也不接触凭据。
- **上下文开销**：技能正文保持精简，细节（17 条硬约束、接口细节、并发实测语义）放在三份按需读取的参考文档里，只有真需要时才读。
- **升级**：换版本需要重启 Harness 才会加载新的 JS；只改技能文档（`assets/**`）的话，下次加载技能即生效。
- 本插件**不注册**任何新工具、也不改动你 profile 里的 MCP 配置；`mcp__wps365__*`（如果你配了 WPS 的 MCP）与它是两套独立能力。

## 验证安装

离线自查（不需要 Harness、不需要联网、不会碰你的云文档）：

```bash
node --test          # 30 个用例：技能解析/资源预算、bundle 与补丁结构、apply() 契约、配置校验
node --check index.js
python3 -m py_compile assets/wps365-docs/scripts/*.py
```

真实验证：让 agent 读一篇你确定有权限的文档，问它"这篇文档的标题是什么、有几个表格"，确认拿到的是真实内容；再让它用 `--dry-run` 写一次（只做转换、不写入、不产生文件），看块结构与比对输出是否符合预期，然后才动真文档。

## 仓库结构（想改插件的人看）

```
index.js                      # Host 插件：向 ctx.skills 注册 wps365-docs
src/plugin-config.js          # 配置校验（手写 Standard Schema v1，零依赖）
src/skill-catalog.js          # 纯函数：解析技能 frontmatter、构造 catalog entry、渲染本机资源块
cordis.patch.yml              # bundle patch：插入 skill-wps365-docs 行
assets/wps365-docs/
├── SKILL.md                  # 常驻正文（约 3.4k 字符，控制在 8192 的修剪阈值内）
├── references/               # otl.md / office-files.md / concurrency.md，按需读取
└── scripts/                  # otl_fetch.py otl_write.py file_fetch.py file_write.py xlsx_edit.py
locale/{en,zh}.json  icon.svg  test/
scripts/check-package.mjs     # 校验 npm 打包内容（CI 也会跑）
.github/workflows/            # ci.yml 跑测试与打包检查；release.yml 打 tag 时发布 npm
CHANGELOG.md  RELEASING.md    # 版本变更与发布流程
```

技能正文里的路径统一写成 `{skillDir}` 占位符，加载时由插件注入真实绝对路径，所以同一份资源放在 profile bundle、工作区 bundle 或打包应用里都能用。

包已发布到 npm（`publishConfig.access: public`），发布流程与注意事项见 [RELEASING.md](RELEASING.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。刻意**不声明任何 `dependencies` / `peerDependencies`**：profile 目录解析不到 `@deepseek-ai/*`（它们在 app 包内），而声明 peer 范围会被 DSH 的兼容性检查用来拒绝安装。

## 更新记录

- **0.1.1** — 修复一个**会导致 Harness 打不开**的严重问题。0.1.0 的手写 Config 上带了一个 `toJSON()` 方法，而 DSH 的 settings 服务用 `"toJSON" in schema` 这个鸭子类型挑选要渲染的 schema，选中后紧接着读 `schema.meta.volatile` —— 手写对象没有 `meta`，于是抛 `TypeError`，`settings/describe` 失败，桌面端 welcome RPC 报 `Web RPC failed`，窗口起不来。删掉 `toJSON()` 即可（`~standard.validate` 不受影响，配置校验照常），并加了回归测试锁住这个形状。同一版本起开始发布到 npm。
- **0.1.0** — 首个版本：把 `wps365-docs` 技能打包成 DSH bundle。**请勿使用**，未发布到 npm。

## 许可

MIT
