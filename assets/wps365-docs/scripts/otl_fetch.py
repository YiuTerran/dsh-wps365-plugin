#!/usr/bin/env python3
"""读取 WPS 365 智能文档（.otl）—— export_to_json 路线。

一次调用拿到正文模型 + 附件下载地址，本地解码后产出：
  <out>/doc.md         正文 Markdown 还原（标题/段落/列表/引用/表格/图片）
  <out>/img/*.png      全部附件（含表格单元格内嵌的图、processon 图）
  <out>/otl.model.json 解码后的 OTL 原始模型（需要精确字段时才看）

关键点：
  * base64 只在本地解码，绝不进模型上下文
  * 附件节点递归收集（表格单元格里也可能有图，只扫顶层会漏）
  * 图片从 download_url 直达磁盘，不经上下文
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from collections import Counter

DEFAULT_CLI = os.path.expanduser("~/.local/bin/wps365-cli")
UA = {"User-Agent": "Mozilla/5.0"}

# 导出模型里 codeBlock 的 lang 是编辑器内部数字枚举，与 blocks API 的字符串枚举
# （plaintext/bash/go/...）编号不一致 —— 25 并不是那个列表里的 matlab。
# 25 = bash 由「写入 ```bash 再回读」实测确认；其余取值不猜。
CODE_LANG = {25: "bash"}


# ---------------------------------------------------------------- 数据获取

def run_export(cli: str, file_id: str, timeout: int) -> dict:
    """调用 CLI 导出 JSON（一次调用直接返回内容，无需轮询任务）。"""
    cmd = [cli, "airpage", "export", "create", file_id, "--format", "json", "-o", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:600]
        sys.exit(f"[otl_fetch] 导出失败（退出码 {proc.returncode}）：{detail}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        sys.exit(f"[otl_fetch] 导出结果不是 JSON：{exc}")


def extract(payload: dict) -> tuple[dict, list[dict]]:
    """拆出 OTL 文档模型与附件表。"""
    if payload.get("code") not in (0, None):
        sys.exit(f"[otl_fetch] 接口报错：{payload.get('msg') or payload}")
    data = payload.get("data") or {}
    content = data.get("content")
    if not content:
        sys.exit("[otl_fetch] 响应里没有 data.content")
    try:
        decoded = json.loads(base64.b64decode(content))
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"[otl_fetch] content 解码失败：{exc}")
    model = decoded.get("data", decoded)
    return model, list(data.get("attachment_list") or [])


# ---------------------------------------------------------------- 附件收集

def collect_attachments(model: dict) -> list[dict]:
    """递归收集所有带 attrs.sourceKey 的节点（含表格单元格内嵌的）。

    只扫 mainBody 顶层会漏掉藏在 tableCell 里的图——实测某文档 77 个附件
    里有 6 个只在表格单元格内，顶层扫描完全看不到。
    """
    items: list[dict] = []

    def walk(node, path: str, in_table: bool) -> None:
        if isinstance(node, dict):
            kind = node.get("type")
            attrs = node.get("attrs") or {}
            key = attrs.get("sourceKey")
            if key:
                items.append({
                    "key": str(key),
                    "kind": kind,
                    "attrs": attrs,
                    "path": path,
                    "in_table": in_table,
                })
            children = node.get("content")
            if isinstance(children, list):
                for i, child in enumerate(children):
                    walk(child, f"{path}/{kind}[{i}]", in_table or kind == "tableCell")
        elif isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, f"{path}[{i}]", in_table)

    for i, node in enumerate(model.get("mainBody") or []):
        walk(node, f"mainBody[{i}]", False)
    return items


def guess_ext(blob: bytes) -> str:
    if blob.startswith(b"\x89PNG"):
        return ".png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp"
    if blob[:3] == b"GIF":
        return ".gif"
    return ".bin"


def download(url: str, dest_dir: str, stem: str, timeout: int) -> tuple[str, int]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        blob = resp.read()
    path = os.path.join(dest_dir, stem + guess_ext(blob))
    with open(path, "wb") as fh:
        fh.write(blob)
    return path, len(blob)


# ---------------------------------------------------------------- 渲染

def render_runs(runs) -> str:
    """把 content 里的富文本片段还原成 Markdown 行内语法。

    相邻且样式相同的片段必须先合并再包标记：转换器常把一段加粗拆成多个元素，
    逐个包 `**` 会渲染出 `**A****B**` 这种破碎结果。
    """
    groups: list[dict] = []
    for el in runs or []:
        if not isinstance(el, dict):
            continue
        kind = el.get("type")
        if kind == "br":
            groups.append({"br": True})
            continue
        if kind != "text":
            continue
        attrs = el.get("attrs") or {}
        link = attrs.get("link") if isinstance(attrs.get("link"), dict) else None
        sig = (bool(attrs.get("bold")), bool(attrs.get("code")), (link or {}).get("href", ""))
        text = str(el.get("content", ""))
        if groups and not groups[-1].get("br") and groups[-1].get("sig") == sig:
            groups[-1]["text"] += text
        else:
            groups.append({"sig": sig, "text": text, "link": link})

    parts: list[str] = []
    bold_open = False
    for g in groups:
        if g.get("br"):
            if bold_open:
                parts.append("**")
                bold_open = False
            parts.append("<br>")
            continue
        bold, code, _href = g["sig"]
        # 加粗按「区间」开闭，而不是逐元素包：否则加粗区间内的代码片段会把
        # 标记切开，产出 `**应用 ID → ****`client_id`**` 这种破碎结果。
        if bold and not bold_open:
            parts.append("**")
            bold_open = True
        elif not bold and bold_open:
            parts.append("**")
            bold_open = False
        text = g["text"]
        if code and text.strip():
            text = f"`{text}`"
        link = g.get("link")
        if link and link.get("href"):
            title = link.get("hrefTitle") or ""
            suffix = f' "{title}"' if title else ""
            text = f"[{text}]({link['href']}{suffix})"
        parts.append(text)
    if bold_open:
        parts.append("**")
    return "".join(parts)


def cell_inline(cell: dict, img_no: dict[str, int]) -> str:
    """渲染一个表格单元格：段落用 <br> 连接，内嵌附件降级成 [图N]。"""
    chunks: list[str] = []
    for para in cell.get("content") or []:
        if not isinstance(para, dict):
            continue
        if (para.get("attrs") or {}).get("sourceKey"):
            no = img_no.get(str(para["attrs"]["sourceKey"]))
            chunks.append(f"[图{no}]" if no else "[图?]")
            continue
        text = render_runs(para.get("content"))
        if text.strip():
            chunks.append(text.strip())
    joined = "<br>".join(chunks)
    return joined.replace("|", "\\|").replace("\n", " ")


def render_table(node: dict, img_no: dict[str, int]) -> list[str]:
    """把 table 节点渲染成 Markdown 表格。"""
    rows = [r for r in (node.get("content") or []) if isinstance(r, dict)]
    if not rows:
        return []
    out: list[str] = []
    width = 0
    for r_i, row in enumerate(rows):
        cells = [c for c in (row.get("content") or []) if isinstance(c, dict)]
        values = [cell_inline(c, img_no) for c in cells]
        if r_i == 0:
            width = max(1, len(values))
            out.append("| " + " | ".join(values) + " |")
            out.append("|" + "|".join([" --- "] * width) + "|")
        else:
            note = ""
            if len(values) < width:
                # 列数少于表头：多为合并/跨列行
                note = " <!-- 跨列行 -->"
                values += [""] * (width - len(values))
            elif len(values) > width:
                values = values[:width]
            out.append("| " + " | ".join(values) + " |" + note)
    return out


def render_markdown(model: dict, img_info: dict[str, dict]) -> str:
    """把 OTL 模型还原成 Markdown。

    分隔空行按「相邻两块的分组关系」决定：连续列表项之间不留空行，其余块之间
    留一个空行。若按当前节点自身追加，列表首项的空行会落进列表内部，把列表
    切成松散列表甚至两个列表。
    """
    title = ""
    for el in model.get("title") or []:
        if isinstance(el, dict) and el.get("type") == "text":
            title += str(el.get("content", ""))

    img_no = {k: v["no"] for k, v in img_info.items()}
    counters: dict[tuple, int] = {}
    blocks: list[tuple[str, bool]] = []  # (块文本, 是否列表项)

    for node in model.get("mainBody") or []:
        if not isinstance(node, dict):
            continue
        kind = node.get("type")
        attrs = node.get("attrs") or {}
        text = render_runs(node.get("content"))
        list_attrs = attrs.get("listAttrs")
        is_list = isinstance(list_attrs, dict)
        chunk: list[str] = []

        if kind == "heading":
            level = max(1, min(6, int(attrs.get("level") or 1)))
            chunk.append("#" * level + " " + text.strip())
        elif kind == "table":
            chunk.extend(render_table(node, img_no))
        elif kind == "codeBlock":
            raw = "\n".join(
                str(e.get("content", ""))
                for e in (node.get("content") or [])
                if isinstance(e, dict) and e.get("type") == "text"
            )
            lang_no = attrs.get("lang")
            lang = CODE_LANG.get(lang_no, "")
            chunk.append("```" + lang)
            chunk.extend(raw.rstrip("\n").split("\n"))
            chunk.append("```")
            if lang_no is not None and not lang:
                chunk.append(f"<!-- codeBlock：lang={lang_no}（内部枚举未映射，按纯文本渲染） -->")
        elif kind == "hr":
            chunk.append("---")
        elif attrs.get("sourceKey"):
            key = str(attrs["sourceKey"])
            info = img_info.get(key)
            no = info["no"] if info else "?"
            rel = info["rel"] if info else ""
            label = "图" if kind in ("picture", "processon") else kind
            chunk.append(f"![{label}{no}]({rel})" if rel else f"[{label}{no} 未下载 sourceKey={key}]")
            size = f"{attrs.get('renderWidth')}x{attrs.get('renderHeight')}"
            chunk.append(f"<!-- {label} {no}：{size} kind={kind} sourceKey={key} -->")
        elif kind == "blockQuote":
            chunk.append("> " + text.strip())
        elif is_list:
            level = int(list_attrs.get("level") or 0)
            indent = "  " * level
            if list_attrs.get("type") == 1:
                chunk.append(f"{indent}- {text.strip()}")
            else:
                serial = list_attrs.get("referenceSerialNum")
                if not serial:
                    k = (list_attrs.get("id"), level)
                    counters[k] = counters.get(k, 0) + 1
                    serial = counters[k]
                chunk.append(f"{indent}{serial}. {text.strip()}")
        else:
            if kind != "paragraph":
                chunk.append(f"<!-- 未支持节点类型：{kind} -->")
            chunk.append(text.rstrip())
        blocks.append(("\n".join(chunk), is_list))

    body: list[str] = []
    total = len(blocks)
    for i, (block_text, is_list) in enumerate(blocks):
        body.append(block_text)
        if i < total - 1 and not (is_list and blocks[i + 1][1]):
            body.append("")

    parts = ([f"<!-- 文档标题：{title} -->", ""] if title else []) + body
    return "\n".join(parts)


# ---------------------------------------------------------------- 主流程

def main() -> None:
    ap = argparse.ArgumentParser(description="读取 WPS 365 智能文档（.otl）正文与图片")
    ap.add_argument("--file-id", help="智能文档 file_id（与 --json 二选一）")
    ap.add_argument("--json", help="复用一份已导出的 JSON 响应文件，不重新调用 API")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--no-images", action="store_true", help="不下载图片")
    ap.add_argument("--print-text", action="store_true", help="把 doc.md 全文打到 stdout（长文档慎用）")
    ap.add_argument("--cli", default=DEFAULT_CLI, help=f"wps365-cli 路径（默认 {DEFAULT_CLI}）")
    ap.add_argument("--timeout", type=int, default=180, help="单次 HTTP 超时秒数")
    args = ap.parse_args()

    if not args.file_id and not args.json:
        ap.error("需要 --file-id 或 --json")

    if args.json:
        with open(args.json, encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        if not os.path.exists(args.cli):
            sys.exit(f"[otl_fetch] 找不到 CLI：{args.cli}")
        payload = run_export(args.cli, args.file_id, args.timeout)

    model, attachments = extract(payload)
    urls = {str(a.get("id")): a.get("download_url") for a in attachments if a.get("id")}

    nodes = collect_attachments(model)
    out_dir = os.path.abspath(args.out)
    img_dir = os.path.join(out_dir, "img")
    os.makedirs(img_dir, exist_ok=True)

    img_info: dict[str, dict] = {}
    for item in nodes:
        key = item["key"]
        if key in img_info:
            continue
        no = len(img_info) + 1
        attrs = item["attrs"]
        info = {
            "no": no,
            "key": key,
            "kind": item["kind"],
            "size": f"{attrs.get('renderWidth')}x{attrs.get('renderHeight')}",
            "path": item["path"],
            "in_table": item["in_table"],
            "rel": "",
            "bytes": 0,
            "error": "",
        }
        img_info[key] = info
        if args.no_images:
            continue
        url = urls.get(key)
        if not url:
            info["error"] = "附件表中没有该 sourceKey"
            continue
        stem = f"{no:04d}_{key}"
        try:
            path, nbytes = download(url, img_dir, stem, args.timeout)
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"下载失败：{exc}"
            continue
        info["rel"] = os.path.relpath(path, out_dir)
        info["bytes"] = nbytes

    md = render_markdown(model, img_info)
    md_path = os.path.join(out_dir, "doc.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)
    model_path = os.path.join(out_dir, "otl.model.json")
    with open(model_path, "w", encoding="utf-8") as fh:
        json.dump(model, fh, ensure_ascii=False, indent=1)

    kinds = Counter(
        n.get("type") for n in (model.get("mainBody") or []) if isinstance(n, dict)
    )
    got = sum(1 for v in img_info.values() if v["rel"])
    nested = sum(1 for v in img_info.values() if v["in_table"])
    title = "".join(
        str(e.get("content", ""))
        for e in (model.get("title") or [])
        if isinstance(e, dict) and e.get("type") == "text"
    )

    print(f"文档：{title or '(无标题)'}")
    print(f"节点：{sum(kinds.values())}（" + " / ".join(f"{k} {v}" for k, v in kinds.most_common()) + "）")
    print(f"附件：{len(img_info)} 个唯一 sourceKey（已下载 {got}）"
          + (f"，其中 {nested} 个内嵌在表格单元格里" if nested else ""))
    for info in sorted(img_info.values(), key=lambda v: v["no"]):
        mark = "↳" if info["in_table"] else " "
        tail = info["rel"] if info["rel"] else f"⚠️ {info['error'] or '未下载'}"
        print(f"  {mark}图{info['no']:<3} {info['kind']:<10} {info['size']:>13}  {info['path']}  "
              f"key={info['key']}")
        print(f"        → {tail}")
    print("产物：")
    print(f"  {md_path}  ({os.path.getsize(md_path)} 字节 —— 用 read 分页读取)")
    print(f"  {model_path}")
    print(f"  {img_dir}/")
    if args.print_text:
        print("\n" + "=" * 60 + "\n")
        print(md)


if __name__ == "__main__":
    main()
