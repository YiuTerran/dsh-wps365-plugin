# Changelog

本文件记录面向使用者的变更；每个版本的细节也同步写在 `README.md` 的「更新记录」一节。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

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
