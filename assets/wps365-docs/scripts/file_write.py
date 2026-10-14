#!/usr/bin/env python3
"""把本地文件写回 WPS 365 —— request_upload 三步路线（**字节透传，格式无关**）。

这是编辑 .docx / .xlsx / .pptx 的**首选且唯一推荐**方式：

    file_fetch.py 下载原件 → 本地用 Office 技能改 → 本脚本原地传回

因为 `.docx` / `.xlsx` 都没有块级正文 API，而 sheets 接口连字面值都写不了
（只能写公式）。本地编辑再整文件回传既没有这些限制，又能保留公式、格式、
合并、多工作表。

  1) POST /v7/drives/{drive_id}/files/{parent_id}/request_upload
         {"file_id"?, "name", "size", "hashes":[md5, sha256]}
       → data.upload_id + data.store_request.{method,url}
  2) PUT 实体字节到 store_request.url（必须带 delegated token）
  3) POST /v7/drives/{drive_id}/files/{parent_id}/commit_upload  {"upload_id":...}

**带 `file_id` = 原地替换**：同一个 file_id、分享链接不变、版本历史保留（可 revert）。
**不带 `file_id` = 在 `parent_id` 下新建文件**（`0` 为盘根目录）。

`.docx` 没有块级正文 API（`/v7/documents/{file_id}/` 整组只有附件/评论/导出/点赞，
`/files/{file_id}/update` 只能改 `ext_attr`，`/files/{file_id}/content` 只有 GET），
所以语义上是整文件替换 —— 要逐段原地编辑请用 .otl（见 otl_write.py）。

最容易踩的坑（实测 v0.3.6）：
  * `hashes` **必填**，且只能用 **md5 / sha256**；**sha1 会被拒**，哪怕
    `enum_hash_type` 里列着它。报错是 `400000004 请求参数不支持`。
  * 别把下载接口给的哈希喂进来：`drive file download --with-hash` 只给 **sha1**。
  * 这个 400 **与权限无关**，不要误判成"没权限 / 接口不可用"。
  * `upload_scene` 可选，不是必需。
  * 上传与当前版本**完全相同的字节不产生新版本**（按内容哈希去重）。
  * `--dry-run` 只做第 1 步，**不产生任何文件**，可当无副作用的连通性测试。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.request

DEFAULT_CLI = os.path.expanduser("~/.local/bin/wps365-cli")
UA = {"User-Agent": "Mozilla/5.0"}


# ---------------------------------------------------------------- CLI 封装

def run(cmd: list[str], timeout: int, what: str) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:600]
        sys.exit(f"[file_write] {what} 失败（退出码 {proc.returncode}）：{detail}")
    return proc.stdout


def api(cli: str, method: str, path: str, timeout: int, data: dict | None = None,
        token_type: str = "delegated") -> dict:
    cmd = [cli, "api", method, path, "-o", "json", "--timeout", f"{timeout}s"]
    if token_type:
        cmd += ["--token-type", token_type]
    if data is not None:
        cmd += ["--data", json.dumps(data, ensure_ascii=False)]
    try:
        payload = json.loads(run(cmd, timeout, f"{method.upper()} {path}"))
    except json.JSONDecodeError as exc:
        sys.exit(f"[file_write] {method.upper()} {path} 返回不是 JSON：{exc}")
    if payload.get("code") not in (0, None):
        sys.exit(f"[file_write] {method.upper()} {path} 报错：{payload.get('msg') or payload}")
    return payload.get("data") or {}


def json_cmd(cli: str, args: list[str], timeout: int, what: str) -> dict:
    payload = json.loads(run([cli, *args, "-o", "json", "--timeout", f"{timeout}s"], timeout, what))
    if payload.get("code") not in (0, None):
        sys.exit(f"[file_write] {what} 报错：{payload.get('msg') or payload}")
    return payload.get("data") or {}


def auth_token(cli: str, timeout: int) -> str:
    return run([cli, "auth", "token"], timeout, "取 access token").strip()


# ---------------------------------------------------------------- 并发冲突检查

def current_state(cli: str, drive_id: str, file_id: str, timeout: int) -> dict:
    """取云端当前状态，供更新前比对。

    `drive file get` 的元数据里直接带 `hash`（sha1），**不用下载**就能拿到当前
    内容哈希；另有 version / mtime / modified_by 可用来描述"被谁改的"。
    """
    data = json_cmd(cli, ["drive", "file", "get", drive_id, file_id], timeout, "取文件状态")
    return {"version": data.get("version"),
            "hash": (data.get("hash") or {}).get("sum"),
            "size": data.get("size"),
            "mtime": data.get("mtime"),
            "modified_by": (data.get("modified_by") or {}).get("name")}


def check_conflict(cli: str, drive_id: str, file_id: str, timeout: int,
                   expect_hash: str | None = None, expect_version: int | None = None,
                   force: bool = False) -> dict:
    """更新前比对：若云端已被他人改动则放弃更新并提示冲突。

    不传任何期望值时不检查（调用方没提供基线，无从判断）。
    """
    if not expect_hash and expect_version is None:
        return {}
    cur = current_state(cli, drive_id, file_id, timeout)
    problems = []
    if expect_version is not None and cur["version"] != expect_version:
        problems.append(f"版本 {expect_version} → {cur['version']}")
    if expect_hash and cur["hash"] and cur["hash"] != expect_hash:
        problems.append(f"哈希 {expect_hash[:12]}… → {cur['hash'][:12]}…")
    if problems and not force:
        who = cur.get("modified_by") or "未知用户"
        sys.exit("[file_write] ⚠️  冲突：云端文件已被改动，已放弃更新。\n"
                 f"   文件：{file_id}\n"
                 f"   差异：{'；'.join(problems)}\n"
                 f"   最后修改：{who}\n"
                 "   处理：先 file_fetch.py 重新下载、把你的改动合并进去再上传；"
                 "确认要覆盖请加 --force。")
    if problems:
        print(f"⚠️  已忽略冲突（--force）：{'；'.join(problems)}")
    return cur


# ---------------------------------------------------------------- Markdown → docx

def _style_run(run_obj, ascii_font: str = "Arial", ea_font: str = "微软雅黑"):
    """同时设置西文与东亚字体 —— 只设 font.name 时中文可能回落到不可控字体。"""
    from docx.oxml.ns import qn
    run_obj.font.name = ascii_font
    rpr = run_obj._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), ea_font)


def _inline(paragraph, text: str) -> None:
    """还原 `**加粗**` 与 `` `代码` ``，其余当普通文本。"""
    import re
    for piece in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**") and len(piece) > 4:
            r = paragraph.add_run(piece[2:-2]); r.bold = True
        elif piece.startswith("`") and piece.endswith("`") and len(piece) > 2:
            r = paragraph.add_run(piece[1:-1]); r.font.name = "Menlo"
        else:
            r = paragraph.add_run(piece)
        _style_run(r)


def _bullet(doc, text: str):
    """优先用列表样式；精简文档没有该样式时降级为手写项目符号。"""
    try:
        doc.styles["List Bullet"]
        para = doc.add_paragraph(style="List Bullet")
        _inline(para, text)
    except KeyError:
        para = doc.add_paragraph()
        _inline(para, "• " + text)
    return para


def build_docx_from_md(md: str, dest: str) -> None:
    """把 Markdown 子集转成 docx。

    支持：`#`~`######` 标题、`-`/`*` 无序列表、`1.` 有序列表、`>` 引用、
    `|` 表格、`**加粗**`、`` `代码` ``、`---` 分隔线、空行分段。
    不支持：图片、嵌套列表、脚注 —— 复杂排版请直接给 --docx。
    """
    try:
        from docx import Document
    except ImportError:
        sys.exit("[file_write] 生成 docx 需要 python-docx；请用 --docx 直接给文件，"
                 "或改用它自带的 Python 运行本脚本")

    import re
    doc = Document()
    if doc.styles["Normal"].font.name is None:
        pass  # 沿用模板默认

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # 表格：当前行以 | 开头且下一行是分隔行
        if stripped.startswith("|") and i + 1 < len(lines) and \
                re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in stripped.strip("|").split("|")]
            rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = doc.add_table(rows=1, cols=len(header))
            try:
                table.style = "Table Grid"
            except KeyError:
                pass
            for idx, cell_text in enumerate(header):
                cell = table.cell(0, idx); cell.text = ""
                _inline(cell.paragraphs[0], cell_text)
                for r in cell.paragraphs[0].runs:
                    r.bold = True
            for row in rows:
                cells = table.add_row().cells
                for idx, cell_text in enumerate(row[:len(header)]):
                    cells[idx].text = ""
                    _inline(cells[idx].paragraphs[0], cell_text)
            doc.add_paragraph()
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 4)
            heading = doc.add_heading("", level=level)
            _inline(heading, m.group(2))
            i += 1
            continue

        if re.match(r"^[-*+]\s+", stripped):
            _bullet(doc, re.sub(r"^[-*+]\s+", "", stripped))
            i += 1
            continue

        m = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if m:
            para = doc.add_paragraph()
            _inline(para, f"{m.group(1)}. {m.group(2)}")
            i += 1
            continue

        if stripped.startswith(">"):
            para = doc.add_paragraph()
            _inline(para, stripped.lstrip("> ").strip())
            para.paragraph_format.left_indent = 254000  # 0.25 英寸
            i += 1
            continue

        if re.match(r"^-{3,}$|^\*{3,}$", stripped):
            i += 1
            continue

        para = doc.add_paragraph()
        _inline(para, stripped)
        i += 1

    doc.save(dest)


# ---------------------------------------------------------------- 上传

def backup_current(cli: str, drive_id: str, file_id: str, backup_dir: str, timeout: int,
                   name: str = "") -> str:
    info = json_cmd(cli, ["drive", "file", "download", drive_id, file_id, "--with-hash"],
                    timeout, "取下载地址")
    url = info.get("url")
    if not url:
        sys.exit(f"[file_write] 备份失败：下载响应里没有 url")
    os.makedirs(backup_dir, exist_ok=True)
    # 扩展名要跟原件一致 —— 早期版本硬编码 .docx，把 .xlsx 备份也存成了 .docx
    ext = (os.path.splitext(name or "")[1]
           or os.path.splitext(url.split("?")[0])[1] or ".bin")
    dest = os.path.join(backup_dir, f"backup-{file_id}{ext}")
    req = urllib.request.Request(url, headers={**UA, "Authorization": f"Bearer {auth_token(cli, timeout)}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = resp.read()
    with open(dest, "wb") as fh:
        fh.write(blob)
    return dest


def upload(cli: str, drive_id: str, parent_id: str, file_id: str | None, name: str,
           path: str, on_name_conflict: str | None, timeout: int,
           token_type: str, dry_run: bool) -> None:
    with open(path, "rb") as fh:
        blob = fh.read()
    size = len(blob)
    md5 = hashlib.md5(blob).hexdigest()
    sha256 = hashlib.sha256(blob).hexdigest()

    body: dict = {
        "name": name,
        "size": size,
        # 只能用 md5/sha256 —— sha1 会被 400 拒掉
        "hashes": [{"type": "md5", "sum": md5}, {"type": "sha256", "sum": sha256}],
    }
    if file_id:
        body["file_id"] = file_id
    if on_name_conflict:
        body["on_name_conflict"] = on_name_conflict

    target = f"原地替换 {file_id}" if file_id else f"新建到 parent={parent_id}"
    print(f"上传：{name}  {size} 字节  ({target})")

    data = api(cli, "post", f"/v7/drives/{drive_id}/files/{parent_id}/request_upload",
               timeout, body, token_type)
    upload_id = data.get("upload_id")
    store = data.get("store_request") or {}
    if not upload_id or not store.get("url"):
        sys.exit(f"[file_write] 申请上传的响应缺字段：{data}")
    print(f"① 申请成功 upload_id={upload_id}")

    if dry_run:
        print("dry-run：只做了申请，未 PUT、未 commit —— 服务端不会出现文件。")
        return

    req = urllib.request.Request(store["url"], data=blob, method=store.get("method", "PUT"),
                                 headers={**UA, "Authorization": f"Bearer {auth_token(cli, timeout)}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status not in (200, 201, 204):
            sys.exit(f"[file_write] PUT 失败：HTTP {resp.status}")
    print("② PUT 成功")

    committed = api(cli, "post", f"/v7/drives/{drive_id}/files/{parent_id}/commit_upload",
                    timeout, {"upload_id": upload_id}, token_type)
    print("③ commit 成功")
    for key, label in (("id", "file_id"), ("name", "name"), ("version", "version"),
                       ("size", "size"), ("link_url", "link_url")):
        if committed.get(key) is not None:
            print(f"   {label:<9} = {committed.get(key)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="把本地文件写回 WPS 365（原地替换或新建）")
    ap.add_argument("--drive-id", required=True, help="目标盘 drive_id")
    ap.add_argument("--file-id", help="要原地替换的 file_id；省略则新建文件")
    ap.add_argument("--parent-id", default="0", help="新建时的父目录 id（0 = 盘根目录）")
    ap.add_argument("--file", "--docx", dest="src_file",
                    help="本地文件路径（字节透传，不限 .docx；与 --md 二选一）")
    ap.add_argument("--md", help="Markdown 文件路径，脚本会先生成 docx 再上传")
    ap.add_argument("--name", help="云端文件名（默认取本地文件名）")
    ap.add_argument("--on-name-conflict", choices=["fail", "rename", "overwrite", "replace"],
                    help="重名策略（新建时建议 rename；原地替换一般不需要）")
    ap.add_argument("--backup-dir", help="替换前把当前版本下载到该目录（强烈建议）")
    ap.add_argument("--expect-hash", help="基线哈希：云端当前哈希与之不符则放弃更新（冲突）")
    ap.add_argument("--expect-version", type=int,
                    help="基线版本号：云端当前版本与之不符则放弃更新（冲突）")
    ap.add_argument("--force", action="store_true", help="忽略冲突检查，强制覆盖")
    ap.add_argument("--print-state", action="store_true",
                    help="只打印云端当前 version/hash 后退出（用于取基线）")
    ap.add_argument("--cli", default=DEFAULT_CLI, help=f"wps365-cli 路径（默认 {DEFAULT_CLI}）")
    ap.add_argument("--timeout", type=int, default=180, help="单次 HTTP 超时秒数")
    ap.add_argument("--token-type", default="delegated",
                    help="delegated（默认）或 app；PUT 也需要同一身份")
    ap.add_argument("--dry-run", action="store_true",
                    help="只申请上传，不 PUT 不提交 —— 不产生任何文件")
    args = ap.parse_args()

    if not args.src_file and not args.md and not args.print_state:
        ap.error("需要 --file 或 --md")
    if not os.path.exists(args.cli):
        sys.exit(f"[file_write] 找不到 CLI：{args.cli}")

    if args.print_state:
        if not args.file_id:
            ap.error("--print-state 需要 --file-id")
        state = current_state(args.cli, args.drive_id, args.file_id, args.timeout)
        print(f"version={state['version']}  hash={state['hash']}"
              f"  size={state['size']}  最后修改={state.get('modified_by')}")
        return

    # 准备要上传的本地文件
    if args.src_file:
        src, name = args.src_file, (args.name or os.path.basename(args.src_file))
        if not os.path.exists(src):
            sys.exit(f"[file_write] 找不到 {src}")
    else:
        if not os.path.exists(args.md):
            sys.exit(f"[file_write] 找不到 {args.md}")
        src = os.path.splitext(args.md)[0] + ".docx"
        with open(args.md, encoding="utf-8") as fh:
            md = fh.read()
        build_docx_from_md(md, src)
        name = args.name or os.path.basename(src)
        print(f"已由 Markdown 生成 {src}（{os.path.getsize(src)} 字节）")

    # 更新前比对：云端若已被他人改动，放弃更新并提示冲突
    if args.file_id:
        check_conflict(args.cli, args.drive_id, args.file_id, args.timeout,
                       args.expect_hash, args.expect_version, args.force)

    if args.file_id and args.backup_dir:
        dest = backup_current(args.cli, args.drive_id, args.file_id, args.backup_dir,
                              args.timeout, name)
        print(f"已备份当前版本到 {dest}")

    upload(args.cli, args.drive_id, args.parent_id, args.file_id, name, src,
           args.on_name_conflict, args.timeout, args.token_type, args.dry_run)


if __name__ == "__main__":
    main()
