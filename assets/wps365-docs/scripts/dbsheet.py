#!/usr/bin/env python3
"""读写 WPS 365 多维表（.dbt）—— 记录、快照、附件。

多维表和智能文档/普通文件是三条不同的路线：

  * `.otl` 智能文档：blocks 级读写（otl_fetch.py / otl_write.py）
  * `.docx/.xlsx/.pptx`：下载原件 → 本地编辑 → 整文件传回
  * `.dbt` 多维表：本脚本。schema + records 接口，记录级读写

本脚本只做**记录级**操作，不封装建表/改字段：结构变更请直接用
`wps365-cli dbsheet sheet/field ...`，参考文档里有实测命令。

实测要点（v0.3.6，2026-10）：

  * `record list` 不传 `--page-size` 只回 1 条；本脚本总是传 100 并翻页。
  * `fields_value` 是 **JSON 字符串**，不是对象；响应里的 `fields` 也是字符串。
  * `prefer_id=true` 时，记录 key 与 filter 的 `field` 都是**字段 ID**；
    但 Link 字段的 filter 值必须是**显示名**，写记录 ID 会返回空。
  * 写入 Link 必须用**记录 ID**；传显示名不会报错，而是**静默清空**该字段。
    本脚本先把显示名解析成 ID，并在写后读回校验。
  * 写入不存在的 SingleSelect/MultipleSelect 选项也会静默清空；本脚本先校验选项。
  * 非法 Date 会返回 400；本脚本先做格式校验，错误更早、更清楚。
  * `record update` 是**部分更新**（实测：只传一个字段，其他字段保留）。
  * 附件必须从 raw 记录拿 `uploadId`；`text` 模式只剩文件名。

退出码：0 成功；1 参数/接口/冲突/读回不一致。所有输出为 UTF-8。
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import urllib.request

from _wps_cli import resolve as _resolve_cli
from _wps_cli import run_cli as _run_cli
from _wps_cli import use_utf8_stdio as _use_utf8_stdio

_LABEL = "dbsheet"
DEFAULT_CLI = _resolve_cli()
DEFAULT_TIMEOUT = 180
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_RECORDS = 50
MAX_PAGE_SIZE = 100
MAX_BATCH = 100
MAX_PAGES = 200
MAX_SELECT_OPTIONS = 12

UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

TEXT_TYPES = {
    "Text", "MultiLineText", "Phone", "Email", "URL",
    "Barcode", "SerialNumber",
}
NUMBER_TYPES = {"Number", "Currency", "Percentage", "Rating"}
READ_ONLY_TYPES = {
    "Formula", "Lookup", "AutoNumber", "CreatedTime", "CreatedBy",
    "LastModifiedTime", "LastModifiedBy", "OneWayLink", "Contact",
}
COMPLEX_READ_ONLY_TYPES = {"Cascade", "Location"}
MULTI_TYPES = {"MultipleSelect", "Link"}
DATE_FORMATS = (
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%Y.%m.%d",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
)
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


# ---------------------------------------------------------------- 基础工具

def fail(message: str) -> None:
    sys.exit(f"[{_LABEL}] {message}")


def _json_load(text: str, what: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        fail(f"{what} 不是合法 JSON：{exc}")


def _quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _short(value, limit: int = 120) -> str:
    text = _as_text(value).replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "…"
    return text


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 接口调用

def _api(cli: str, token_type: str, method: str, path: str, body, query,
         timeout: int, what: str):
    """调用 `wps365-cli api`，请求体写临时文件，避免命令行长度限制。"""
    cmd = [
        cli, "api", method.lower(), path,
        "-o", "json",
        "--timeout", f"{max(1, timeout)}s",
    ]
    if token_type:
        cmd += ["--token-type", token_type]
    for key, value in (query or []):
        cmd += ["--query", f"{key}={value}"]

    body_path = None
    if body is not None:
        fd, body_path = tempfile.mkstemp(prefix="dbsheet-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(body, handle, ensure_ascii=False)
        cmd += ["--file", body_path]
    try:
        stdout = _run_cli(cmd, timeout + 10, _LABEL, what)
    finally:
        if body_path:
            try:
                os.unlink(body_path)
            except OSError:
                pass

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        fail(f"{what} 返回的不是 JSON：{exc}")
    code = payload.get("code")
    if code not in (0, "0", None):
        message = payload.get("msg") or payload.get("message") or "未知错误"
        fail(f"{what} 失败（code={code}）：{message}")
    return payload.get("data") or {}


def _resolve_link(cli: str, link: str, token_type: str, timeout: int) -> str:
    link_id = link.rstrip("/").rsplit("/", 1)[-1].split("?")[0].split("#")[0]
    if not link_id:
        fail(f"无法从分享链接里取 link_id：{link}")
    data = _api(cli, token_type, "GET", f"/v7/links/{_quote(link_id)}/meta",
                None, None, timeout, f"解析分享链接 {link_id}")
    file_id = data.get("file_id")
    if not file_id:
        fail(f"分享链接解析结果缺少 file_id：{data}")
    return str(file_id)


def _file_id_from_args(args) -> str:
    if args.file_id and args.link:
        fail("--file-id 与 --link 只能给一个")
    if args.link:
        return _resolve_link(args.cli, args.link, args.token_type, args.timeout)
    if not args.file_id:
        fail("需要 --file-id 或 --link")
    return args.file_id


# ---------------------------------------------------------------- schema

def _get_schema(cli: str, file_id: str, token_type: str, timeout: int) -> dict:
    data = _api(cli, token_type, "GET",
                f"/v7/coop/dbsheet/{_quote(file_id)}/schema",
                None, None, timeout, "读取多维表结构")
    if not isinstance(data.get("sheets"), list):
        fail(f"schema 响应里没有 sheets：{_short(data)}")
    return data


def _sheets(schema: dict) -> list:
    return schema.get("sheets") or []


def _fields(sheet: dict) -> list:
    return sheet.get("fields") or []


def _field_by_id(sheet: dict, field_id: str):
    for field in _fields(sheet):
        if str(field.get("id")) == str(field_id):
            return field
    return None


def _field_name(sheet: dict, field_id: str) -> str:
    field = _field_by_id(sheet, field_id)
    return str(field.get("name")) if field else str(field_id)


def _resolve_sheet(schema: dict, ref) -> dict:
    wanted = str(ref)
    for sheet in _sheets(schema):
        if str(sheet.get("id")) == wanted:
            return sheet
    matches = [sheet for sheet in _sheets(schema) if str(sheet.get("name")) == wanted]
    if len(matches) == 1:
        return matches[0]
    names = [f"{s.get('id')} {s.get('name')}" for s in _sheets(schema)]
    if len(matches) > 1:
        fail(f"工作表名“{wanted}”不唯一，请用 ID：{names}")
    fail(f"找不到工作表“{wanted}”。可用：{names}")


def _resolve_field(sheet: dict, ref) -> dict:
    wanted = str(ref)
    for field in _fields(sheet):
        if str(field.get("id")) == wanted:
            return field
    matches = [field for field in _fields(sheet) if str(field.get("name")) == wanted]
    if len(matches) == 1:
        return matches[0]
    names = [f"{f.get('id')} {f.get('name')}" for f in _fields(sheet)]
    if len(matches) > 1:
        fail(f"字段名“{wanted}”不唯一，请用字段 ID：{names}")
    close = difflib.get_close_matches(wanted, [str(f.get("name")) for f in _fields(sheet)], n=3)
    hint = f"，相近字段：{close}" if close else ""
    fail(f"找不到字段“{wanted}”{hint}。可用：{names}")


def _resolve_view(sheet: dict, ref):
    wanted = str(ref)
    for view in sheet.get("views") or []:
        if str(view.get("id")) == wanted:
            return view
    matches = [v for v in (sheet.get("views") or []) if str(v.get("name")) == wanted]
    if len(matches) == 1:
        return matches[0]
    names = [f"{v.get('id')} {v.get('name')}" for v in (sheet.get("views") or [])]
    if len(matches) > 1:
        fail(f"视图名“{wanted}”不唯一，请用 ID：{names}")
    fail(f"找不到视图“{wanted}”。可用：{names}")


def _primary_field(sheet: dict):
    primary = sheet.get("primary_field_id")
    if primary:
        field = _field_by_id(sheet, primary)
        if field:
            return field
    return _fields(sheet)[0] if _fields(sheet) else None


def schema_hash(sheet: dict) -> str:
    payload = [
        {
            "id": field.get("id"),
            "name": field.get("name"),
            "type": field.get("type"),
            "data": field.get("data"),
        }
        for field in _fields(sheet)
    ]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(blob).hexdigest()


def _sheet_kind(sheet: dict) -> str:
    kind = sheet.get("sheet_type")
    if kind == "xlEtFlexPaperSheet":
        return "文档型（FlexPaper，正文不可读）"
    if kind == "xlEtDataBaseSheet":
        return "数据表"
    return str(kind or "未知类型")


def _option_values(field: dict) -> list:
    items = ((field.get("data") or {}).get("items") or [])
    return [str(item.get("value")) for item in items if item.get("value") is not None]


def _link_target(sheet_lookup: dict, field: dict):
    data = field.get("data") or {}
    target_id = data.get("link_sheet")
    if target_id is None:
        return None
    return sheet_lookup.get(str(target_id))


def _render_schema(schema: dict, only=None) -> str:
    sheets = _sheets(schema)
    lookup = {str(sheet.get("id")): sheet for sheet in sheets}
    if only is not None:
        sheets = [_resolve_sheet(schema, only)]
    lines = [f"文件共 {len(_sheets(schema))} 张表；以下显示 {len(sheets)} 张。"]
    for sheet in sheets:
        sid = sheet.get("id")
        primary = _primary_field(sheet)
        primary_name = f"{primary.get('name')}({primary.get('id')})" if primary else "无"
        lines.append("")
        lines.append(f"## {sid} {sheet.get('name')} — {_sheet_kind(sheet)}")
        lines.append(f"- 记录数：{sheet.get('records_count', '?')}；主字段：{primary_name}")
        views = sheet.get("views") or []
        if views:
            bits = [f"{v.get('id')} {v.get('name')} ({v.get('type')})"
                    for v in views]
            lines.append(f"- 视图：{'；'.join(bits)}")
        if not _fields(sheet):
            lines.append("- 字段：无（文档型工作表，记录/附件接口均不可用）")
            continue
        lines.append("- 字段：")
        for field in _fields(sheet):
            extra = ""
            typ = field.get("type")
            if typ in ("SingleSelect", "MultipleSelect"):
                values = _option_values(field)
                shown = "、".join(values[:MAX_SELECT_OPTIONS])
                more = f" …共 {len(values)} 项" if len(values) > MAX_SELECT_OPTIONS else ""
                extra = f" [{shown}{more}]" if values else " [无选项]"
            elif typ == "Link":
                target = _link_target(lookup, field)
                data = field.get("data") or {}
                target_name = f"{target.get('name')}({target.get('id')})" if target else "?"
                multi = "多选" if data.get("multiple_links") else "单选"
                extra = f" → {target_name}，{multi}"
            elif typ in READ_ONLY_TYPES or typ in COMPLEX_READ_ONLY_TYPES:
                extra = "（只读/不支持直接写）"
            elif typ == "Attachment":
                extra = "（附件，本脚本只下载）"
            lines.append(f"  - {field.get('id')} {field.get('name')} : {typ}{extra}")
        for view in views:
            if view.get("type") != "Form":
                continue
            attrs = view.get("fields_attribute") or []
            visible = [a.get("field") for a in attrs if not a.get("hidden")]
            hidden = [a.get("field") for a in attrs if a.get("hidden")]
            lines.append(f"- 表单 {view.get('id')} {view.get('name')} 可见字段：{'、'.join(visible) or '无'}")
            if hidden:
                lines.append(f"  隐藏字段：{'、'.join(hidden)}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 读记录

def _decode_fields(record: dict) -> dict:
    raw = record.get("fields")
    if raw in (None, "", "null"):
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decode_record(sheet: dict, record: dict, text_mode: bool) -> dict:
    raw = _decode_fields(record)
    decoded = {}
    for field in _fields(sheet):
        fid = str(field.get("id"))
        if fid in raw:
            decoded[str(field.get("name"))] = raw[fid]
    return {"id": record.get("id"), "fields": decoded}


def _list_page(cli: str, file_id: str, sheet_id, token_type: str, timeout: int,
               page_size: int, page_token: str, fields, filter_body, text_mode: bool,
               view_id) -> dict:
    body = {"page_size": page_size, "prefer_id": True}
    if fields:
        body["fields"] = fields
    if filter_body:
        body["filter"] = filter_body
    if text_mode:
        body["text_value"] = "text"
    if view_id:
        body["view_id"] = str(view_id)
    if page_token:
        body["page_token"] = page_token
    return _api(cli, token_type, "POST",
                f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet_id)}/records",
                body, None, timeout, "读取记录")


def _iter_records(cli: str, file_id: str, sheet: dict, token_type: str, timeout: int,
                  page_size: int, max_records: int, fields, filter_body,
                  text_mode: bool, view_id):
    sheet_id = sheet.get("id")
    token = ""
    seen = set()
    pages = 0
    total = 0
    while True:
        page_size = min(MAX_PAGE_SIZE, max(1, page_size))
        if max_records:
            page_size = min(page_size, max(1, max_records - total))
        data = _list_page(cli, file_id, sheet_id, token_type, timeout, page_size,
                          token, fields, filter_body, text_mode, view_id)
        records = data.get("records") or []
        for record in records:
            yield record
            total += 1
            if max_records and total >= max_records:
                return
        token = str(data.get("page_token") or "")
        if not token or token in seen or not records:
            return
        seen.add(token)
        pages += 1
        if pages > MAX_PAGES:
            fail(f"翻页超过 {MAX_PAGES} 页仍未结束，已停止；请缩小范围或检查 page_token")


def _search_records(cli: str, file_id: str, sheet: dict, token_type: str, timeout: int,
                    record_ids: list, text_mode: bool) -> list:
    ids = [str(item) for item in record_ids if str(item)]
    if not ids:
        return []
    out = []
    for chunk in _chunks(ids, 100):
        body = {"records": chunk, "prefer_id": True}
        if text_mode:
            body["text_value"] = "text"
        data = _api(cli, token_type, "POST",
                    f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet.get('id'))}/records/search",
                    body, None, timeout, "按 ID 读取记录")
        out.extend(data.get("records") or [])
    return out


def _get_record(cli: str, file_id: str, sheet: dict, token_type: str, timeout: int,
                record_id: str, text_mode: bool) -> dict:
    query = [("prefer_id", "true")]
    if text_mode:
        query.append(("text_value", "text"))
    data = _api(cli, token_type, "GET",
                f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet.get('id'))}"
                f"/records/{_quote(record_id)}",
                None, query, timeout, "读取单条记录")
    record = data.get("record")
    if not record:
        fail(f"找不到记录 {record_id}")
    return record


def _pick_fields(sheet: dict, refs) -> list:
    if refs:
        picked = []
        for ref in refs.split(","):
            ref = ref.strip()
            if not ref:
                continue
            field = _resolve_field(sheet, ref)
            if field not in picked:
                picked.append(field)
        return picked
    primary = _primary_field(sheet)
    picked = [primary] if primary else []
    for field in _fields(sheet):
        if len(picked) >= 6:
            break
        if field not in picked:
            picked.append(field)
    return picked


def _format_cell(field: dict, value, truncate: bool) -> str:
    if value is None:
        return ""
    typ = field.get("type")
    if typ == "Checkbox":
        return "是" if _parse_bool(value) else "否"
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return "、".join(str(item.get("fileName") or "附件") for item in value)
        text = "、".join(_as_text(item) for item in value)
    else:
        text = _as_text(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("|", "\\|").replace("\n", "<br>")
    if truncate and len(text) > 40:
        text = text[:40] + "…"
    return text


def _render_table(records: list, fields: list, truncate: bool) -> str:
    headers = [str(field.get("name")) for field in fields]
    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for record in records:
        values = [_format_cell(field, record["fields"].get(str(field.get("name"))), truncate)
                  for field in fields]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _render_cards(records: list, fields: list, truncate: bool) -> str:
    lines = []
    for record in records:
        lines.append(f"### 记录 {record['id']}")
        for field in fields:
            name = str(field.get("name"))
            value = record["fields"].get(name)
            if field.get("type") == "Checkbox" and value is not None:
                value = "是" if _parse_bool(value) else "否"
            elif isinstance(value, list) and value and isinstance(value[0], dict):
                value = "、".join(str(item.get("fileName") or "附件") for item in value)
            elif isinstance(value, list):
                value = "、".join(_as_text(item) for item in value)
            text = _as_text(value).strip()
            if not text:
                continue
            if truncate and len(text) > 500:
                text = text[:500] + "…"
            lines.append(f"- **{name}**：{text}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _load_json_arg(raw: str, path: str, what: str):
    if raw and path:
        fail(f"{what} 的 --... 与 --...-file 只能给一个")
    if path:
        if path == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
            except OSError as exc:
                fail(f"读不了 {path}：{exc}")
        return _json_load(text, what)
    if raw:
        return _json_load(raw, what)
    return None


def _normalize_filter(sheet: dict, body: dict) -> dict:
    if not isinstance(body, dict):
        fail("filter 必须是 JSON 对象")
    criteria = body.get("criteria")
    if not isinstance(criteria, list):
        fail("filter 缺少 criteria 数组")
    normalized = []
    for item in criteria:
        if not isinstance(item, dict):
            fail("filter.criteria 的每一项都必须是对象")
        field_ref = item.get("field")
        if field_ref is None:
            fail("filter.criteria 缺少 field")
        field = _resolve_field(sheet, field_ref)
        operator = item.get("operator") or item.get("op")
        if not operator:
            fail(f"filter 字段 {field.get('name')} 缺少 operator/op")
        criterion = {"field": str(field.get("id")), "operator": str(operator)}
        if "values" in item:
            criterion["values"] = item["values"]
        normalized.append(criterion)
    return {"mode": body.get("mode") or "And", "criteria": normalized}


# ---------------------------------------------------------------- 值转换

def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y", "是", "对"):
        return True
    if text in ("false", "0", "no", "n", "否", "错", ""):
        return False
    fail(f"无法把“{value}”解析成复选框值（用 true/false）")


def _select_option(field: dict, value) -> str:
    text = _as_text(value)
    items = ((field.get("data") or {}).get("items") or [])
    for item in items:
        if str(item.get("value")) == text or str(item.get("id")) == text:
            return str(item.get("value"))
    options = [str(item.get("value")) for item in items]
    fail(f"字段“{field.get('name')}”没有选项“{text}”；可用：{options}")


def _parse_multi(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if text.startswith("["):
        parsed = _json_load(text, "多值字段")
        if not isinstance(parsed, list):
            fail("多值字段的 JSON 必须是数组")
        return parsed
    if text == "":
        return []
    return [part.strip() for part in re.split(r"[,，]", text) if part.strip()]


def _parse_date(value) -> datetime.datetime:
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    fail(f"日期“{text}”格式不对；用 YYYY/MM/DD 或 YYYY-MM-DD（可带 HH:MM[:SS]）")


def _date_has_time(field: dict) -> bool:
    fmt = str((field.get("data") or {}).get("number_format") or "")
    return bool(re.search(r"[hH]|:mm", fmt))


def _normalize_date(field: dict, value) -> str:
    if value in (None, ""):
        return ""
    try:
        stamp = _parse_date(value)
    except SystemExit:
        return str(value).strip()
    if _date_has_time(field):
        fmt = str((field.get("data") or {}).get("number_format") or "")
        # Ignore quoted literals and Excel's escaped characters when deciding
        # whether the field displays seconds.
        fmt = re.sub(r'"[^"]*"|\\.', "", fmt)
        return stamp.strftime("%Y/%m/%d %H:%M:%S" if re.search(r"s", fmt, re.I)
                              else "%Y/%m/%d %H:%M")
    return stamp.strftime("%Y/%m/%d")


def _coerce_value(field: dict, value, resolver=None):
    typ = field.get("type")
    if typ in READ_ONLY_TYPES:
        fail(f"字段“{field.get('name')}”是 {typ}，接口不允许手动写")
    if typ in COMPLEX_READ_ONLY_TYPES:
        fail(f"字段“{field.get('name')}”是 {typ}，结构复杂；如确知格式请用 --set-json")
    if typ == "Attachment":
        fail(f"字段“{field.get('name')}”是附件，写入需要上传流程；本脚本只支持下载")
    if typ in TEXT_TYPES:
        return _as_text(value)
    if typ in NUMBER_TYPES:
        if isinstance(value, bool):
            fail(f"字段“{field.get('name')}”要数字，不是布尔")
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = str(value).strip()
            if typ == "Percentage" and text.endswith("%"):
                text = text[:-1]
                try:
                    number = float(text) / 100.0
                except ValueError:
                    fail(f"字段“{field.get('name')}”要数字，收到“{value}”")
            else:
                try:
                    number = float(text)
                except ValueError:
                    fail(f"字段“{field.get('name')}”要数字，收到“{value}”")
        if number != number or number in (float("inf"), float("-inf")):
            fail(f"字段“{field.get('name')}”不接受 NaN/Infinity")
        return int(number) if number.is_integer() else number
    if typ == "Checkbox":
        return _parse_bool(value)
    if typ == "Date":
        return _parse_date(value).strftime(
            "%Y/%m/%d %H:%M:%S" if _date_has_time(field) else "%Y/%m/%d")
    if typ == "SingleSelect":
        return _select_option(field, value)
    if typ == "MultipleSelect":
        return [_select_option(field, item) for item in _parse_multi(value)]
    if typ == "Link":
        if resolver is None:
            fail(f"字段“{field.get('name')}”是 Link，但缺少解析器")
        return resolver(field, _parse_multi(value))
    fail(f"字段“{field.get('name')}”的类型 {typ} 暂不支持写入；如确知格式请用 --set-json")


def _select_value(field: dict, value) -> str:
    """比较用：选项存在就返回显示名，否则原样返回，不因脏数据中断比对。"""
    text = _as_text(value)
    for item in ((field.get("data") or {}).get("items") or []):
        if str(item.get("value")) == text or str(item.get("id")) == text:
            return str(item.get("value"))
    return text


def _values_equal(field: dict, left, right) -> bool:
    typ = field.get("type")
    if typ in ("Link", "MultipleSelect"):
        return {_as_text(item) for item in _as_list(left)} == {
            _as_text(item) for item in _as_list(right)}
    if typ in NUMBER_TYPES:
        if left in (None, "") and right in (None, ""):
            return True
        try:
            return float(left) == float(right)
        except (TypeError, ValueError):
            return False
    if typ == "Checkbox":
        try:
            return _parse_bool(False if left is None else left) == _parse_bool(
                False if right is None else right)
        except SystemExit:
            return False
    if typ == "Date":
        return _normalize_date(field, left) == _normalize_date(field, right)
    if typ == "SingleSelect":
        if left in (None, "") and right in (None, ""):
            return True
        return _select_value(field, left) == _select_value(field, right)
    return _as_text(left) == _as_text(right)


# ---------------------------------------------------------------- Link 解析

def _build_link_maps(schema: dict, cli: str, file_id: str, token_type: str, timeout: int):
    """按目标表返回 {target_sheet_id: {显示名: [record_id...]}}。"""
    cache = {}

    def mapping_for(target_sheet: dict) -> dict:
        target_id = str(target_sheet.get("id"))
        if target_id in cache:
            return cache[target_id]
        primary = _primary_field(target_sheet)
        if not primary:
            fail(f"目标表“{target_sheet.get('name')}”没有主字段，无法按名称解析 Link")
        mapping = {}
        for record in _iter_records(
                cli, file_id, target_sheet, token_type, timeout,
                DEFAULT_PAGE_SIZE, 0, [str(primary.get("id"))], None, True, None):
            fields = _decode_fields(record)
            value = fields.get(str(primary.get("id")))
            if isinstance(value, list):
                value = ",".join(_as_text(item) for item in value)
            name = _as_text(value).strip()
            if not name:
                continue
            mapping.setdefault(name, []).append(str(record.get("id")))
        cache[target_id] = mapping
        return mapping

    def resolver(field: dict, values: list) -> list:
        data = field.get("data") or {}
        target_id = data.get("link_sheet")
        if target_id is None:
            fail(f"Link 字段“{field.get('name')}”缺少 link_sheet")
        target_sheet = None
        for sheet in _sheets(schema):
            if str(sheet.get("id")) == str(target_id):
                target_sheet = sheet
                break
        if target_sheet is None:
            fail(f"Link 字段“{field.get('name')}”指向的工作表 {target_id} 不存在")
        mapping = mapping_for(target_sheet)
        resolved = []
        for item in values:
            text = _as_text(item).strip()
            if not text:
                continue
            ids = mapping.get(text)
            if ids:
                if len(ids) > 1:
                    fail(f"Link 值“{text}”在目标表里对应多条记录 {ids}；请直接传记录 ID")
                resolved.append(ids[0])
                continue
            # 直接传记录 ID 也允许，但必须确认存在，否则接口会静默清空。
            found = _search_records(cli, file_id, target_sheet, token_type, timeout,
                                    [text], False)
            if found and str(found[0].get("id")) == text:
                resolved.append(text)
                continue
            close = difflib.get_close_matches(text, list(mapping), n=3)
            hint = f"，相近：{close}" if close else ""
            fail(f"Link 字段“{field.get('name')}”找不到“{text}”{hint}；"
                 f"请用目标表“{target_sheet.get('name')}”的主字段显示名或记录 ID")
        if not data.get("multiple_links") and len(resolved) > 1:
            fail(f"Link 字段“{field.get('name')}”只允许一条关联，收到 {resolved}")
        return resolved

    return resolver


# ---------------------------------------------------------------- 写记录

def _parse_set(items: list) -> list:
    pairs = []
    seen = set()
    for item in items or []:
        if "=" not in item:
            fail(f"--set 需要“字段=值”，收到“{item}”")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            fail("--set 的字段名不能为空")
        if key in seen:
            fail(f"--set 字段“{key}”重复；请合并成一条")
        seen.add(key)
        pairs.append((key, value))
    return pairs


def _parse_set_json(items: list) -> list:
    pairs = []
    seen = set()
    for item in items or []:
        if "=" not in item:
            fail(f"--set-json 需要“字段=<JSON>”，收到“{item}”")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            fail("--set-json 的字段名不能为空")
        if key in seen:
            fail(f"--set-json 字段“{key}”重复；请合并成一条")
        seen.add(key)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail(f"--set-json 的 {key} 不是合法 JSON：{exc}")
        pairs.append((key, value))
    return pairs


def _read_records_file(path: str) -> list:
    if path == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            fail(f"读不了 {path}：{exc}")
    data = _json_load(text, "records-file")
    if not isinstance(data, list) or not data:
        fail("records-file 必须是非空 JSON 数组")
    if len(data) > MAX_BATCH:
        fail(f"单次最多 {MAX_BATCH} 条记录，收到 {len(data)}；请分批")
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            fail(f"records-file 第 {index + 1} 条不是对象")
    return data


def _build_record(sheet: dict, spec: dict, resolver, allow_id: bool,
                  seen_fields: set | None = None) -> dict:
    if "fields_value" in spec:
        if "fields" in spec or "json_fields" in spec:
            fail("fields_value 不能与 fields/json_fields 混用")
        raw = spec.get("fields_value")
        if not isinstance(raw, str):
            fail("fields_value 必须是 JSON 字符串")
        parsed = _json_load(raw, "fields_value")
        if not isinstance(parsed, dict):
            fail("fields_value 必须是 JSON 对象")
        fields_value = {}
        for key, value in parsed.items():
            field = _field_by_id(sheet, key) or _resolve_field(sheet, key)
            if str(field.get("id")) != str(key):
                fail(f"fields_value 的 key 必须是字段 ID；{key} 是名称，"
                     f"请改用“{field.get('id')}”或普通 fields 映射")
            if str(field.get("type")) in READ_ONLY_TYPES or str(field.get("type")) == "Attachment":
                fail(f"字段“{field.get('name')}”是只读类型 {field.get('type')}，不能写入")
            fields_value[str(field.get("id"))] = value
        return {"fields_value": json.dumps(fields_value, ensure_ascii=False)}

    fields = spec.get("fields")
    json_fields = spec.get("json_fields")
    if not isinstance(fields, dict) and not isinstance(json_fields, dict):
        fail("每条记录需要非空的 fields 对象（或 fields_value 字符串）")
    built = {}
    for source in (fields or {}, json_fields or {}):
        for ref, value in source.items():
            field = _resolve_field(sheet, ref)
            fid = str(field.get("id"))
            if fid in built:
                fail(f"字段“{field.get('name')}”在 --set 与 --set-json 中重复")
            if seen_fields is not None:
                if fid in seen_fields:
                    fail(f"字段“{field.get('name')}”重复")
                seen_fields.add(fid)
            if source is json_fields:
                if str(field.get("type")) in READ_ONLY_TYPES or str(field.get("type")) == "Attachment":
                    fail(f"字段“{field.get('name')}”是只读类型 {field.get('type')}，不能用 --set-json 写入")
                built[fid] = value
            else:
                built[fid] = _coerce_value(field, value, resolver)
    if not built:
        fail("没有可写字段：请至少给一个 --set 或 --set-json")
    return {"fields_value": json.dumps(built, ensure_ascii=False)}


def _expected_from_record(sheet: dict, built: dict) -> dict:
    return _json_load(built["fields_value"], "fields_value")


def _print_conflicts(conflicts: list, will_force: bool = False) -> None:
    if will_force:
        print("警告：以下记录在快照之后被改过；将按 --force 覆盖。", file=sys.stderr)
    else:
        print("冲突：以下记录在快照之后被改过，已放弃写入。", file=sys.stderr)
    for item in conflicts:
        print(f"- 记录 {item['record_id']} 字段 "
              f"{item['field_name']}({item['field_id']})", file=sys.stderr)
        print(f"    快照：{_short(item['old'])}", file=sys.stderr)
        print(f"    当前：{_short(item['new'])}", file=sys.stderr)
    if not will_force:
        print("重新读取最新记录、合并你的改动后再写；确认覆盖才用 --force。", file=sys.stderr)


def _snapshot_conflicts(snapshot: dict, sheet: dict, current: dict, target_ids: list,
                        target_fields_by_id: dict, all_fields: bool) -> list:
    conflicts = []
    fields = {str(field.get("id")): field for field in _fields(sheet)}
    for record_id in target_ids:
        record_id = str(record_id)
        saved = (snapshot.get("records") or {}).get(record_id)
        if not saved:
            conflicts.append({"record_id": record_id, "field_id": "-", "field_name": "记录",
                              "old": "快照中不存在", "new": "记录存在"})
            continue
        now = current.get(record_id)
        if not now:
            conflicts.append({"record_id": record_id, "field_id": "-", "field_name": "记录",
                              "old": "记录存在", "new": "记录已删除"})
            continue
        now_fields = _decode_fields(now)
        saved_fields = saved.get("fields") or {}
        ids = list(fields) if all_fields else list(target_fields_by_id.get(record_id, []))
        for fid in ids:
            field = fields.get(str(fid))
            if field is None:
                continue
            old = saved_fields.get(str(fid))
            new = now_fields.get(str(fid))
            if not _values_equal(field, old, new):
                conflicts.append({
                    "record_id": record_id,
                    "field_id": str(fid),
                    "field_name": str(field.get("name")),
                    "old": old,
                    "new": new,
                })
    return conflicts


def _verify_written(sheet: dict, records: list, expected: dict) -> list:
    by_id = {str(record.get("id")): record for record in records}
    errors = []
    for record_id, fields in expected.items():
        record = by_id.get(str(record_id))
        if not record:
            errors.append(f"记录 {record_id} 读不回来")
            continue
        current = _decode_fields(record)
        for fid, wanted in fields.items():
            field = _field_by_id(sheet, fid)
            if field is None:
                continue
            actual = current.get(str(fid))
            if not _values_equal(field, wanted, actual):
                errors.append(f"记录 {record_id} 字段 {field.get('name')}({fid})："
                              f"写入 {_short(wanted)}，读回 {_short(actual)}")
    return errors


# ---------------------------------------------------------------- 附件

def _attachment_entries(sheet: dict, records: list, only_field=None) -> list:
    entries = []
    seen = set()
    for record in records:
        record_id = str(record.get("id"))
        fields = _decode_fields(record)
        for field in _fields(sheet):
            fid = str(field.get("id"))
            if only_field is not None and fid != str(only_field.get("id")):
                continue
            if field.get("type") != "Attachment":
                continue
            value = fields.get(fid)
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    continue
            if isinstance(value, dict):
                value = [value]
            if not isinstance(value, list):
                continue
            for index, item in enumerate(value):
                if not isinstance(item, dict):
                    continue
                upload_id = item.get("uploadId") or item.get("attachment_id")
                if not upload_id:
                    continue
                key = (record_id, str(upload_id))
                if key in seen:
                    continue
                seen.add(key)
                entries.append({
                    "record_id": record_id,
                    "field_id": fid,
                    "field_name": str(field.get("name")),
                    "index": index,
                    "filename": str(item.get("fileName") or ""),
                    "size": item.get("size"),
                    "mime": str(item.get("type") or ""),
                    "upload_id": str(upload_id),
                })
    return entries


def _safe_filename(name: str, fallback: str) -> str:
    base = os.path.basename(name or "").strip()
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    base = base.rstrip(" .")
    if not base:
        base = fallback
    if len(base) > 120:
        stem, ext = os.path.splitext(base)
        base = stem[:120 - len(ext)] + ext
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
        f"LPT{i}" for i in range(1, 10)}
    if os.path.splitext(base)[0].upper() in reserved:
        base = "_" + base
    return base


def _is_image(entry: dict) -> bool:
    mime = entry.get("mime", "").lower()
    ext = os.path.splitext(entry.get("filename") or "")[1].lower()
    return mime in IMAGE_MIMES or ext in IMAGE_EXTS


def _attachment_url(cli: str, file_id: str, upload_id: str, mime: str,
                    token_type: str, timeout: int) -> dict:
    kind = mime if "/" in mime else os.path.splitext(mime)[1].lstrip(".") or "bin"
    data = _api(cli, token_type, "GET",
                f"/v7/coop/dbsheet/{_quote(file_id)}/attachments/{_quote(upload_id)}",
                None, [("type", kind)], timeout, f"取附件 {upload_id}")
    attachment = data.get("attachment") or {}
    value = attachment.get("value") or {}
    url = value.get("download_url")
    if not url:
        fail(f"附件 {upload_id} 没有 download_url：{_short(data)}")
    return {
        "url": url,
        "file_size": value.get("file_size"),
        "file_ext": attachment.get("file_ext") or "",
    }


def _image_magic_ext(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return ""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head[:3] == b"GIF":
        return ".gif"
    return ""


def _download(url: str, path: str, timeout: int) -> int:
    request = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with open(path, "wb") as handle:
            shutil.copyfileobj(response, handle)
    return os.path.getsize(path)


# ---------------------------------------------------------------- 子命令

def _load_sheet(args):
    file_id = _file_id_from_args(args)
    schema = _get_schema(args.cli, file_id, args.token_type, args.timeout)
    if not getattr(args, "sheet", None):
        fail("这个子命令需要 --sheet（工作表名或 ID）")
    sheet = _resolve_sheet(schema, args.sheet)
    return file_id, schema, sheet


def cmd_schema(args) -> None:
    file_id = _file_id_from_args(args)
    schema = _get_schema(args.cli, file_id, args.token_type, args.timeout)
    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(schema, handle, ensure_ascii=False, indent=2)
    print(_render_schema(schema, args.sheet))
    if args.out:
        print(f"\n完整 schema 已写入：{out}")


def cmd_list(args) -> None:
    file_id, schema, sheet = _load_sheet(args)
    if _sheet_kind(sheet).startswith("文档型"):
        fail(f"工作表“{sheet.get('name')}”是 FlexPaper 文档型工作表，没有记录接口")
    fields = _pick_fields(sheet, args.fields)
    if not fields:
        fail(f"工作表“{sheet.get('name')}”没有可读字段")
    view = None
    view_id = None
    if args.view:
        view = _resolve_view(sheet, args.view)
        if view.get("type") == "Form":
            print(f"⚠️ 视图“{view.get('name')}”是 Form 表单视图，不承载记录列表；"
                  f"已改为读取底层数据表。表单字段顺序可用 schema 查看。", file=sys.stderr)
        else:
            view_id = str(view.get("id"))
    filter_body = _load_json_arg(args.filter, args.filter_file, "filter")
    if filter_body is not None:
        filter_body = _normalize_filter(sheet, filter_body)
    max_records = args.max_records
    if max_records < 0:
        fail("--max-records 不能为负数；0 表示全部")
    records = []
    for record in _iter_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                args.page_size, max_records,
                                [str(field.get("id")) for field in fields],
                                filter_body, not args.raw, view_id):
        records.append(_decode_record(sheet, record, not args.raw))
    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        payload = {
            "file_id": file_id,
            "sheet_id": sheet.get("id"),
            "sheet_name": sheet.get("name"),
            "view": view.get("name") if view else None,
            "fields": [f.get("name") for f in fields],
            "records": records,
            "fetched_at": _now_iso(),
        }
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    if args.format in ("json", "jsonl") and args.out:
        print(f"已读取 {len(records)} 条记录，完整结果：{out}")
        return
    if not records:
        print("没有匹配的记录。")
        return
    if args.format == "json":
        print(json.dumps(records, ensure_ascii=False, indent=2))
    elif args.format == "jsonl":
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
    elif args.format == "cards":
        print(_render_cards(records, fields, not args.no_truncate))
    else:
        print(_render_table(records, fields, not args.no_truncate))
    if args.out:
        print(f"\n已读取 {len(records)} 条记录，完整结果：{out}")


def cmd_get(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    record = _get_record(args.cli, file_id, sheet, args.token_type, args.timeout,
                         args.record, not args.raw)
    decoded = _decode_record(sheet, record, not args.raw)
    if args.format == "json":
        print(json.dumps(decoded, ensure_ascii=False, indent=2))
    else:
        print(_render_cards([decoded], _fields(sheet), False))


def _duplicate_check(args, file_id: str, sheet: dict, spec: dict, resolver,
                     schema: dict) -> None:
    refs = [ref.strip() for ref in (args.check_duplicate or "").split(",") if ref.strip()]
    if not refs:
        return
    fields = [_resolve_field(sheet, ref) for ref in refs]
    source = spec.get("fields") or {}
    criteria = []
    for field in fields:
        value = None
        for ref, candidate in source.items():
            if str(_resolve_field(sheet, ref).get("id")) == str(field.get("id")):
                value = candidate
                break
        if value is None:
            fail(f"--check-duplicate 字段“{field.get('name')}”不在本次要写入的字段里")
        typ = field.get("type")
        if typ == "Link":
            ids = _coerce_value(field, value, resolver)
            target = _resolve_sheet(schema, (field.get("data") or {}).get("link_sheet"))
            primary = _primary_field(target)
            if primary is None:
                fail(f"Link 字段“{field.get('name')}”的目标表没有主字段，无法查重")
            records = _search_records(args.cli, file_id, target, args.token_type,
                                      args.timeout, ids, True)
            by_id = {str(record.get("id")): _decode_fields(record) for record in records}
            values = []
            for record_id in ids:
                name = _as_text(by_id.get(str(record_id), {}).get(str(primary.get("id"))))
                if not name:
                    fail(f"Link 目标记录 {record_id} 的显示名读不到，已放弃创建")
                values.append(name)
        elif typ == "MultipleSelect":
            values = _coerce_value(field, value)
        elif typ == "SingleSelect":
            values = [_select_option(field, value)]
        elif typ in NUMBER_TYPES:
            values = [_coerce_value(field, value)]
        elif typ == "Checkbox":
            values = [_parse_bool(value)]
        elif typ == "Date":
            values = [_coerce_value(field, value)]
        else:
            values = [_as_text(value)]
        operator = "Equals" if typ in NUMBER_TYPES | {"Date", "Checkbox"} else "Intersected"
        criteria.append({"field": str(field.get("id")), "operator": operator, "values": values})
    filter_body = {"mode": "And", "criteria": criteria}
    matches = []
    try:
        for record in _iter_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                    DEFAULT_PAGE_SIZE, 20, None, filter_body, True, None):
            matches.append(record)
    except SystemExit as exc:
        fail(f"自动查重失败，已放弃创建：{exc}")
    if matches:
        ids = [str(record.get("id")) for record in matches]
        if args.allow_duplicate:
            print(f"⚠️ 发现 {len(matches)} 条可能重复的记录 {ids}；--allow-duplicate 已放行。",
                  file=sys.stderr)
            return
        fail(f"发现 {len(matches)} 条可能重复的记录 {ids}；确认要建重复记录才加 --allow-duplicate")


def cmd_create(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    if _sheet_kind(sheet).startswith("文档型"):
        fail(f"工作表“{sheet.get('name')}”是 FlexPaper 文档型工作表，不能建记录")
    if args.records_file and (args.set or args.set_json):
        fail("--records-file 与 --set/--set-json 只能给一个")
    specs = _read_records_file(args.records_file) if args.records_file else [
        {"fields": dict(_parse_set(args.set)), "json_fields": dict(_parse_set_json(args.set_json))}
    ]
    if args.check_duplicate and len(specs) != 1:
        fail("--check-duplicate 只支持单条创建")
    resolver = _build_link_maps(_schema, args.cli, file_id, args.token_type, args.timeout)
    records = []
    expected = {}
    for spec in specs:
        if "id" in spec:
            fail("create 的记录不需要 id；更新请用 update")
        built = _build_record(sheet, spec, resolver, allow_id=False)
        records.append(built)
    if args.check_duplicate:
        for spec in specs:
            _duplicate_check(args, file_id, sheet, spec, resolver, _schema)
    body = {"prefer_id": True, "records": records}
    if args.dry_run:
        print("预演：未发送创建请求。")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    print("注意：创建接口非幂等。若本次调用超时/网络错误，不要直接重试；"
          "先用 list/filter 确认是否已经创建。", file=sys.stderr)
    data = _api(args.cli, args.token_type, "POST",
                f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet.get('id'))}/records/create",
                body, None, args.timeout, "创建记录")
    created = data.get("records") or []
    ids = [str(record.get("id")) for record in created if record.get("id")]
    if len(ids) != len(records):
        fail(f"创建响应缺少记录 ID（期望 {len(records)}，收到 {ids}）；"
             f"记录可能已创建，请先 list 检查，不要盲目重试")
    for record_id, built in zip(ids, records):
        expected[record_id] = _expected_from_record(sheet, built)
    readback = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                               ids, False)
    errors = _verify_written(sheet, readback, expected)
    if errors:
        print(f"⚠️ 已创建记录 {ids}，但读回校验不一致：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(1)
    print(f"已创建 {len(ids)} 条记录：{', '.join(ids)}")
    display = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                              ids, True)
    for record in display:
        print(_render_cards([_decode_record(sheet, record, True)], _fields(sheet), True))


def _load_snapshot(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
    except OSError as exc:
        fail(f"读不了快照 {path}：{exc}")
    except json.JSONDecodeError as exc:
        fail(f"快照 {path} 不是合法 JSON：{exc}")
    if not isinstance(snapshot, dict) or snapshot.get("version") != 1:
        fail("快照版本不对；请用 dbsheet.py snapshot 重新生成")
    return snapshot


def _check_snapshot_identity(snapshot: dict, file_id: str, sheet: dict) -> None:
    if str(snapshot.get("file_id")) != str(file_id):
        fail(f"快照属于文件 {snapshot.get('file_id')}，本次是 {file_id}")
    if str(snapshot.get("sheet_id")) != str(sheet.get("id")):
        fail(f"快照属于工作表 {snapshot.get('sheet_id')}，本次是 {sheet.get('id')}")
    if snapshot.get("schema_hash") != schema_hash(sheet):
        fail("工作表结构在快照之后变过（字段名/类型/选项/关联目标），请重新生成快照")


def _targets_from_file(args, sheet: dict, resolver) -> tuple[list, dict]:
    specs = _read_records_file(args.records_file)
    ids = []
    expected = {}
    for spec in specs:
        record_id = spec.get("id")
        if not record_id:
            fail("update/delete 的 records-file 每条都要有 id")
        record_id = str(record_id)
        if record_id in expected:
            fail(f"records-file 的记录 ID {record_id} 重复；请合并为一条更新")
        fields = {key: value for key, value in spec.items() if key not in ("id", "fields", "fields_value")}
        if fields:
            fail(f"记录 {record_id} 的字段应放在 fields 里，不能直接放在顶层")
        built = _build_record(sheet, spec, resolver, allow_id=True)
        ids.append(str(record_id))
        expected[str(record_id)] = _expected_from_record(sheet, built)
    return ids, expected


def cmd_update(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    if not args.expect_snapshot and not args.force and not args.dry_run:
        fail("update 必须先 snapshot，再传 --expect-snapshot；确认覆盖才用 --force")
    resolver = _build_link_maps(_schema, args.cli, file_id, args.token_type, args.timeout)
    if args.records_file:
        if args.record or args.set or args.set_json:
            fail("--records-file 与 --record/--set/--set-json 不能混用")
        ids, expected = _targets_from_file(args, sheet, resolver)
    else:
        ids = _record_ids(args.record)
        if not ids:
            fail("需要 --record（记录 ID）")
        if not args.set and not args.set_json:
            fail("需要 --set 或 --set-json（字段=值）")
        built = _build_record(sheet, {
            "fields": dict(_parse_set(args.set)),
            "json_fields": dict(_parse_set_json(args.set_json)),
        }, resolver, allow_id=True)
        values = _expected_from_record(sheet, built)
        expected = {record_id: values for record_id in ids}
    target_fields = {record_id: list(values) for record_id, values in expected.items()}
    if not args.dry_run:
        current_records = _search_records(args.cli, file_id, sheet, args.token_type,
                                          args.timeout, ids, False)
        current = {str(record.get("id")): record for record in current_records}
        if args.expect_snapshot:
            snapshot = _load_snapshot(args.expect_snapshot)
            _check_snapshot_identity(snapshot, file_id, sheet)
            conflicts = _snapshot_conflicts(snapshot, sheet, current, ids,
                                            target_fields, all_fields=False)
            if conflicts:
                _print_conflicts(conflicts, will_force=args.force)
                if not args.force:
                    sys.exit(1)
    else:
        current = {}
    body = {
        "prefer_id": True,
        "records": [
            {"id": record_id, "fields_value": json.dumps(expected[record_id], ensure_ascii=False)}
            for record_id in ids
        ],
    }
    if args.dry_run:
        print("预演：未发送更新请求。")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    _api(args.cli, args.token_type, "POST",
         f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet.get('id'))}/records/update",
         body, None, args.timeout, "更新记录")
    readback = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                               ids, False)
    errors = _verify_written(sheet, readback, expected)
    if errors:
        print("⚠️ 更新请求已返回，但读回校验不一致：", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        sys.exit(1)
    print(f"已更新 {len(ids)} 条记录：{', '.join(ids)}")


def _record_ids(items) -> list:
    ids = []
    for item in items or []:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in ids:
                ids.append(part)
    return ids


def cmd_delete(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    ids = _record_ids(args.record)
    if not ids:
        fail("需要 --record（记录 ID）")
    if not args.expect_snapshot and not args.force and not args.dry_run:
        fail("delete 必须先 snapshot，再传 --expect-snapshot；确认覆盖才用 --force")
    if args.expect_snapshot:
        snapshot = _load_snapshot(args.expect_snapshot)
        _check_snapshot_identity(snapshot, file_id, sheet)
        current_records = _search_records(args.cli, file_id, sheet, args.token_type,
                                          args.timeout, ids, False)
        current = {str(record.get("id")): record for record in current_records}
        conflicts = _snapshot_conflicts(snapshot, sheet, current, ids, {}, all_fields=True)
        if conflicts:
            _print_conflicts(conflicts, will_force=args.force)
            if not args.force:
                sys.exit(1)
    elif not args.force and not args.dry_run:
        fail("delete 需要 --expect-snapshot，或显式 --force")
    body = {"records": ids}
    if args.dry_run:
        print("预演：未发送删除请求。")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    data = _api(args.cli, args.token_type, "POST",
                f"/v7/coop/dbsheet/{_quote(file_id)}/sheets/{_quote(sheet.get('id'))}/records/batch_delete",
                body, None, args.timeout, "删除记录")
    deleted = data.get("records") or []
    failed = [str(item.get("id")) for item in deleted if not item.get("deleted")]
    if failed:
        fail(f"以下记录没有删除成功：{failed}；响应：{_short(data)}")
    remaining = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                ids, False)
    if remaining:
        fail(f"删除请求成功，但仍能读到记录 {[r.get('id') for r in remaining]}")
    print(f"已删除 {len(ids)} 条记录：{', '.join(ids)}")


def cmd_snapshot(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    if not args.out:
        fail("snapshot 需要 --out（快照 JSON 路径）")
    if args.record:
        ids = _record_ids(args.record)
    elif args.filter or args.filter_file:
        body = _load_json_arg(args.filter, args.filter_file, "filter")
        body = _normalize_filter(sheet, body)
        ids = []
        for record in _iter_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                    args.page_size, args.max_records, None, body, True, None):
            ids.append(str(record.get("id")))
    else:
        fail("snapshot 需要 --record 或 --filter/--filter-file")
    if not ids:
        fail("没有匹配的记录，未生成快照")
    raw_records = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                  ids, False)
    text_records = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                   ids, True)
    raw_by_id = {str(record.get("id")): record for record in raw_records}
    text_by_id = {str(record.get("id")): record for record in text_records}
    missing = [record_id for record_id in ids if record_id not in raw_by_id]
    if missing:
        fail(f"以下记录读不到，未生成快照：{missing}")
    snapshot = {
        "version": 1,
        "file_id": file_id,
        "sheet_id": sheet.get("id"),
        "sheet_name": sheet.get("name"),
        "schema_hash": schema_hash(sheet),
        "fields": [
            {"id": field.get("id"), "name": field.get("name"), "type": field.get("type")}
            for field in _fields(sheet)
        ],
        "records": {
            record_id: {
                "fields": _decode_fields(raw_by_id[record_id]),
                "text": _decode_fields(text_by_id.get(record_id) or {}),
            }
            for record_id in ids
        },
        "fetched_at": _now_iso(),
    }
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)
    print(f"已记录 {len(ids)} 条快照：{out}")


def cmd_attachments(args) -> None:
    file_id, _schema, sheet = _load_sheet(args)
    if not args.out:
        fail("attachments 需要 --out（下载目录）")
    if args.record:
        ids = _record_ids(args.record)
        records = _search_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                  ids, False)
        if not records:
            fail(f"找不到记录 {ids}")
    elif args.filter or args.filter_file:
        body = _load_json_arg(args.filter, args.filter_file, "filter")
        body = _normalize_filter(sheet, body)
        records = list(_iter_records(args.cli, file_id, sheet, args.token_type, args.timeout,
                                     args.page_size, args.max_records, None, body, False, None))
    else:
        fail("attachments 需要 --record 或 --filter/--filter-file")
    only_field = _resolve_field(sheet, args.field) if args.field else None
    entries = _attachment_entries(sheet, records, only_field)
    if args.images_only:
        entries = [entry for entry in entries if _is_image(entry)]
    if not entries:
        print("没有找到附件。")
        return
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    failures = []
    seen = set()
    index_by_record = {}
    for entry in entries:
        upload_id = entry["upload_id"]
        if upload_id in seen:
            continue
        seen.add(upload_id)
        try:
            info = _attachment_url(args.cli, file_id, upload_id, entry["mime"],
                                   args.token_type, args.timeout)
        except SystemExit as exc:
            failures.append(f"{entry['record_id']}/{entry['filename']}：{exc}")
            continue
        declared = info.get("file_size")
        entry["server_size"] = declared
        entry["url"] = info["url"]
        if args.list_only:
            continue
        fallback_ext = "." + info.get("file_ext", "").lstrip(".") if info.get("file_ext") else ""
        fallback = f"attachment_{upload_id}{fallback_ext}"
        name = _safe_filename(entry["filename"], fallback)
        sequence = index_by_record.get(entry["record_id"], 0)
        index_by_record[entry["record_id"]] = sequence + 1
        local_name = f"{entry['record_id']}_{sequence}_{name}"
        path = os.path.join(out_dir, local_name)
        try:
            entry["bytes"] = _download(info["url"], path, args.timeout)
            entry["path"] = path
            actual_ext = _image_magic_ext(path)
            old_ext = os.path.splitext(path)[1].lower()
            if actual_ext and old_ext in IMAGE_EXTS and actual_ext != old_ext:
                renamed = os.path.splitext(path)[0] + actual_ext
                os.replace(path, renamed)
                entry["path"] = renamed
                entry["note"] = f"服务器实际返回 {actual_ext.upper()}，已按内容改扩展名"
        except Exception as exc:  # urllib errors are varied
            failures.append(f"{entry['record_id']}/{entry['filename']}：下载失败 {exc}")
            continue
    print(f"{'附件清单' if args.list_only else '已下载附件'}（{len(entries)} 项，"
          f"{len(seen)} 个唯一文件）：")
    for entry in entries:
        size = entry.get("bytes") if entry.get("bytes") is not None else entry.get("server_size")
        size_text = f"{size} B" if size is not None else "大小未知"
        line = (f"- {entry['record_id']} {entry['field_name']}：{entry['filename'] or '(无名)'}"
                f" [{entry['mime'] or '未知类型'}] {size_text}")
        if entry.get("path"):
            line += f"\n  本地：{entry['path']}"
            if _is_image(entry):
                line += f"\n  查看图片：read_image(\"{entry['path']}\")"
            if entry.get("note"):
                line += f"\n  说明：{entry['note']}"
        if args.show_urls and entry.get("url"):
            line += f"\n  URL：{entry['url']}"
        print(line)
    if args.list_only:
        return
    if failures:
        print("\n以下附件失败：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------- CLI

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file-id", help="多维表文件 ID（与 --link 二选一）")
    parser.add_argument("--link", help="kdocs/plus 分享链接（与 --file-id 二选一）")
    parser.add_argument("--sheet", help="工作表名或 ID")
    parser.add_argument("--cli", default=DEFAULT_CLI, help=f"wps365-cli 路径（默认 {DEFAULT_CLI}）")
    parser.add_argument("--token-type", default="delegated", choices=("delegated", "app"),
                        help="认证身份（默认 delegated）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"单次 HTTP 超时秒数（默认 {DEFAULT_TIMEOUT}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印请求，不写入")


def _add_list_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--filter", help="过滤条件 JSON")
    parser.add_argument("--filter-file", help="从文件读过滤条件（- 为 stdin）")
    parser.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS,
                        help=f"最多读多少条，0=全部（默认 {DEFAULT_MAX_RECORDS}）")
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE,
                        help=f"每页条数，最大 {MAX_PAGE_SIZE}（默认 {DEFAULT_PAGE_SIZE}）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbsheet.py",
        description="读写 WPS 365 多维表（.dbt）的记录、快照和附件。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    schema = sub.add_parser("schema", help="列出工作表/视图/字段")
    _add_common(schema)
    schema.add_argument("--out", help="把完整 schema JSON 写到文件")
    schema.set_defaults(func=cmd_schema)

    listing = sub.add_parser("list", help="分页/过滤读记录")
    _add_common(listing)
    _add_list_options(listing)
    listing.add_argument("--view", help="视图名或 ID（Form 视图会提示并读底层表）")
    listing.add_argument("--fields", help="逗号分隔的字段名/ID；默认主字段+前 5 个")
    listing.add_argument("--format", choices=("md", "cards", "json", "jsonl"), default="md",
                         help="输出格式（默认 md）")
    listing.add_argument("--out", help="把完整解码结果写到 JSON 文件")
    listing.add_argument("--raw", action="store_true", help="保留原始值（Link 为记录 ID）")
    listing.add_argument("--no-truncate", action="store_true", help="md/cards 不截断长文本")
    listing.set_defaults(func=cmd_list)

    get = sub.add_parser("get", help="读单条记录")
    _add_common(get)
    get.add_argument("--record", required=True, help="记录 ID")
    get.add_argument("--format", choices=("md", "json"), default="md")
    get.add_argument("--raw", action="store_true")
    get.set_defaults(func=cmd_get)

    create = sub.add_parser("create", help="新建记录")
    _add_common(create)
    create.add_argument("--set", action="append", default=[], metavar="字段=值",
                        help="要写入的字段，可重复")
    create.add_argument("--set-json", action="append", default=[], metavar="字段=JSON",
                        help="原样写 JSON 值（高级逃生口，可重复）")
    create.add_argument("--records-file", help="批量记录 JSON 文件（- 为 stdin）")
    create.add_argument("--check-duplicate", metavar="字段1,字段2",
                        help="创建前按这些字段查重")
    create.add_argument("--allow-duplicate", action="store_true", help="查重命中仍创建")
    create.set_defaults(func=cmd_create)

    update = sub.add_parser("update", help="更新记录")
    _add_common(update)
    update.add_argument("--record", action="append", default=[], metavar="ID",
                        help="记录 ID，可重复/逗号分隔")
    update.add_argument("--set", action="append", default=[], metavar="字段=值",
                        help="要更新的字段，可重复")
    update.add_argument("--set-json", action="append", default=[], metavar="字段=JSON",
                        help="原样写 JSON 值（高级逃生口，可重复）")
    update.add_argument("--records-file", help="批量更新 JSON 文件（- 为 stdin）")
    update.add_argument("--expect-snapshot", help="更新前比对的快照 JSON")
    update.add_argument("--force", action="store_true", help="跳过冲突检查（危险）")
    update.set_defaults(func=cmd_update)

    delete = sub.add_parser("delete", help="删除记录")
    _add_common(delete)
    delete.add_argument("--record", action="append", default=[], metavar="ID",
                        help="记录 ID，可重复/逗号分隔")
    delete.add_argument("--expect-snapshot", help="删除前比对的快照 JSON")
    delete.add_argument("--force", action="store_true", help="跳过冲突检查（危险）")
    delete.set_defaults(func=cmd_delete)

    snapshot = sub.add_parser("snapshot", help="导出记录级快照")
    _add_common(snapshot)
    _add_list_options(snapshot)
    snapshot.add_argument("--record", action="append", default=[], metavar="ID")
    snapshot.add_argument("--out", required=True, help="快照 JSON 路径")
    snapshot.set_defaults(func=cmd_snapshot)

    attachments = sub.add_parser("attachments", help="列出/下载记录附件")
    _add_common(attachments)
    _add_list_options(attachments)
    attachments.add_argument("--record", action="append", default=[], metavar="ID")
    attachments.add_argument("--field", help="只处理这个附件字段")
    attachments.add_argument("--out", required=True, help="下载目录")
    attachments.add_argument("--images-only", action="store_true", help="只处理图片")
    attachments.add_argument("--list-only", action="store_true", help="只列元信息，不下载")
    attachments.add_argument("--show-urls", action="store_true", help="打印带签名的下载 URL")
    attachments.set_defaults(func=cmd_attachments)

    return parser


def main() -> None:
    _use_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "page_size", 1) < 1:
        parser.error("--page-size 必须 >= 1")
    args.func(args)


if __name__ == "__main__":
    main()
