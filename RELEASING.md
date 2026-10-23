# 发布流程（维护者）

这个包发布到 npm，让其他人能在 DSH 的插件管理器里**按包名**安装。发布由 GitHub Actions 在打 tag 时自动完成。

- **npm 包名**：`@tryao/dsh-wps365-plugin`（scoped）
- **GitHub 仓库名**：`dsh-wps365-plugin`（不是包名，URL 与 Trusted Publisher 里都用它）

## 为什么用 scoped 名

最初尝试发布无 scope 的 `dsh-wps365-plugin`，npm 在 PUT 阶段直接拒绝：

```
403 Forbidden - PUT https://registry.npmjs.org/dsh-wps365-plugin
Package name triggered spam detection
```

这是 npm 针对**全局包名**的反垃圾/相似度过滤（账号无发布历史、名字含品牌词等都会触发），与包内容无关；同类案例的解法只有「改名（通常是加自己的 scope）」或「向 npm 官方申诉人工复核」。因此改用 `@tryao/dsh-wps365-plugin`：发布到自己的命名空间，绕开全局名字的启发式过滤。参考案例：[community #132982](https://github.com/orgs/community/discussions/132982)、[community #208302](https://github.com/orgs/community/discussions/208302)、[community #191906](https://github.com/orgs/community/discussions/191906)、[Stack Overflow 48668389](https://stackoverflow.com/questions/48668389/npm-publish-failed-with-package-name-triggered-spam-detection)。

## 一次性准备

1. **npm 账号**：用户名 `tryao` 即 scope 名，无需新建组织。确认 `@tryao/dsh-wps365-plugin` 尚未发布。
2. **首次发布必须在本地做**（有先后依赖）：Trusted Publisher 只能在**包已存在**之后配置，所以先用 `npm login && npm publish` 把包创建出来（见下方「本地兜底」）。
3. **配置 Trusted Publishing（推荐）**：npmjs.com → 该包 → `Settings` → `Trusted Publisher` → 选 `GitHub Actions`，填：

   | 字段 | 值 | 说明 |
   |---|---|---|
   | Organization or user | `YiuTerran` | GitHub 账号 |
   | Repository | `dsh-wps365-plugin` | GitHub 仓库名，**不是** npm 包名 |
   | Workflow filename | `release.yml` | |
   | Environment | 留空 | |

   这样 CI 用 OIDC 直接向 npm 证明来源，**仓库里不需要保存任何长期 token**，发布产物还自带 provenance 签名。
4. **备选：用 token**。在仓库加 `NPM_TOKEN` secret，并在 `release.yml` 的 publish 步骤上给 `NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}`。

## 每次发版

```bash
# 1. 确认干净且测试通过（含打包内容检查）
npm test && npm run check:pack

# 2. 设置目标版本（以下以 0.2.5 为例，已是目标版本则跳过命令），同步更新 CHANGELOG.md 与两个 README 的「更新记录」。Codex 插件的两份 manifest 也应使用根目录 `package.json` 的版本；构建会检查一致性。
npm version 0.2.5 --no-git-tag-version

# 3. 提交并推送
git add -A && git commit -m "release: v0.2.5" && git push

# 4. 打 tag 并推送 —— 这一步会触发 release.yml 自动发布
git tag -a v0.2.5 -m "Release v0.2.5" && git push origin v0.2.5
```

`release.yml` 会先跑测试、校验 **tag 与 `package.json` 的版本一致**，再用 `npm publish --provenance` 发布，然后自动创建一个 **GitHub Release**（标题为 `Release vX.Y.Z`，正文取自 `CHANGELOG.md` 中本版本的章节）。配置好 Trusted Publisher 后，本地不需要 `npm login`。

## 本地兜底（CI 不可用，或首次创建包）

```bash
npm login                # 需要 npm 账号与 2FA
npm test && npm run check:pack
npm publish              # scoped 包公开可装靠 publishConfig.access: "public"（已写入 package.json）
```

## 硬性注意事项

- **绝对不要发布 0.1.0。** 它会让 Harness 无法启动（见 CHANGELOG）。真正的起点是 0.1.1。npm 不允许覆盖已发布的版本号，一旦误发只能 `npm deprecate @tryao/dsh-wps365-plugin@0.1.0 "breaks DeepSeek Harness startup; use 0.1.1+"` 补救。
- **不要给这个包加 `peerDependencies`。** DSH 的插件兼容性检查读的就是 peer 范围（第三方插件曾因 peer 不匹配被拒绝安装），而本包零依赖、只用相对导入与 `node:` 内置模块，声明 peer 只会缩小可安装范围。改成在 README 里写明实测的 DSH 版本。
- **发布前跑 `npm run check:pack`**：它断言 tarball 含全部 26 个必需路径，且不含字节码/日志/tgz 等仓库专属文件。`files` 字段是白名单，漏一个目录会让装好的 bundle 缺文件。CI 每次也跑这一步。
- **`publishConfig.access` 必须保持 `"public"`**，否则 scoped 包默认是私有的，别人装不了。
- 发布后可以验证真实安装效果（会改到你的 profile，注意先备份 `cordis.patch.yml`）：

  ```bash
  dsh plugin --profile desktop add @tryao/dsh-wps365-plugin
  ```
