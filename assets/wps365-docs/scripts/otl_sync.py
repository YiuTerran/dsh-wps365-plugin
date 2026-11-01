#!/usr/bin/env python3
"""把一整个目录的 Markdown 批量同步成 WPS 365 智能文档（一份 md → 一个 .otl）。

用在哪：一叠标准/规范/接口文档放在同一个目录里，要一次性放到云盘某个文件夹下，
每份一个智能文档，图还得是本地上传的 —— 这种活儿逐份手跑 otl_write.py 会很慢，
这里把「建文档 → 传图 → 转块 → 分批写入 → 复核」串起来，并保留用于安全恢复的检查点。

    python otl_sync.py --md-dir ./protocol/markdown \\
        --drive-id <盘> --parent-id <目标文件夹>

约定：
  * 文档名 = markdown 文件名去掉 .md（同名文档按 --existing 处理）
  * Front matter（`---` 包起来的 YAML）会被摘掉，改写成文首一行「来源/页数/转换日期」
  * 图片按 markdown 里的出现顺序对应 convert 出的 picture 块，逐张上传成附件
  * 单表 >100 行自动拆表（服务端 maxItems=100），表头会重复
  * 状态按云盘、目标目录和源目录隔离；中断后核对检查点并重建未完成文档，不逐块续写
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile

from _wps_cli import resolve as _resolve_cli
from _wps_cli import use_utf8_stdio as _use_utf8_stdio
from otl_write import (api, body_fingerprint, commit_document, doc_children,
                       ensure_title, image_plan, prepare_markdown, strip_front_matter)

DEFAULT_CLI = _resolve_cli()
_LABEL = "otl_sync"
_use_utf8_stdio()
#: 真正的命令行调用在 otl_write.api 里（走 _wps_cli.run_cli），这里不再直接碰 subprocess。
SWAP = str.maketrans({"/": "-", "\\": "-", "*": "", "?": ""})


def safe_name(stem: str) -> str:
    """云端文件名里不能有路径分隔符与 * ?；中文标点（含全角冒号）原样保留。"""
    return stem.translate(SWAP).strip()


def list_folder(cli: str, timeout: int, drive_id: str, parent_id: str) -> dict[str, dict]:
    """目标目录下的文件（按名字索引），用于判断「已存在就跳过/覆盖」。"""
    found: dict[str, dict] = {}
    page_token = ""
    while True:
        query = {"page_size": "100"}
        if page_token:
            query["page_token"] = page_token
        data = api(cli, "get", f"/v7/drives/{drive_id}/files/{parent_id}/children",
                   None, timeout, query=query)
        for item in data.get("items") or []:
            name = item.get("fname") or item.get("name") or ""
            if name:
                found[name] = item
        page_token = data.get("next_page_token") or ""
        if not page_token:
            return found


def meta_blockquote(meta: dict) -> str:
    bits = []
    if meta.get("source"):
        bits.append(f"来源文件：{meta['source']}")
    if meta.get("pages"):
        bits.append(f"页数：{meta['pages']}")
    if meta.get("converted"):
        bits.append(f"转换日期：{meta['converted']}")
    return ("> " + "｜".join(bits) + "\n\n") if bits else ""


def state_target(drive_id: str, parent_id: str, md_dir: str) -> dict:
    return {"drive_id": drive_id, "parent_id": parent_id,
            "md_dir": os.path.realpath(md_dir)}


def default_state_path(target: dict) -> str:
    key = hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()[:24]
    return os.path.join(tempfile.gettempdir(), f"otl-sync-{key}.json")


def load_state(path: str, target: dict) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
        if (not isinstance(state, dict) or state.get("version") != 1
                or state.get("target") != target or not isinstance(state.get("documents"), dict)):
            sys.exit("[otl_sync] 状态文件版本未知或目标不匹配；请保留旧状态，指定新的 --state 文件")
        return state
    return {"version": 1, "target": target, "documents": {}}


def save_state(path: str, state: dict) -> None:
    # A crash must not truncate the last usable checkpoint.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=os.path.dirname(os.path.abspath(path)),
                                         encoding="utf-8", delete=False) as fh:
            temporary = fh.name
            json.dump(state, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def create_doc(cli: str, timeout: int, drive_id: str, parent_id: str, name: str) -> str:
    data = api(cli, "post", "/v7/airpage/files",
               {"drive_id": drive_id, "parent_id": parent_id, "name": name,
                "on_name_conflict": "rename", "template_id": ""}, timeout)
    if not data.get("id"):
        sys.exit(f"[otl_sync] 建文档失败：{json.dumps(data, ensure_ascii=False)[:300]}")
    return data["id"]


def main() -> None:
    ap = argparse.ArgumentParser(description="把目录下的 Markdown 批量同步成智能文档")
    ap.add_argument("--md-dir", required=True, help="Markdown 目录（含图片子目录）")
    ap.add_argument("--drive-id", required=True, help="目标盘 drive_id")
    ap.add_argument("--parent-id", required=True, help="目标文件夹 id（根目录为 0）")
    ap.add_argument("--pattern", default="*.md", help="文件名匹配（默认 *.md）")
    ap.add_argument("--only", help="只同步名字包含该子串的文件（逗号分隔可多个）")
    ap.add_argument("--existing", choices=["skip", "replace", "fail"], default="skip",
                    help="目标目录已有同名文档时：skip 跳过（默认）/ replace 清空重写 / fail 报错")
    ap.add_argument("--state", help="恢复状态文件（默认按云盘、文件夹和本地源目录隔离）")
    ap.add_argument("--retitle", action="store_true",
                    help="标题已有内容也覆盖（默认只在标题为空时写）")
    ap.add_argument("--images", choices=["auto", "off"], default="auto",
                    help="auto：本地上传图片并回填（默认）；off：不处理图片")
    ap.add_argument("--img-width", type=int, default=86, help="图片渲染宽度百分比（默认 86）")
    ap.add_argument("--chunk", type=int, default=200, help="每批写入块数（默认 200）")
    ap.add_argument("--cli", default=DEFAULT_CLI, help=f"wps365-cli 路径（默认 {DEFAULT_CLI}）")
    ap.add_argument("--timeout", type=int, default=180, help="单次 HTTP 超时秒数")
    ap.add_argument("--dry-run", action="store_true", help="只转换、建文档前就停（不上传不写入）")
    args = ap.parse_args()

    md_dir = os.path.abspath(args.md_dir)
    if not os.path.isdir(md_dir):
        sys.exit(f"[otl_sync] 目录不存在：{md_dir}")
    pattern = re.compile("^" + re.escape(args.pattern).replace("\\*", ".*") + "$")
    names = sorted(n for n in os.listdir(md_dir)
                   if pattern.match(n) and os.path.isfile(os.path.join(md_dir, n)))
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        names = [n for n in names if any(w in n for w in wanted)]
    if not names:
        sys.exit(f"[otl_sync] {md_dir} 下没有匹配 {args.pattern} 的文件")

    doc_names = [safe_name(os.path.splitext(name)[0]) + ".otl" for name in names]
    if len(set(doc_names)) != len(doc_names):
        sys.exit("[otl_sync] 多个本地文件映射到同一文档名，已停止同步")
    target = state_target(args.drive_id, args.parent_id, md_dir)
    state_path = args.state or default_state_path(target)
    state = load_state(state_path, target)
    folder = list_folder(args.cli, args.timeout, args.drive_id, args.parent_id)
    print(f"目标目录已有 {len(folder)} 个文件；待同步 {len(names)} 份 → "
          f"drive={args.drive_id} parent={args.parent_id}")
    print(f"状态文件：{state_path}")

    rows = []
    for filename in names:
        stem = os.path.splitext(filename)[0]
        doc_name = safe_name(stem) + ".otl"
        with open(os.path.join(md_dir, filename), encoding="utf-8") as fh:
            body, meta = strip_front_matter(fh.read())
        markdown = meta_blockquote(meta) + body
        entry = state["documents"].setdefault(doc_name, {})
        print(f"\n=== {doc_name} ===")

        if args.dry_run:
            plan = image_plan(markdown, md_dir) if args.images != "off" else []
            local = sum(1 for item in plan if item["local"])
            remote = len(plan) - local
            print(f"  正文 {len(markdown)} 字符；图片 {len(plan)} 张（本地 {local}"
                  + (f"，远程 {remote}" if remote else "") + "）")
            rows.append((doc_name, "?", local, "-", "dry-run"))
            continue

        existing = folder.get(doc_name)
        if existing and entry.get("file_id") and entry["file_id"] != existing.get("id"):
            entry.clear()  # 目录里的是另一个文档，别把状态套错
        recovering = (existing and entry.get("file_id") == existing.get("id")
                      and entry.get("status") in ("pending", "writing"))
        if existing:
            if args.existing == "skip" and not recovering:
                print(f"  已存在，跳过（{existing.get('id')}）")
                rows.append((doc_name, entry.get("total"), entry.get("images"),
                             existing.get("link_url") or "", "跳过"))
                continue
            if args.existing == "fail":
                sys.exit(f"[otl_sync] 目标目录已有同名文档：{doc_name}（换个目录或用 --existing replace）")
            file_id = existing["id"]
            print(f"  {'恢复未完成任务' if recovering else '覆盖已有文档'} {file_id}")
        elif entry.get("file_id"):
            sys.exit("[otl_sync] 状态文档不在目标目录中；请刷新目录并核实文档位置，不会复用缓存 ID")
        else:
            file_id = create_doc(args.cli, args.timeout, args.drive_id, args.parent_id, doc_name)
            print(f"  已创建 {file_id}")
            folder[doc_name] = {"id": file_id}
            entry.update({"file_id": file_id, "status": "pending"})
            save_state(state_path, state)
        entry["file_id"] = file_id
        children = doc_children(args.cli, file_id, args.timeout)
        if (recovering and args.existing != "replace"
                and entry.get("checkpoint") != body_fingerprint(children)):
            sys.exit("[otl_sync] 中断后正文与已保存检查点不一致，可能已提交或被他人编辑；"
                     "请先核对云端内容，确认覆盖后再用 --existing replace")
        entry.update({"status": "pending", "checkpoint": body_fingerprint(children)})
        save_state(state_path, state)

        def checkpoint(current):
            entry.update({"status": "writing", "checkpoint": body_fingerprint(current)})
            save_state(state_path, state)

        cache = entry.setdefault("attachments", {})
        prepared = prepare_markdown(
            args.cli, file_id, markdown, base=md_dir,
            timeout=args.timeout, images=args.images, img_pct=args.img_width,
            image_cache=cache, cache_updated=lambda: save_state(state_path, state))
        stats = commit_document(
            args.cli, file_id, prepared, mode="replace", baseline=children,
            timeout=args.timeout, chunk=args.chunk, checkpoint=checkpoint)
        title = meta.get("title") or stem
        action = ensure_title(args.cli, file_id, title, args.timeout, overwrite=args.retitle)
        if action == "set":
            print(f"  标题：已写入「{title}」")
        elif action == "kept":
            print("  标题：文档已有标题，保留不动（要覆盖加 --retitle）")
        entry.update({"written": stats["written"], "total": stats["blocks"],
                      "images": stats["images"], "title": title,
                      "final_blocks": stats.get("final_blocks"), "status": "complete"})
        save_state(state_path, state)
        info = folder.get(doc_name) or {}
        rows.append((doc_name, stats["blocks"], stats["images"],
                     info.get("link_url") or "", "完成"))

    # 新文档的 link_url 建的时候还没进过目录列表，最后统一补一次
    if not args.dry_run:
        folder = list_folder(args.cli, args.timeout, args.drive_id, args.parent_id)
        rows = [(name, blocks, images, link or (folder.get(name) or {}).get("link_url") or "", status)
                for name, blocks, images, link, status in rows]

    print("\n汇总：")
    print(f"  {'文档':<40}{'块':>6}{'图':>4}  {'状态':<6}链接")
    for name, blocks, images, link, status in rows:
        print(f"  {name[:38]:<40}{str(blocks or '-'):>6}{str(images or '-'):>4}  "
              f"{status:<6}{link}")


if __name__ == "__main__":
    main()
