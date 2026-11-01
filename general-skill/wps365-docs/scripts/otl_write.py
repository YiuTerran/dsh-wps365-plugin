#!/usr/bin/env python3
"""把 Markdown 写进 WPS 365 智能文档（.otl），本地图片一并上传成文档附件。

路线：Markdown --blocks/convert--> 块 --（补图片 id / 拆超长表）--> blocks/create
  POST /v7/airpage/v2/{file_id}/blocks/convert
  POST /v7/coop/files/{file_id}/attachments/upload/{address,complete}   ← 本地图片
  POST /v7/airpage/v2/{file_id}/blocks/create

为什么走 convert 而不是自己拼块：convert 返回的块结构与 blocks/create 期望的
doc_child 是同一个 union，实测可直接透传；自己拼块要维护十几种节点类型。

为什么图片要单独处理：convert 认得 `![](x.png)` / `<img src="x.png">`，但产出的
picture 块 `id` 是空串、尺寸是 300x300 占位；直接把空 id 写进去会被服务端以
`invalid DocChildren.Children[i]` 拒掉（看不出真正原因）。所以这里先在本地把图片
传成附件拿 id，再按出现顺序回填。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request

from _wps_cli import resolve as _resolve_cli
from _wps_cli import run_cli as _run_cli
from _wps_cli import use_utf8_stdio as _use_utf8_stdio

DEFAULT_CLI = _resolve_cli()
_LABEL = "otl_write"
_use_utf8_stdio()
MAX_BLOCKS_PER_CALL = 1000  # 服务端限制：doc_children.children 单次 ≤1000
MAX_TABLE_ROWS = 100        # 服务端限制：table.rows maxItems=100，超了必须拆表
INLINE_BODY_LIMIT = 60000   # 再大就改走 --file：命令行参数有 ARG_MAX（E2BIG）上限
DOC_WIDTH = 740             # 正文默认宽度（px），86% ≈ 636
DEFAULT_IMG_PCT = 86
_UA = {"User-Agent": "wps365-cli"}
#: `<img src="...">` 与 `![alt](...)`，按出现顺序 —— 与 convert 出的 picture 块一一对应。
IMAGE_RE = re.compile(r'<img[^>]*?\bsrc="([^"]+)"|!\[[^\]]*\]\(([^)\s]+)\)', re.IGNORECASE)


def api(cli: str, method: str, path: str, data: dict | None, timeout: int,
        query: dict | None = None) -> dict:
    # 必须把超时也传给 CLI 本身：它有自己的 HTTP 超时（默认较短），
    # 否则批量写入会在 await headers 阶段被客户端掐断。
    cmd = [cli, "api", method, path, "-o", "json", "--timeout", f"{timeout}s"]
    for key, value in (query or {}).items():
        # 查询串要交给 CLI 的 --query，直接拼进 path 会被当成路径的一部分而 404
        cmd += ["--query", f"{key}={value}"]
    tmp_path = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False)
        if len(body) > INLINE_BODY_LIMIT:
            # 千块级写入的 JSON 有几百 KB，塞进 argv 会 OSError: [Errno 7] Argument list too long
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            try:
                handle.write(body)
                tmp_path = handle.name
            finally:
                handle.close()
            cmd += ["--file", tmp_path]
        else:
            cmd += ["--data", body]
    try:
        stdout = _run_cli(cmd, timeout, _LABEL, f"{method.upper()} {path}")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        sys.exit(f"[otl_write] {method.upper()} {path} 返回不是 JSON：{exc}")
    if payload.get("code") not in (0, None):
        sys.exit(f"[otl_write] {method.upper()} {path} 报错：{payload.get('msg') or payload}")
    return payload.get("data") or {}


def block_children(data: dict, block_id: str = "doc") -> list:
    """从 blocks 查询响应里取出目标块的子块列表。

    响应结构是 data.block[<block_id>]，块以 block_id 为键存放 ——
    按 data.block.children 取会永远得到空列表，进而让 replace 变成静默追加。
    """
    block = data.get("block") or {}
    node = block.get(block_id)
    if node is None and len(block) == 1:  # 兜底：单键即目标块
        node = next(iter(block.values()))
    return (node or {}).get("children") or []


def doc_children(cli: str, file_id: str, timeout: int) -> list:
    """文档根下的一级子块列表。"""
    data = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks", {"block_id": "doc"}, timeout)
    return block_children(data, "doc")


def doc_children_count(cli: str, file_id: str, timeout: int) -> int:
    """取文档根下的一级子块数量。"""
    return len(doc_children(cli, file_id, timeout))


def strip_front_matter(md: str) -> tuple[str, dict]:
    """摘掉文首的 YAML front matter（返回 正文, 元数据）。

    直接把 `---` 包起来的 YAML 喂给 convert，会变成一条水平线加一整行 YAML 文本。
    """
    meta: dict[str, str] = {}
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end != -1:
            for line in md[3:end].strip().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
            md = md[end + 4:]
    return md.lstrip("\n"), meta


def resolve_title(explicit: str | None, meta_title: str | None,
                  md_path: str | None) -> str:
    """决定文档标题：显式文本 > front matter 的 title > md 文件名。

    新建的智能文档**标题块是空的**（只有文件名），所以写正文时顺手补上，
    否则文档顶部一直是「输入标题」占位。
    """
    if explicit and explicit not in ("auto", "none"):
        return explicit.strip()
    if explicit == "none":
        return ""
    if meta_title:
        return meta_title.strip()
    if md_path:
        return os.path.splitext(os.path.basename(md_path))[0].strip()
    return ""


def doc_title(cli: str, file_id: str, timeout: int) -> str:
    """读文档标题块（`title`）的纯文本。"""
    data = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks", {"block_id": "doc"}, timeout)
    node = ((data.get("block") or {}).get("doc") or {}).get("title") or {}
    return "".join(str(((el or {}).get("text") or {}).get("attributes", {}).get("content") or "")
                   for el in (node.get("elements") or []))


def set_title(cli: str, file_id: str, title: str, timeout: int) -> None:
    """写标题块。`update_elements` 要按块类型再嵌一层，标题是 `title_elements`。"""
    api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks/update",
        {"block_id": "title",
         "update_elements": {"title_elements": {"elements": [
             {"text": {"attributes": {"content": title}, "range_marks": []}}]}}}, timeout)


def ensure_title(cli: str, file_id: str, title: str, timeout: int,
                 overwrite: bool = False) -> str:
    """标题为空才写；已有别的标题时默认不动（可能是别人改过的）。返回动作名。"""
    current = doc_title(cli, file_id, timeout)
    if not title or current == title:
        return "unchanged"
    if current and not overwrite:
        return "kept"
    set_title(cli, file_id, title, timeout)
    return "set"


def is_blank_paragraph(block: dict) -> bool:
    """新建的空文档自带一个没有 elements 的段落；它占着索引，写之前要清掉。"""
    if not isinstance(block, dict) or next(iter(block), "") != "paragraph":
        return False
    return not ((block.get("paragraph") or {}).get("elements") or [])


# ---------------------------------------------------------------- 并发冲突检查

def _block_id(block: dict) -> str:
    """块是单键 union（{"heading": {...}}），block_id 在内层。"""
    inner = next(iter(block.values())) if isinstance(block, dict) and block else {}
    return str((inner or {}).get("block_id") or "")


def snapshot(cli: str, file_id: str, timeout: int,
             scope_ids: list[str] | None = None,
             scope_index: list[int] | None = None) -> dict:
    """记录每个一级块的哈希，并声明「本次改动依赖哪些块」。

    局部对比的前提是**自己知道要改哪几个块**：`blocks` 存全文（用于整体判断），
    `scope` 存本次真正依赖/会覆盖的块。写入前只校验 scope 内的块是否变化 ——
    别人改了无关的块不该挡住我。普通文件没有块结构，只能比整文件哈希
    （见 file_write.py）。
    """
    data = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks", {"block_id": "doc"}, timeout)
    children = block_children(data, "doc")
    want_ids = set(scope_ids or [])
    want_idx = set(scope_index or [])
    blocks, scope = [], []
    for i, block in enumerate(children):
        bid = _block_id(block)
        blob = json.dumps(block, sort_keys=True, ensure_ascii=False).encode()
        blocks.append({"block_id": bid, "hash": hashlib.sha1(blob).hexdigest()})
        if bid in want_ids or i in want_idx:
            scope.append(bid)
    return {"file_id": file_id, "version": data.get("version"),
            "scope_explicit": scope_ids is not None or scope_index is not None,
            "scope": scope, "blocks": blocks}


def diff_maps(old_map: dict, new_map: dict) -> tuple[list[str], list[str], list[str]]:
    """返回 (新增, 删除, 内容变化) 的 block_id 列表。"""
    added = [i for i in new_map if i not in old_map]
    removed = [i for i in old_map if i not in new_map]
    changed = [i for i in old_map if i in new_map and old_map[i] != new_map[i]]
    return added, removed, changed


def verify_snapshot(cli: str, file_id: str, snap_path: str, mode: str, timeout: int,
                    allow_added: bool, force: bool) -> int:
    """写入前做**局部**对比：只看快照声明依赖的块有没有被改。

    * 声明了 scope：只有 scope 内的块**被改或被删**才算冲突；无关块变了只提示。
    * 没声明 scope：保守处理，全文任何变化都算冲突（`--allow-added` 可放行纯新增）。
    * `mode=replace`：会清空重写，无论 scope 都按全文比对。
    """
    if not os.path.exists(snap_path):
        sys.exit(f"[otl_write] 找不到快照文件 {snap_path}")
    with open(snap_path, encoding="utf-8") as fh:
        old = json.load(fh)
    new = snapshot(cli, file_id, timeout)

    old_full = {b["block_id"]: b["hash"] for b in old.get("blocks") or []}
    new_full = {b["block_id"]: b["hash"] for b in new.get("blocks") or []}
    added, removed, changed = diff_maps(old_full, new_full)

    old_order = [b["block_id"] for b in old.get("blocks") or []]
    new_order = [b["block_id"] for b in new.get("blocks") or []]
    new_ids = set(new_order)
    old_ids = set(old_order)
    old_shared_order = [bid for bid in old_order if bid in new_ids]
    new_shared_order = [bid for bid in new_order if bid in old_ids]
    order_changed = old_shared_order != new_shared_order
    order_changed_ids = ([old_bid for old_bid, new_bid in
                          zip(old_shared_order, new_shared_order) if old_bid != new_bid]
                         if order_changed else [])

    scope = old.get("scope") or []
    explicit = bool(old.get("scope_explicit"))
    sc_changed = [i for i in scope if i in new_full and new_full[i] != old_full.get(i)]
    sc_gone = [i for i in scope if i not in new_full]

    if not (added or removed or changed or order_changed):
        print(f"并发检查：文档未被改动（{len(new_full)} 个一级块，"
              f"version {old.get('version')}→{new.get('version')}）")
        return len(new_full)

    detail = "；".join(
        [f"{len(changed)} 个块内容变化"] * bool(changed)
        + [f"{len(removed)} 个块被删除"] * bool(removed)
        + [f"{len(added)} 个块新增"] * bool(added)
        + ["一级块顺序变化"] * bool(order_changed))

    if mode == "replace":
        conflict, why = True, "replace 会清空重写，按全文比对"
    elif explicit:
        conflict = bool(sc_changed or sc_gone)
        why = f"只比对声明的 {len(scope)} 个依赖块"
    elif allow_added and not changed and not removed and not order_changed:
        conflict, why = False, "仅新增块，未改未删"
    else:
        conflict, why = True, "未声明依赖块，保守按全文比对"

    if not conflict:
        print(f"并发检查：文档整体有变化（{detail}），但{why} —— 不影响本次写入，继续")
        return len(new_full)
    if force:
        print(f"⚠️  已忽略冲突（--force）：{detail}")
        return len(new_full)

    hit = ((sc_changed + sc_gone) if (explicit and mode != "replace")
           else changed + removed + added + order_changed_ids)
    hit = list(dict.fromkeys(hit))
    sys.exit("[otl_write] ⚠️  冲突：本次依赖的块已被他人修改，已放弃写入。\n"
             f"   判定：{why}\n"
             f"   文档整体：{detail}\n"
             f"   受影响：{', '.join(hit[:8])}{' …' if len(hit) > 8 else ''}\n"
             "   处理：先 otl_fetch.py 重新读取、把你的改动合并进去再写；"
             "确认要覆盖请加 --force。")


def block_summary(block: dict) -> str:
    """给块生成一行摘要，便于挑出要声明的依赖块。"""
    kind = next(iter(block)) if isinstance(block, dict) and block else "?"
    inner = next(iter(block.values())) if isinstance(block, dict) and block else {}
    texts = []
    for el in (inner or {}).get("elements") or []:
        for key, val in (el or {}).items():
            if key == "text":
                texts.append(str(((val or {}).get("attributes") or {}).get("content") or ""))
    return f"[{kind}] {''.join(texts).strip().replace(chr(10), ' ')[:52]}"


def list_blocks(cli: str, file_id: str, timeout: int) -> None:
    """列出根下所有一级块：索引、block_id、摘要 —— 用来决定 scope。"""
    data = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks", {"block_id": "doc"}, timeout)
    children = block_children(data, "doc")
    print(f"version={data.get('version')}  一级块 {len(children)} 个")
    for i, block in enumerate(children):
        print(f"  {i:>4}  {_block_id(block):<16} {block_summary(block)}")


# ---------------------------------------------------------------- 本地图片

def image_plan(md: str, base: str) -> list[dict]:
    """按出现顺序列出 Markdown 里引用的图片；本地文件与远程 URL 分开标记。"""
    plan = []
    for match in IMAGE_RE.finditer(md):
        src = (match.group(1) or match.group(2) or "").strip()
        if not src:
            continue
        clean = src.split("?")[0].split("#")[0]
        if clean.startswith(("http://", "https://", "//", "data:")):
            plan.append({"src": src, "path": None, "local": False})
            continue
        path = clean if os.path.isabs(clean) else os.path.join(base, clean)
        plan.append({"src": src, "path": os.path.normpath(path),
                     "local": os.path.exists(os.path.normpath(path))})
    return plan


def image_size(path: str) -> tuple[int, int]:
    """图片原始宽高；picture 块写死要求 width/height > 0。"""
    try:
        from PIL import Image
    except ImportError:
        sys.exit("[otl_write] 需要 Pillow 读图片尺寸；请用带 Pillow 的解释器跑"
                 "（本机资源那节的 {python}），或给 --images off 跳过图片")
    with Image.open(path) as img:
        return img.size


def upload_attachment(cli: str, file_id: str, path: str, timeout: int) -> str:
    """本地文件 → 智能文档附件 → 附件 id（13 位，就是 picture 块的 `id`）。

    三步：申请上传地址 → PUT 字节 → complete。**不能**用
    `/v7/coop/airpage/{file_id}/attachments/upload`（该路由服务端返回
    `method AttachmentsUpload not implemented`）。
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp",
             ".svg": "image/svg+xml", ".bmp": "image/bmp"}.get(ext, "application/octet-stream")
    addr = api(cli, "post", f"/v7/coop/files/{file_id}/attachments/upload/address",
               {"name": name, "size": len(blob), "content_type": ctype,
                "md5": hashlib.md5(blob).hexdigest()}, timeout)
    request = addr.get("request") or {}
    if not request.get("url") or not addr.get("upload_id"):
        sys.exit(f"[otl_write] 申请附件上传地址失败：{json.dumps(addr, ensure_ascii=False)[:300]}")
    req = urllib.request.Request(
        request["url"], data=blob, method=request.get("method", "PUT"),
        headers={**_UA, **(request.get("headers") or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}

    def pick(value):
        """send_back_params 的值形如 `header.ETag` / `header.newfilename`：从 PUT 响应头取。

        不同存储后端的头名不一样（ks3_compatible 是 newfilename，obs 是 x-obs-save-key），
        写死头名会让 complete 报 `params is empty, must contain key and etag`。
        """
        if isinstance(value, str) and value.lower().startswith("header."):
            return headers.get(value.split(".", 1)[1].lower())
        return value

    params = {k: pick(v) for k, v in (addr.get("send_back_params") or {}).items()}
    params.setdefault("etag", headers.get("etag"))
    if not params.get("key"):
        params["key"] = headers.get("x-obs-save-key") or headers.get("newfilename")
    params["etag"] = (params.get("etag") or "").strip('"')
    if not params.get("key"):
        sys.exit(f"[otl_write] 附件上传后取不到 key，PUT 响应头：{sorted(headers)}")
    done = api(cli, "post", f"/v7/coop/files/{file_id}/attachments/upload/complete",
               {"upload_id": addr["upload_id"], "params": params}, timeout)
    attachment_id = (done or {}).get("attachment_id")
    if not attachment_id:
        sys.exit(f"[otl_write] 附件 complete 没返回 attachment_id：{done}")
    return str(attachment_id)


def picture_refs(blocks: list) -> list[tuple[list, int, dict]]:
    """按文档顺序收集 picture 块，连同它所在的列表与下标（便于原地替换）。"""
    refs: list[tuple[list, int, dict]] = []

    def walk(seq: list) -> None:
        for index, block in enumerate(seq):
            if not isinstance(block, dict) or not block:
                continue
            kind = next(iter(block))
            inner = block[kind] or {}
            if kind == "picture":
                refs.append((seq, index, inner))
                continue
            if kind == "picture_column":
                children = inner.get("children") or []
                for position, child in enumerate(children):
                    if isinstance(child, dict) and "picture" in child:
                        refs.append((children, position, child["picture"]))
                continue
            for key in ("children", "columns"):
                for child in inner.get(key) or []:
                    if isinstance(child, dict):
                        walk([child])
            for row in inner.get("rows") or []:
                for cell in row.get("cells") or []:
                    walk(cell.get("children") or [])

    walk(blocks)
    return refs


def placeholder_paragraph(text: str) -> dict:
    return {"paragraph": {"attributes": {"align": "left"},
                          "elements": [{"text": {"attributes": {"content": text},
                                                 "range_marks": []}}],
                          "range_marks": []}}


def fill_pictures(blocks: list, plan: list[dict], img_pct: int, doc_width: int,
                  progress=print) -> int:
    """把上传好的附件 id / 真实尺寸回填到对应的 picture 块。

    convert 出的 picture 块顺序与 Markdown 里 `<img>` / `![]()` 的出现顺序一致，
    所以按位置配对即可；远程图片没有本地字节，替换成一行说明而不是留空 id。
    """
    refs = picture_refs(blocks)
    if len(refs) != len(plan):
        sys.exit(f"[otl_write] 图片块 {len(refs)} 个，但 Markdown 里找到 {len(plan)} 张图 —— "
                 "顺序配对不成立，请检查是否有宏/代码块里的假图片语法")
    for (seq, index, inner), item in zip(refs, plan):
        if not item["local"]:
            seq[index] = placeholder_paragraph(f"〔未上传的远程图片：{item['src']}〕")
            progress(f"  跳过远程图片（无本地字节）：{item['src']}")
            continue
        width = item.get("width") or 0
        height = item.get("height") or 0
        target = min(round(img_pct * doc_width / 100), width) if img_pct else width
        inner["attributes"] = {"align": "center", "border_type": "none", "rotate": 0,
                              "id": item["id"], "width": width, "height": height,
                              "render_width": target}
        inner["range_marks"] = inner.get("range_marks") or []
        inner.pop("block_id", None)
    return len(refs)


# ---------------------------------------------------------------- 超长表格

def split_wide_tables(blocks: list, max_rows: int = MAX_TABLE_ROWS) -> tuple[list, int]:
    """把超过 max_rows 行的表格拆成多张（重复表头）。

    服务端 table.rows 是 maxItems=100，超了写入直接 400；这些标准里的字段清单表
    动辄几百行，只能拆。返回 (新块列表, 拆分出来的额外表格数)。
    """
    out, split = [], 0
    for block in blocks:
        rows = (block.get("table") or {}).get("rows") if isinstance(block, dict) else None
        if not rows or len(rows) <= max_rows:
            out.append(block)
            continue
        head, data = rows[0], rows[1:]
        step = max_rows - 1
        for start in range(0, len(data), step):
            clone = json.loads(json.dumps(block, ensure_ascii=False))
            clone["table"]["rows"] = json.loads(
                json.dumps([head] + data[start:start + step], ensure_ascii=False))
            _strip_block_ids(clone)
            out.append(clone)
            split += 1
        split -= 1  # 第一张不算额外
    return out, split


def _strip_block_ids(obj) -> None:
    """拆分出的副本不能复用原表的 block_id，否则同一批里 id 撞车。"""
    if isinstance(obj, dict):
        obj.pop("block_id", None)
        for value in obj.values():
            _strip_block_ids(value)
    elif isinstance(obj, list):
        for value in obj:
            _strip_block_ids(value)


# ---------------------------------------------------------------- 写入主流程

def file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for piece in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def prepare_markdown(cli: str, file_id: str, md: str, *, base: str = ".",
                     timeout: int = 180, dry_run: bool = False,
                     images: str = "auto", img_pct: int = DEFAULT_IMG_PCT,
                     doc_width: int = DOC_WIDTH, image_cache: dict | None = None,
                     cache_updated=None, progress=print) -> dict:
    """Convert and validate everything before any document body is deleted."""
    plan = image_plan(md, base) if images != "off" else []
    converted = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks/convert",
                    {"format": "markdown", "content": md}, timeout)
    blocks = converted.get("blocks") or []
    if not blocks:
        sys.exit("[otl_write] 转换结果为空，未写入任何内容")

    kinds: dict[str, int] = {}
    for block in blocks:
        key = next(iter(block)) if isinstance(block, dict) and block else "?"
        kinds[key] = kinds.get(key, 0) + 1
    progress(f"转换：Markdown {len(md)} 字符 → {len(blocks)} 个块（"
             + " / ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]))
             + "）")

    uploaded = 0
    pictured = 0
    if images == "off":
        if picture_refs(blocks):
            sys.exit("[otl_write] 文档里有图片块，但 --images off：空 id 的图片块会被服务端拒掉，"
                     "改用 --images auto")
    else:
        if len(picture_refs(blocks)) != len(plan):
            sys.exit("[otl_write] 图片块与 Markdown 图片数量不一致，已停止；未删除正文")
        # Validate every local image before uploading the first attachment.
        for item in plan:
            if item["path"] is not None and not item["local"]:
                sys.exit(f"[otl_write] 本地图片不存在：{item['path']}")
            if item["local"]:
                item["sha256"] = file_digest(item["path"])
                item["width"], item["height"] = image_size(item["path"])
        if plan:
            for item in plan:
                if not item["local"]:
                    continue
                cached = (image_cache or {}).get(item["src"])
                if (cached and not dry_run and cached.get("file_id") == file_id
                        and cached.get("sha256") == item["sha256"] and cached.get("id")):
                    item.update({"id": cached["id"], "width": cached["width"],
                                 "height": cached["height"]})
                    continue
                if dry_run:
                    item["id"] = "dry-run"
                    continue
                item["id"] = upload_attachment(cli, file_id, item["path"], timeout)
                if file_digest(item["path"]) != item["sha256"]:
                    sys.exit(f"[otl_write] 上传期间图片发生变化：{item['path']}；未删除正文")
                uploaded += 1
                if image_cache is not None:
                    image_cache[item["src"]] = {"id": item["id"], "width": item["width"],
                                                "height": item["height"], "file_id": file_id,
                                                "sha256": item["sha256"]}
                    if cache_updated:
                        cache_updated()
                progress(f"  上传图片 {os.path.basename(item['path'])} "
                         f"{item['width']}x{item['height']} → {item['id']}")
            progress(f"图片：{len(plan)} 张（本地上传 {uploaded} 张）")
        fill_pictures(blocks, plan, img_pct, doc_width, progress)
        pictured = len(plan)

    blocks, split = split_wide_tables(blocks)
    if split:
        progress(f"超长表格：按 {MAX_TABLE_ROWS} 行上限拆出 {split} 张（重复表头）")

    return {"file_id": file_id, "children": blocks, "dry_run": dry_run,
            "blocks": len(blocks), "written": 0, "images": pictured,
            "uploaded": uploaded, "split_tables": split}


def body_fingerprint(children: list) -> str:
    return hashlib.sha256(json.dumps(children, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_prepared(cli: str, file_id: str, prepared: dict, *, index: int = 0,
                   timeout: int = 180, chunk: int = 200, checkpoint=None,
                   progress=print) -> dict:
    if prepared["dry_run"] or prepared["file_id"] != file_id:
        raise ValueError("Cannot commit a preview or preparation for another document")
    blocks = prepared["children"]
    before = doc_children_count(cli, file_id, timeout)
    if not 0 <= index <= before:
        sys.exit("[otl_write] 插入位置已失效，已停止写入")

    step = max(1, min(chunk, MAX_BLOCKS_PER_CALL))
    written = 0
    for start in range(0, len(blocks), step):
        piece = blocks[start:start + step]
        api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks/create",
            {"block_id": "doc", "index": index + written,
             "doc_children": {"children": piece}}, timeout)
        written += len(piece)
        progress(f"已写入 {written}/{len(blocks)} 个块")
        current = doc_children(cli, file_id, timeout)
        final = len(current)
        if final < before + written:
            sys.exit(f"[otl_write] 复核不一致：预期至少 {before + written} 个一级块，实际 {final}。"
                     "请先读取云端内容并核对；不要直接重试 append/prepend，以免重复写入。")
        if checkpoint:
            checkpoint(current)
    return {key: value for key, value in prepared.items() if key not in ("children", "dry_run")} | {
        "written": written, "final_blocks": final}


def commit_document(cli: str, file_id: str, prepared: dict, *, mode: str,
                    baseline: list, timeout: int = 180, chunk: int = 200,
                    checkpoint=None, progress=print) -> dict:
    if prepared["dry_run"] or prepared["file_id"] != file_id:
        raise ValueError("Cannot commit a preview or preparation for another document")
    current = doc_children(cli, file_id, timeout)
    if body_fingerprint(current) != body_fingerprint(baseline):
        sys.exit("[otl_write] 准备期间正文发生变化，已放弃写入；请重新读取并合并改动")
    clear = mode == "replace" or (len(current) == 1 and is_blank_paragraph(current[0]))
    if clear and current:
        # Keep the live block tree, including attachments, before destructive work.
        with tempfile.NamedTemporaryFile("w", prefix="otl-backup-", suffix=".json",
                                         encoding="utf-8", delete=False) as fh:
            json.dump({"file_id": file_id, "children": current}, fh, ensure_ascii=False)
            progress(f"正文块备份：{fh.name}")
        api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks/delete",
            {"block_id": "doc", "start_index": 0, "end_index": len(current)}, timeout)
        current = doc_children(cli, file_id, timeout)
        if any(not is_blank_paragraph(block) for block in current):
            sys.exit("[otl_write] 删除后正文仍非空，已停止；请检查云端内容")
        if checkpoint:
            checkpoint(current)
    index = 0 if mode in ("replace", "prepend") else len(current)
    if mode == "append" and current and is_blank_paragraph(current[-1]):
        index -= 1
    return write_prepared(cli, file_id, prepared, index=index, timeout=timeout,
                          chunk=chunk, checkpoint=checkpoint, progress=progress)


def write_markdown(cli: str, file_id: str, md: str, *, base: str = ".",
                   index: int = 0, timeout: int = 180, dry_run: bool = False,
                   images: str = "auto", img_pct: int = DEFAULT_IMG_PCT,
                   doc_width: int = DOC_WIDTH, chunk: int = 200,
                   image_cache: dict | None = None, progress=print) -> dict:
    prepared = prepare_markdown(cli, file_id, md, base=base, timeout=timeout,
                                dry_run=dry_run, images=images, img_pct=img_pct,
                                doc_width=doc_width, image_cache=image_cache, progress=progress)
    if dry_run:
        return {key: value for key, value in prepared.items() if key not in ("children", "dry_run")}
    return write_prepared(cli, file_id, prepared, index=index, timeout=timeout,
                          chunk=chunk, progress=progress)


def main() -> None:
    ap = argparse.ArgumentParser(description="把 Markdown 写入 WPS 365 智能文档")
    ap.add_argument("--file-id", required=True, help="智能文档 file_id")
    ap.add_argument("--md", help="Markdown 文件路径")
    ap.add_argument("--text", help="直接给 Markdown 文本（与 --md 二选一）")
    ap.add_argument("--mode", choices=["append", "replace", "prepend"], default="append",
                    help="append 追加到末尾（默认）/ replace 清空后写入 / prepend 插到最前")
    ap.add_argument("--images", choices=["auto", "off"], default="auto",
                    help="auto：本地图片上传成附件并回填 id（默认）；off：不处理图片")
    ap.add_argument("--img-base", help="图片相对路径的基准目录（默认取 --md 所在目录）")
    ap.add_argument("--img-width", type=int, default=DEFAULT_IMG_PCT,
                    help=f"图片渲染宽度占正文宽度的百分比（默认 {DEFAULT_IMG_PCT}；0 表示用原始宽度）")
    ap.add_argument("--doc-width", type=int, default=DOC_WIDTH,
                    help=f"正文宽度像素（默认 {DOC_WIDTH}）")
    ap.add_argument("--chunk", type=int, default=200, help="每批写入的块数（默认 200）")
    ap.add_argument("--title", default="auto",
                    help="文档标题：auto 取自 front matter 的 title 或 md 文件名（默认）/ none 不写 / 其他值按原文写入")
    ap.add_argument("--retitle", action="store_true", help="标题已有内容也覆盖（默认只在标题为空时写）")
    ap.add_argument("--front-matter", choices=["drop", "keep"], default="drop",
                    help="文首 YAML front matter：drop 丢弃（默认）/ keep 原样交给转换器")
    ap.add_argument("--cli", default=DEFAULT_CLI, help=f"wps365-cli 路径（默认 {DEFAULT_CLI}）")
    ap.add_argument("--timeout", type=int, default=180, help="单次 HTTP 超时秒数")
    ap.add_argument("--dry-run", action="store_true", help="只转换不写入，打印块结构摘要")
    ap.add_argument("--snapshot-out", metavar="PATH",
                    help="只保存当前文档的块级快照后退出（配合 --expect-snapshot 做并发检查）")
    ap.add_argument("--scope-blocks", metavar="ID,ID",
                    help="声明本次改动依赖/会覆盖的块（block_id，逗号分隔）—— 只校验这些块")
    ap.add_argument("--scope-index", metavar="N,N",
                    help="同上，但按一级块索引声明（更省事，见 --list-blocks）")
    ap.add_argument("--list-blocks", action="store_true",
                    help="列出根下所有一级块的索引与 block_id 后退出")
    ap.add_argument("--expect-snapshot", metavar="PATH",
                    help="写入前比对快照：依赖的块被他人改过则放弃写入并提示冲突")
    ap.add_argument("--allow-added", action="store_true",
                    help="仅「新增块」不算冲突（未声明 scope 时的宽松档）")
    ap.add_argument("--force", action="store_true", help="忽略冲突检查，强制写入")
    args = ap.parse_args()

    if args.list_blocks:
        list_blocks(args.cli, args.file_id, args.timeout)
        return

    if args.snapshot_out:
        scope_ids = [s.strip() for s in (args.scope_blocks or "").split(",") if s.strip()]
        scope_idx = [int(s) for s in (args.scope_index or "").split(",") if s.strip()]
        snap = snapshot(args.cli, args.file_id, args.timeout,
                        scope_ids or None, scope_idx or None)
        with open(args.snapshot_out, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, ensure_ascii=False, indent=1)
        note = (f"声明依赖 {len(snap['scope'])} 个块" if snap["scope_explicit"]
                else "未声明依赖块（将保守按全文比对）")
        print(f"已保存快照：{args.snapshot_out}"
              f"（{len(snap['blocks'])} 个一级块，version {snap.get('version')}；{note}）")
        return

    if not args.md and not args.text:
        ap.error("需要 --md 或 --text")
    base = os.getcwd()
    if args.md:
        with open(args.md, encoding="utf-8") as fh:
            md = fh.read()
        base = os.path.dirname(os.path.abspath(args.md))
    else:
        md = args.text
    if args.img_base:
        base = os.path.abspath(args.img_base)
    meta: dict = strip_front_matter(md)[1]
    if args.front_matter == "drop":
        md = strip_front_matter(md)[0]
        if meta:
            print(f"已摘掉 front matter（{len(meta)} 个字段）")

    baseline = doc_children(args.cli, args.file_id, args.timeout)
    prepared = prepare_markdown(
        args.cli, args.file_id, md, base=base, timeout=args.timeout,
        dry_run=args.dry_run, images=args.images, img_pct=args.img_width,
        doc_width=args.doc_width)
    if args.dry_run:
        print("dry-run：未修改正文或标题，未上传图片")
        return

    # Recheck after potentially slow conversion and attachment uploads.
    if args.expect_snapshot:
        verify_snapshot(args.cli, args.file_id, args.expect_snapshot, args.mode,
                        args.timeout, args.allow_added, args.force)
        baseline = doc_children(args.cli, args.file_id, args.timeout)
    stats = commit_document(args.cli, args.file_id, prepared, mode=args.mode,
                            baseline=baseline, timeout=args.timeout, chunk=args.chunk)
    print(f"完成：{args.mode} {stats['written']} 个块到 {args.file_id}"
          f"（图片 {stats['images']} 张，拆表 {stats['split_tables']} 张）")

    title = resolve_title(args.title, meta.get("title"), args.md)
    action = ensure_title(args.cli, args.file_id, title, args.timeout,
                          overwrite=args.retitle)
    if action == "set":
        print(f"标题：已写入「{title}」")
    elif action == "kept":
        print(f"标题：文档已有标题，保留不动（要覆盖加 --retitle）")
    elif title:
        print(f"标题：已是「{title}」")


if __name__ == "__main__":
    main()
