# 发布流程（维护者）

这个包要发布到 npm，让其他人能在 DSH 的插件管理器里**按包名**安装。发布由 GitHub Actions 在打 tag 时自动完成。

## 一次性准备

1. **注册 npm 账号**，并确认包名 `dsh-wps365-plugin` 未被占用（当前是空位）。
2. **配置 Trusted Publishing（推荐）**：npmjs.com → 该包 → `Settings` → `Trusted Publisher` → 选 `GitHub Actions`，填：

   | 字段 | 值 |
   |---|---|
   | Organization or user | `YiuTerran` |
   | Repository | `dsh-wps365-plugin` |
   | Workflow filename | `release.yml` |
   | Environment | 留空 |

   这样 CI 用 OIDC 直接向 npm 证明来源，**仓库里不需要保存任何长期 token**，发布产物还自带 provenance 签名。
   首次发布前包还不存在，先在本地用 `npm publish`（见下方「本地兜底」）创建包，再回 npmjs.com 配 Trusted Publisher。
3. **备选：用 token**。在仓库加 `NPM_TOKEN` secret，并在 `release.yml` 的 publish 步骤上给 `NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}`。

## 每次发版

```bash
# 1. 确认干净且测试通过
npm test

# 2. 改版本号（例如 0.1.1 → 0.1.2），同步更新 CHANGELOG.md 与两个 README 的「更新记录」
npm version patch --no-git-tag-version

# 3. 提交并推送
git add -A && git commit -m "release: v0.1.2" && git push

# 4. 打 tag 并推送 —— 这一步会触发 release.yml 自动发布
git tag v0.1.2 && git push origin v0.1.2
```

`release.yml` 会先跑测试、校验 **tag 与 `package.json` 的版本一致**，再用 `npm publish --provenance` 发布。本地不需要 `npm login`。

## 本地兜底（CI 不可用时）

```bash
npm login                # 需要 npm 账号与 2FA
npm test
npm publish --access public
```

## 硬性注意事项

- **绝对不要发布 0.1.0。** 它会让 Harness 无法启动（见 CHANGELOG）。真正的起点是 0.1.1。npm 不允许覆盖已发布的版本号，所以一旦误发只能 `npm deprecate dsh-wps365-plugin@0.1.0 "breaks DeepSeek Harness startup; use 0.1.1+"` 补救。
- **不要给这个包加 `peerDependencies`。** DSH 的插件兼容性检查读的就是 peer 范围（第三方插件曾因 peer 不匹配被拒绝安装），而本包零依赖、只用相对导入与 `node:` 内置模块，声明 peer 只会缩小可安装范围。改成在 README 里写明实测的 DSH 版本。
- **发布前跑一次 `npm pack --dry-run`**，确认 `assets/`、`cordis.patch.yml`、`locale/`、`icon.svg` 都在文件清单里。`files` 字段是白名单，漏一个目录会让装好的 bundle 缺文件。CI 每次也会跑这一步。
- 发布后可以用一条命令验证真实安装效果（会改到你的 profile，注意先备份 `cordis.patch.yml`）：

  ```bash
  dsh plugin --profile desktop add dsh-wps365-plugin
  ```
