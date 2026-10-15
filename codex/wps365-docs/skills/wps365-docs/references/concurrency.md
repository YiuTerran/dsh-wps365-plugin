# 写入前必须比对：文件是否已被他人修改

> 本文件属于 `wps365-docs` 技能，配合 `{skillDir}/SKILL.md` 使用。

> 命令约定：下方命令是 POSIX 单行示例。PowerShell 中对带引号的可执行文件使用调用运算符 &，例如 & "{python}" ... 或 & "{cli}" ...；将脚本路径分隔符改为反斜杠，参数与冲突检查选项保持一致。
> API behavior and measurements below are inherited from the DSH 0.1.2 project notes; they are not new Codex cloud end-to-end verification.



**这是硬性前置步骤，不是可选优化。** 云文档是多人协作的，从你读到内容到写回之间，别人可能已经改过。**发现被改过就放弃更新、并告知用户存在冲突**，由用户决定合并还是覆盖 —— 绝不静默覆盖别人的改动。

比对方式按文档类型分两种：

| 类型 | 比对方式 | 记录什么 |
|---|---|---|
| **`.otl`** | **块级局部比对** —— 只校验「本次改动依赖的块」 | 每个一级块的哈希（见 `--scope-blocks` / `--scope-index`） |
| **`.docx` / `.xlsx` / 其他** | **整文件哈希** | `drive file get` 的 `hash`(sha1) + `version` |

## .otl：局部比对（前提是自己知道要改哪几个块）

智能文档有块级结构，别人改了文档末尾跟你无关，不该挡住你改开头。**所以要显式声明依赖范围**：

```
# 0. 先导出本地备份
"{python}" "{skillDir}/scripts/otl_fetch.py" --file-id <id> --out "{tmp}/otl-backup" --cli "{cli}"

# 1. 先列块，挑出你要改的块
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <id> --list-blocks --cli "{cli}"
#    0  JAhWraLloiMK  [heading] 概述
#    1  OjRTXcRPYJQe  [paragraph] 本文说明 AI Agent 如何…

# 2. 存快照，并声明本次依赖哪几个块（--scope-index 用索引更省事）
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <id> --snapshot-out "{tmp}/snap.json" --scope-index 0,1 --cli "{cli}"

# 3. 写回时带上快照 —— 依赖块被改过就放弃写入
"{python}" "{skillDir}/scripts/otl_write.py" --file-id <id> --md "{tmp}/改后.md" --mode replace --expect-snapshot "{tmp}/snap.json" --cli "{cli}"
```

判定规则：

| 情形 | 结果 |
|---|---|
| 声明的依赖块**被改 / 被删** | ❌ 冲突，放弃写入并报出是哪几个块 |
| 只有**无关块**变化 | ✅ 放行（仅提示文档整体有变化） |
| 未声明 scope | ⚠️ 保守处理：全文任何变化都算冲突（`--allow-added` 可放行纯新增） |
| `--mode replace` | ❌ 会清空重写，**无论是否声明 scope 都按全文比对** |
| `--force` | 只在用户明确授权覆盖已报告冲突后使用；强制覆盖并打印告警 |

## 并发写入的实测语义（为什么 `replace` 必须全文比对）

WPS 有**两条互不相干的通道**，行为完全不同：

| 通道 | 行为 |
|---|---|
| **客户端/网页里多人同时打开编辑** | 实时协同（OT/CRDT），两边改动都合并保留、互相实时可见，不存在谁覆盖谁（官方协同机制，未实测） |
| **OpenAPI（本技能走的这条）** | **不是协同通道**，是「读-改-写的增量 RPC」。服务端逐条执行 `create`/`update`/`delete`，**不做任何合并** |

双写者实测（在临时文档上做的，已删除）：

| 并发情况 | 结果 |
|---|---|
| 两边都 `create`，且都用陈旧的同一 index | ✅ **两块都不丢**，但**顺序会颠倒** —— 后提交的插在前面 |
| 一边 `update` 某块、一边用它做 `replace` | ❌ **update 被直接抹掉**（数据丢失，实测 B 的修改消失） |
| 一边 `create`、一边用陈旧范围 `delete` | ✅ 新增的块**幸存**（在删除范围之外） |

两条对写代码有直接影响的结论：

1. **`blocks/create` 的 `index` 是「插入位置」，不是「追加锚点」** —— A 插到 index N 之后，B 再插到 index N 会把 A 顶到 N+1。所以并发新增**不丢数据，但顺序可能不符合任何一方的预期**。追加有序内容（编号列表、步骤）时尤其要注意，别盲目用 `--allow-added` 放行。
2. **`delete` 是按索引删范围，是真会删掉别人东西的操作** —— 它的范围基于「我当时看到文档有多少块」，别人增删之后这个范围已不对应我看到的那几个块。`replace` = delete 全部 + 重建，所以**它必须按全文比对**，这也是脚本的默认行为。

另外：`/v7/airpage/v2/.../blocks/*` 的请求体里**没有任何 `version` 前置条件** —— 这条通道**没有服务端乐观锁**，客户端的比对是唯一防线。

## 盘级元数据是异步滞后的（.otl 尤其明显）

`drive file get` 的 `version` / `hash` / `size` 反映的是**已落盘的导出物**，不是文档的实时状态。实测改完一篇 .otl（块级**立即**生效，airpage `version` 138→141）后，盘级元数据**仍是旧值** `version=14 size=86207`，**约 30 秒后**才变成 `version=15 size=168583`。

两个后果：

1. **`.otl` 绝不能拿盘级 hash 当变更检测依据** —— 你刚写完它还是旧值、别人刚改完也可能是旧值，两头都会误判。块级比对（`data.version` + 逐块哈希）是实时的，那才是正确依据。
2. **写完不要立刻用盘级元数据校验**，否则会误判成「没保存成功」。`.xlsx` 的接口写入同理（见「.xlsx 的接口写入」一节）。

## .docx / .xlsx：整文件哈希（够用，且不用下载）

这类文件是整文件替换，改任何一处都会改变整个文件的字节，所以「整文件哈希变了」就等价于「有人动过」。而且**不必下载** —— `drive file get` 的元数据里直接带 `hash`（sha1，实测与本地算出的完全一致）和 `version`：

```
# 取云端哈希/版本，并导出本地备份
"{python}" "{skillDir}/scripts/file_write.py" --drive-id <盘> --file-id <id> --print-state --cli "{cli}"
"{python}" "{skillDir}/scripts/file_fetch.py" --file-id <id> --out "{tmp}/office-backup" --download --cli "{cli}"
# version=2  hash=7d8d8e9f…  size=12169  最后修改=某人

# 写回时带基线；云端哈希/版本对不上就放弃
"{python}" "{skillDir}/scripts/file_write.py" --drive-id <盘> --file-id <id> --file "x.docx" --expect-hash 7d8d8e9f… --expect-version 2 --backup-dir "{tmp}/bk" --cli "{cli}"
```

`xlsx_edit.py` **自动完成比对**：它自己下载，天然知道基线，传回前会自动检查（输出第 ④ 步）。

⚠️ 哈希是**字节级**的：别人只是打开又保存、或服务端异步重存过，哈希同样会变 —— 这属于**误报**，但方向安全（宁可让用户确认，也不静默覆盖）。

**冲突时的正确做法**：重新用 file_fetch.py / otl_fetch.py 拉取最新版本，把自己的改动合并进去再写；只有用户明确授权覆盖已报告冲突后才用 --force。
