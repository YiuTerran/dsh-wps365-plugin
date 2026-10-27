#!/usr/bin/env python3
"""把 Markdown 写进 WPS 365 智能文档（.otl）。

路线：Markdown --blocks/convert--> 块 --blocks/create--> 文档
  POST /v7/airpage/v2/{file_id}/blocks/convert   {"format":"markdown","content":...}
  POST /v7/airpage/v2/{file_id}/blocks/create    {"block_id":"doc","index":N,"doc_children":{"children":[...]}}

为什么走 convert 而不是自己拼块：convert 返回的块结构与 blocks/create 期望的
doc_child 是同一个 union，实测可直接透传；自己拼块要维护十几种节点类型。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from _wps_cli import resolve as _resolve_cli
from _wps_cli import run_cli as _run_cli
from _wps_cli import use_utf8_stdio as _use_utf8_stdio

DEFAULT_CLI = _resolve_cli()
_LABEL = "otl_write"
_use_utf8_stdio()
MAX_BLOCKS_PER_CALL = 1000  # 服务端限制：doc_children.children 单次 ≤1000


def api(cli: str, method: str, path: str, data: dict | None, timeout: int) -> dict:
    # 必须把超时也传给 CLI 本身：它有自己的 HTTP 超时（默认较短），
    # 否则批量写入会在 await headers 阶段被客户端掐断。
    cmd = [cli, "api", method, path, "-o", "json", "--timeout", f"{timeout}s"]
    if data is not None:
        cmd += ["--data", json.dumps(data, ensure_ascii=False)]
    stdout = _run_cli(cmd, timeout, _LABEL, f"{method.upper()} {path}")
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


def doc_children_count(cli: str, file_id: str, timeout: int) -> int:
    """取文档根下的一级子块数量。"""
    data = api(cli, "post", f"/v7/airpage/v2/{file_id}/blocks", {"block_id": "doc"}, timeout)
    return len(block_children(data, "doc"))


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


def main() -> None:
    ap = argparse.ArgumentParser(description="把 Markdown 写入 WPS 365 智能文档")
    ap.add_argument("--file-id", required=True, help="智能文档 file_id")
    ap.add_argument("--md", help="Markdown 文件路径")
    ap.add_argument("--text", help="直接给 Markdown 文本（与 --md 二选一）")
    ap.add_argument("--mode", choices=["append", "replace", "prepend"], default="append",
                    help="append 追加到末尾（默认）/ replace 清空后写入 / prepend 插到最前")
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
    if args.md:
        with open(args.md, encoding="utf-8") as fh:
            md = fh.read()
    else:
        md = args.text

    # 1. Markdown → 块
    converted = api(args.cli, "post", f"/v7/airpage/v2/{args.file_id}/blocks/convert",
                    {"format": "markdown", "content": md}, args.timeout)
    blocks = converted.get("blocks") or []
    if not blocks:
        sys.exit("[otl_write] 转换结果为空，未写入任何内容")

    kinds: dict[str, int] = {}
    for b in blocks:
        # 块是单键 union：{"heading": {...}} / {"paragraph": {...}} / {"table": {...}}
        key = next(iter(b)) if isinstance(b, dict) and b else "?"
        kinds[key] = kinds.get(key, 0) + 1
    print(f"转换：Markdown {len(md)} 字符 → {len(blocks)} 个块（"
          + " / ".join(f"{k} {v}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])) + "）")

    # 并发检查：写入前比对快照，文档被他人改过就放弃（.otl 能做块级局部对比）
    verified = None
    if args.expect_snapshot:
        verified = verify_snapshot(args.cli, args.file_id, args.expect_snapshot, args.mode,
                                   args.timeout, args.allow_added, args.force)

    existing = verified if verified is not None else doc_children_count(
        args.cli, args.file_id, args.timeout)
    print(f"文档：根下现有 {existing} 个一级块")

    if args.dry_run:
        print("dry-run：未写入。首块预览：")
        print(json.dumps(blocks[:2], ensure_ascii=False)[:800])
        return

    # 2. 按模式处理已有内容
    if args.mode == "replace" and existing:
        api(args.cli, "post", f"/v7/airpage/v2/{args.file_id}/blocks/delete",
            {"block_id": "doc", "start_index": 0, "end_index": existing}, args.timeout)
        print(f"已删除原有 {existing} 个一级块")
        index = 0
    elif args.mode == "prepend":
        index = 0
    else:
        index = existing

    # 3. 分批写入（单次 ≤1000 块）
    written = 0
    for start in range(0, len(blocks), MAX_BLOCKS_PER_CALL):
        chunk = blocks[start:start + MAX_BLOCKS_PER_CALL]
        api(args.cli, "post", f"/v7/airpage/v2/{args.file_id}/blocks/create",
            {"block_id": "doc", "index": index + written, "doc_children": {"children": chunk}},
            args.timeout)
        written += len(chunk)
        print(f"已写入 {written}/{len(blocks)} 个块")

    print(f"完成：{args.mode} {written} 个块到 {args.file_id}")


if __name__ == "__main__":
    main()
