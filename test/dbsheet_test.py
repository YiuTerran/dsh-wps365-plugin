#!/usr/bin/env python3
"""Pure-function and offline download tests for dbsheet.py.

No network, no cloud calls: the schema/records below are synthetic. The one
socket in this file is a loopback HTTP server serving a fixed PNG.
"""
from __future__ import annotations

import contextlib
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "wps365-docs", "scripts")
sys.path.insert(0, SCRIPTS)

import dbsheet  # noqa: E402


def rec(record_id: str, fields: dict) -> dict:
    return {"id": record_id, "fields": json.dumps(fields, ensure_ascii=False)}


SCHEMA = {
    "sheets": [
        {
            "id": 12,
            "name": "周报",
            "sheet_type": "xlEtDataBaseSheet",
            "records_count": 2,
            "primary_field_id": "F",
            "fields": [
                {"id": "F", "name": "日期", "type": "Date",
                 "data": {"number_format": "yyyy/MM/dd;@"}},
                {"id": "T", "name": "总结", "type": "MultiLineText"},
                {"id": "S", "name": "状态", "type": "SingleSelect",
                 "data": {"items": [{"id": "s1", "value": "待办"},
                                    {"id": "s2", "value": "完成"}]}},
                {"id": "M", "name": "标签", "type": "MultipleSelect",
                 "data": {"items": [{"id": "m1", "value": "甲"},
                                    {"id": "m2", "value": "乙"}]}},
                {"id": "L", "name": "成员", "type": "Link",
                 "data": {"link_sheet": 15, "multiple_links": True}},
                {"id": "A", "name": "附件", "type": "Attachment"},
                {"id": "X", "name": "公式", "type": "Formula"},
                {"id": "O", "name": "单向", "type": "OneWayLink"},
            ],
            "views": [
                {"id": "E", "name": "提交", "type": "Form", "records_count": 0,
                 "fields_attribute": [{"field": "总结", "hidden": False},
                                      {"field": "公式", "hidden": True}]},
                {"id": "C", "name": "明细", "type": "Grid", "records_count": 0},
            ],
        },
        {
            "id": 15,
            "name": "成员表",
            "sheet_type": "xlEtDataBaseSheet",
            "records_count": 2,
            "primary_field_id": "BN",
            "fields": [{"id": "BN", "name": "成员", "type": "Contact"}],
            "views": [{"id": "K", "name": "成员", "type": "Grid", "records_count": 0}],
        },
    ]
}
SHEET = SCHEMA["sheets"][0]
MEMBERS = SCHEMA["sheets"][1]


class SchemaTest(unittest.TestCase):
    def test_resolve_sheet_by_id_and_name(self):
        self.assertEqual(dbsheet._resolve_sheet(SCHEMA, 12)["name"], "周报")
        self.assertEqual(dbsheet._resolve_sheet(SCHEMA, "周报")["id"], 12)
        with self.assertRaises(SystemExit):
            dbsheet._resolve_sheet(SCHEMA, "不存在")

    def test_resolve_field_by_id_and_name(self):
        self.assertEqual(dbsheet._resolve_field(SHEET, "T")["name"], "总结")
        self.assertEqual(dbsheet._resolve_field(SHEET, "总结")["id"], "T")
        with self.assertRaises(SystemExit):
            dbsheet._resolve_field(SHEET, "不存在的字段")

    def test_schema_hash_changes_with_type_or_options(self):
        first = dbsheet.schema_hash(SHEET)
        self.assertEqual(first, dbsheet.schema_hash(json.loads(json.dumps(SHEET))))
        changed = json.loads(json.dumps(SHEET))
        changed["fields"][2]["data"]["items"].append({"id": "s3", "value": "挂起"})
        self.assertNotEqual(first, dbsheet.schema_hash(changed))

    def test_render_schema_marks_form_and_read_only_fields(self):
        rendered = dbsheet._render_schema(SCHEMA)
        self.assertIn("12 周报", rendered)
        self.assertIn("Form", rendered)
        self.assertIn("表单 E 提交 可见字段：总结", rendered)
        self.assertIn("隐藏字段：公式", rendered)
        self.assertIn("公式 : Formula（只读/不支持直接写）", rendered)
        self.assertIn("→ 成员表(15)", rendered)


class FilterTest(unittest.TestCase):
    def test_field_names_become_ids_and_op_alias_is_normalized(self):
        body = {"criteria": [{"field": "成员", "op": "Intersected",
                              "values": ["陈澍"]}]}
        normalized = dbsheet._normalize_filter(SHEET, body)
        self.assertEqual(normalized["mode"], "And")
        self.assertEqual(normalized["criteria"][0]["field"], "L")
        self.assertEqual(normalized["criteria"][0]["operator"], "Intersected")
        # Link filter values are display names; they must not be rewritten.
        self.assertEqual(normalized["criteria"][0]["values"], ["陈澍"])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(SystemExit):
            dbsheet._normalize_filter(SHEET, {"criteria": [{"field": "无", "operator": "Equals"}]})


class ValueTest(unittest.TestCase):
    def test_text_number_and_checkbox(self):
        self.assertEqual(dbsheet._coerce_value(SHEET["fields"][1], 42), "42")
        self.assertEqual(dbsheet._coerce_value(SHEET["fields"][1], None), "")
        # Use a synthetic Number field; the schema above has no number field.
        number = {"id": "N", "name": "数量", "type": "Number"}
        self.assertEqual(dbsheet._coerce_value(number, "0"), 0)
        self.assertEqual(dbsheet._coerce_value(number, "3.5"), 3.5)
        with self.assertRaises(SystemExit):
            dbsheet._coerce_value(number, "abc")
        checkbox = {"id": "B", "name": "完成", "type": "Checkbox"}
        self.assertTrue(dbsheet._coerce_value(checkbox, "是"))
        self.assertFalse(dbsheet._coerce_value(checkbox, "false"))
        with self.assertRaises(SystemExit):
            dbsheet._coerce_value(checkbox, "maybe")

    def test_date_formats_and_invalid_date(self):
        date = SHEET["fields"][0]
        self.assertEqual(dbsheet._coerce_value(date, "2026/10/19"), "2026/10/19")
        self.assertEqual(dbsheet._coerce_value(date, "2026-10-19"), "2026/10/19")
        with self.assertRaises(SystemExit):
            dbsheet._coerce_value(date, "2026/13/40")

    def test_select_by_name_or_id_and_invalid_option(self):
        status = SHEET["fields"][2]
        self.assertEqual(dbsheet._coerce_value(status, "完成"), "完成")
        self.assertEqual(dbsheet._coerce_value(status, "s1"), "待办")
        with self.assertRaises(SystemExit):
            dbsheet._coerce_value(status, "不存在")

    def test_multi_select_json_or_comma(self):
        tags = SHEET["fields"][3]
        self.assertEqual(dbsheet._coerce_value(tags, '["甲","乙"]'), ["甲", "乙"])
        self.assertEqual(dbsheet._coerce_value(tags, "甲,乙"), ["甲", "乙"])
        self.assertEqual(dbsheet._coerce_value(tags, ""), [])

    def test_read_only_types_are_rejected(self):
        for field in (SHEET["fields"][6], SHEET["fields"][7]):
            with self.assertRaises(SystemExit):
                dbsheet._coerce_value(field, "x")
        attachment = SHEET["fields"][5]
        with self.assertRaises(SystemExit):
            dbsheet._coerce_value(attachment, "x")

    def test_percentage_accepts_percent_suffix(self):
        percent = {"id": "P", "name": "成功率", "type": "Percentage"}
        self.assertAlmostEqual(dbsheet._coerce_value(percent, "12%"), 0.12)
        self.assertAlmostEqual(dbsheet._coerce_value(percent, 0.5), 0.5)


class EqualityTest(unittest.TestCase):
    def test_checkbox_text_values_are_parsed_instead_of_using_truthiness(self):
        field = {"id": "B", "name": "完成", "type": "Checkbox"}
        for value in (False, 0, "0", "false", "否", "", None):
            with self.subTest(value=value):
                self.assertTrue(dbsheet._values_equal(field, False, value))
                self.assertFalse(dbsheet._values_equal(field, True, value))
        self.assertFalse(dbsheet._values_equal(field, True, "invalid"))

    def test_date_comparison_respects_second_precision(self):
        field = {"id": "D", "type": "Date",
                 "data": {"number_format": "yyyy/MM/dd hh:mm:ss;@"}}
        self.assertFalse(dbsheet._values_equal(
            field, "2026/10/19 10:30:01", "2026/10/19 10:30:59"))
        field["data"]["number_format"] = 'yyyy/MM/dd hh:mm "seconds";@'
        self.assertTrue(dbsheet._values_equal(
            field, "2026/10/19 10:30:01", "2026/10/19 10:30:59"))

    def test_link_and_multi_compare_as_sets(self):
        link = SHEET["fields"][4]
        self.assertTrue(dbsheet._values_equal(link, ["CN", "CP"], ["CP", "CN"]))
        self.assertFalse(dbsheet._values_equal(link, ["CN"], ["CP"]))
        tags = SHEET["fields"][3]
        self.assertTrue(dbsheet._values_equal(tags, ["甲", "乙"], ["乙", "甲"]))

    def test_number_checkbox_and_date_are_normalized(self):
        number = {"id": "N", "name": "数量", "type": "Number"}
        self.assertTrue(dbsheet._values_equal(number, 42, "42.00"))
        checkbox = {"id": "B", "name": "完成", "type": "Checkbox"}
        self.assertTrue(dbsheet._values_equal(checkbox, True, "1"))
        date = SHEET["fields"][0]
        self.assertTrue(dbsheet._values_equal(date, "2026-10-19", "2026/10/19"))

    def test_invalid_stored_select_does_not_crash_comparison(self):
        status = SHEET["fields"][2]
        self.assertFalse(dbsheet._values_equal(status, "完成", "脏数据"))


class BuildRecordTest(unittest.TestCase):
    def test_fields_use_ids_and_fields_value_is_a_string(self):
        built = dbsheet._build_record(SHEET, {"fields": {"总结": "内容", "状态": "待办"}}, None, True)
        self.assertIsInstance(built["fields_value"], str)
        parsed = json.loads(built["fields_value"])
        self.assertEqual(parsed, {"T": "内容", "S": "待办"})

    def test_json_fields_bypass_coercion_but_still_resolve_ids(self):
        built = dbsheet._build_record(
            SHEET, {"json_fields": {"状态": ""}}, None, True)
        self.assertEqual(json.loads(built["fields_value"]), {"S": ""})

    def test_json_fields_still_reject_read_only_types(self):
        sheet = {"fields": SHEET["fields"] + [{"id": "A", "name": "附件", "type": "Attachment"}]}
        with self.assertRaises(SystemExit):
            dbsheet._build_record(sheet, {"json_fields": {"A": "x"}}, None, True)

    def test_mixing_same_field_in_set_and_set_json_is_rejected(self):
        with self.assertRaises(SystemExit):
            dbsheet._build_record(
                SHEET, {"fields": {"总结": "a"}, "json_fields": {"总结": "b"}}, None, True)

    def test_fields_value_keys_must_be_ids(self):
        with self.assertRaises(SystemExit):
            dbsheet._build_record(
                SHEET, {"fields_value": '{"总结": "x"}'}, None, True)
        built = dbsheet._build_record(
            SHEET, {"fields_value": '{"T": "x"}'}, None, True)
        self.assertEqual(json.loads(built["fields_value"]), {"T": "x"})


class LinkResolverTest(unittest.TestCase):
    def test_display_name_resolves_and_unknown_name_fails(self):
        target_records = [rec("CN", {"BN": "陈澍"}), rec("CP", {"BN": "芦宇轩"})]

        def fake_iter(*args, **kwargs):
            return iter(target_records)

        def fake_search(*args, **kwargs):
            ids = args[5]
            return [rec("CN", {"BN": "陈澍"})] if ids == ["CN"] else []

        with mock.patch.object(dbsheet, "_iter_records", side_effect=fake_iter), \
                mock.patch.object(dbsheet, "_search_records", side_effect=fake_search):
            resolver = dbsheet._build_link_maps(SCHEMA, "cli", "file", "delegated", 1)
            link = SHEET["fields"][4]
            self.assertEqual(resolver(link, ["陈澍"]), ["CN"])
            self.assertEqual(resolver(link, ["CN"]), ["CN"])
            with self.assertRaises(SystemExit):
                resolver(link, ["查无此人"])

    def test_ambiguous_name_fails(self):
        target_records = [rec("A1", {"BN": "同名"}), rec("A2", {"BN": "同名"})]

        with mock.patch.object(dbsheet, "_iter_records", return_value=iter(target_records)), \
                mock.patch.object(dbsheet, "_search_records", return_value=[]):
            resolver = dbsheet._build_link_maps(SCHEMA, "cli", "file", "delegated", 1)
            with self.assertRaises(SystemExit):
                resolver(SHEET["fields"][4], ["同名"])


class SnapshotTest(unittest.TestCase):
    def test_second_only_date_change_blocks_delete_and_fails_readback(self):
        field = {"id": "D", "name": "时间", "type": "Date",
                 "data": {"number_format": "yyyy/MM/dd hh:mm:ss;@"}}
        sheet = {"fields": [field]}
        old, new = "2026/10/19 10:30:01", "2026/10/19 10:30:59"
        snapshot = {"records": {"r1": {"fields": {"D": old}}}}
        record = rec("r1", {"D": new})
        self.assertEqual(len(dbsheet._snapshot_conflicts(
            snapshot, sheet, {"r1": record}, ["r1"], {}, all_fields=True)), 1)
        self.assertEqual(len(dbsheet._verify_written(sheet, [record], {"r1": {"D": old}})), 1)

    def setUp(self):
        self.snapshot = {
            "version": 1,
            "file_id": "file",
            "sheet_id": 12,
            "schema_hash": dbsheet.schema_hash(SHEET),
            "records": {"r1": {"fields": {"T": "旧", "F": "2026/10/19"}}},
        }

    def test_target_field_change_conflicts(self):
        current = {"r1": rec("r1", {"T": "新", "F": "2026/10/19"})}
        conflicts = dbsheet._snapshot_conflicts(self.snapshot, SHEET, current, ["r1"],
                                                {"r1": ["T"]}, all_fields=False)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["field_id"], "T")

    def test_unrelated_field_change_is_allowed_for_update(self):
        current = {"r1": rec("r1", {"T": "旧", "F": "2026/10/20"})}
        conflicts = dbsheet._snapshot_conflicts(self.snapshot, SHEET, current, ["r1"],
                                                {"r1": ["T"]}, all_fields=False)
        self.assertEqual(conflicts, [])

    def test_delete_compares_all_fields(self):
        current = {"r1": rec("r1", {"T": "旧", "F": "2026/10/20"})}
        conflicts = dbsheet._snapshot_conflicts(self.snapshot, SHEET, current, ["r1"],
                                                {}, all_fields=True)
        self.assertEqual(conflicts[0]["field_id"], "F")

    def test_missing_record_and_missing_snapshot_record_conflict(self):
        current = {}
        conflicts = dbsheet._snapshot_conflicts(self.snapshot, SHEET, current, ["r1"],
                                                {"r1": ["T"]}, all_fields=False)
        self.assertEqual(conflicts[0]["field_name"], "记录")
        conflicts = dbsheet._snapshot_conflicts(self.snapshot, SHEET,
                                                {"r2": rec("r2", {})}, ["r2"],
                                                {"r2": ["T"]}, all_fields=False)
        self.assertEqual(conflicts[0]["record_id"], "r2")

    def test_identity_checks_file_sheet_and_schema(self):
        dbsheet._check_snapshot_identity(self.snapshot, "file", SHEET)
        with self.assertRaises(SystemExit):
            dbsheet._check_snapshot_identity(self.snapshot, "other", SHEET)
        with self.assertRaises(SystemExit):
            dbsheet._check_snapshot_identity(self.snapshot, "file", SCHEMA["sheets"][1])
        changed = json.loads(json.dumps(self.snapshot))
        changed["schema_hash"] = "stale"
        with self.assertRaises(SystemExit):
            dbsheet._check_snapshot_identity(changed, "file", SHEET)


class AttachmentTest(unittest.TestCase):
    def test_raw_entries_are_extracted_and_text_mode_is_ignored(self):
        raw = rec("r1", {"A": [{"fileName": "图.png", "type": "image/png",
                                "uploadId": "U1", "size": 10}]})
        entries = dbsheet._attachment_entries(SHEET, [raw])
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["upload_id"], "U1")
        self.assertTrue(dbsheet._is_image(entries[0]))
        text = rec("r1", {"A": "图.png"})
        self.assertEqual(dbsheet._attachment_entries(SHEET, [text]), [])

    def test_duplicate_upload_ids_are_collapsed_per_record(self):
        raw = rec("r1", {"A": [{"fileName": "a.png", "uploadId": "U1"},
                               {"fileName": "a-copy.png", "uploadId": "U1"}]})
        self.assertEqual(len(dbsheet._attachment_entries(SHEET, [raw])), 1)

    def test_safe_filename_strips_paths_and_reserved_names(self):
        self.assertEqual(dbsheet._safe_filename("../../etc/passwd", "fallback"), "passwd")
        cleaned = dbsheet._safe_filename("CON.png", "fallback")
        self.assertTrue(cleaned.startswith("_"))
        self.assertNotIn("/", dbsheet._safe_filename("a/b\\c:d?.png", "fallback"))

    def test_non_image_is_not_flagged(self):
        entry = {"filename": "表.xlsx", "mime": "xlsx"}
        self.assertFalse(dbsheet._is_image(entry))

    def test_magic_extension(self):
        cases = {
            b"\x89PNG\r\n\x1a\n" + b"0" * 8: ".png",
            b"\xff\xd8\xff\xe0" + b"0" * 8: ".jpg",
            b"RIFF" + b"0" * 4 + b"WEBP" + b"0" * 8: ".webp",
            b"GIF89a" + b"0" * 8: ".gif",
            b"not an image": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for index, (blob, expected) in enumerate(cases.items()):
                path = os.path.join(tmp, f"{index}.bin")
                with open(path, "wb") as handle:
                    handle.write(blob)
                self.assertEqual(dbsheet._image_magic_ext(path), expected)


class DownloadTest(unittest.TestCase):
    BLOB = b"\x89PNG\r\n\x1a\n" + b"payload"

    def test_download_streams_to_disk_and_magic_is_detected(self):
        blob = self.BLOB

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "x.bin")
                url = f"http://127.0.0.1:{server.server_port}/x"
                self.assertEqual(dbsheet._download(url, path, 5), len(blob))
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), blob)
                self.assertEqual(dbsheet._image_magic_ext(path), ".png")
        finally:
            server.shutdown()
            server.server_close()


class PaginationTest(unittest.TestCase):
    def test_follows_tokens_and_honours_max_records(self):
        pages = [
            {"records": [rec("r1", {}), rec("r2", {})], "page_token": "t1"},
            {"records": [rec("r3", {})], "page_token": ""},
        ]

        def fake_page(*args, **kwargs):
            return pages.pop(0)

        with mock.patch.object(dbsheet, "_list_page", side_effect=fake_page):
            got = list(dbsheet._iter_records("cli", "file", SHEET, "delegated", 1,
                                             100, 2, None, None, True, None))
        self.assertEqual([item["id"] for item in got], ["r1", "r2"])

    def test_repeated_token_stops(self):
        pages = [
            {"records": [rec("r1", {})], "page_token": "same"},
            {"records": [rec("r2", {})], "page_token": "same"},
        ]

        with mock.patch.object(dbsheet, "_list_page", side_effect=lambda *a, **k: pages.pop(0)):
            got = list(dbsheet._iter_records("cli", "file", SHEET, "delegated", 1,
                                             100, 0, None, None, True, None))
        self.assertEqual([item["id"] for item in got], ["r1", "r2"])


class RenderTest(unittest.TestCase):
    def test_text_mode_checkbox_zero_is_rendered_as_unchecked(self):
        field = {"id": "B", "name": "完成", "type": "Checkbox"}
        for value in ("0", "false", "否", False):
            record = {"id": "r1", "fields": {"完成": value}}
            with self.subTest(value=value):
                self.assertIn("| 否 |", dbsheet._render_table([record], [field], False))
                self.assertIn("**完成**：否", dbsheet._render_cards([record], [field], False))

    def test_table_escapes_pipes_and_truncates(self):
        fields = [SHEET["fields"][1]]
        record = {"id": "r1", "fields": {"总结": "a|b\n" + "x" * 80}}
        table = dbsheet._render_table([record], fields, truncate=True)
        self.assertIn("a\\|b<br>", table)
        self.assertIn("…", table)

    def test_cards_render_checkbox_as_chinese(self):
        checkbox = {"id": "B", "name": "完成", "type": "Checkbox"}
        record = {"id": "r1", "fields": {"完成": False}}
        cards = dbsheet._render_cards([record], [checkbox], truncate=False)
        self.assertIn("**完成**：否", cards)


class InputTest(unittest.TestCase):
    def test_parse_set_and_set_json(self):
        self.assertEqual(dbsheet._parse_set(["总结=a=b"]), [("总结", "a=b")])
        with self.assertRaises(SystemExit):
            dbsheet._parse_set(["没有等号"])
        self.assertEqual(dbsheet._parse_set_json(['状态=""']), [("状态", "")])
        with self.assertRaises(SystemExit):
            dbsheet._parse_set_json(["状态=不是JSON"])

    def test_records_file_requires_non_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "records.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"fields": {"总结": "x"}}], handle, ensure_ascii=False)
            self.assertEqual(len(dbsheet._read_records_file(path)), 1)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([], handle)
            with self.assertRaises(SystemExit):
                dbsheet._read_records_file(path)

    def test_parser_exposes_set_json(self):
        parser = dbsheet.build_parser()
        args = parser.parse_args(["update", "--file-id", "f", "--sheet", "12",
                                  "--record", "r1", "--set-json", '状态=""'])
        self.assertEqual(args.set_json, ['状态=""'])


class WriteGuardTest(unittest.TestCase):
    def test_failed_duplicate_query_does_not_send_create_even_when_allowed(self):
        for flags in ([], ["--allow-duplicate"]):
            args = dbsheet.build_parser().parse_args([
                "create", "--file-id", "file", "--sheet", "12", "--set", "总结=内容",
                "--check-duplicate", "总结", *flags])
            with self.subTest(flags=flags), \
                    mock.patch.object(dbsheet, "_load_sheet", return_value=("file", SCHEMA, SHEET)), \
                    mock.patch.object(dbsheet, "_iter_records", side_effect=SystemExit("HTTP 429")), \
                    mock.patch.object(dbsheet, "_api") as api, \
                    self.assertRaisesRegex(SystemExit, "自动查重失败.*已放弃创建"):
                dbsheet.cmd_create(args)
            api.assert_not_called()

    def test_duplicate_ids_are_rejected_before_any_update_or_current_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "records.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([{"id": "r1", "fields": {"总结": "a"}},
                           {"id": "r1", "fields": {"状态": "完成"}}], handle)
            args = dbsheet.build_parser().parse_args([
                "update", "--file-id", "file", "--sheet", "12",
                "--records-file", path, "--force"])
            with mock.patch.object(dbsheet, "_load_sheet", return_value=("file", SCHEMA, SHEET)), \
                    mock.patch.object(dbsheet, "_search_records") as search, \
                    mock.patch.object(dbsheet, "_api") as api, \
                    self.assertRaisesRegex(SystemExit, "记录 ID r1 重复"):
                dbsheet.cmd_update(args)
            api.assert_not_called()
            search.assert_not_called()

    def test_link_id_and_name_both_use_display_name_for_duplicate_filter(self):
        for value in ("CN", "陈澍"):
            args = dbsheet.build_parser().parse_args([
                "create", "--file-id", "file", "--sheet", "12", "--set", f"成员={value}",
                "--check-duplicate", "成员"])

            def records(*pos, **kwargs):
                if pos[2]["id"] == MEMBERS["id"]:
                    return iter([rec("CN", {"BN": "陈澍"})])
                self.assertEqual(pos[8]["criteria"][0]["values"], ["陈澍"])
                return iter([rec("existing", {"L": ["CN"]})])

            with self.subTest(value=value), \
                    mock.patch.object(dbsheet, "_load_sheet", return_value=("file", SCHEMA, SHEET)), \
                    mock.patch.object(dbsheet, "_iter_records", side_effect=records), \
                    mock.patch.object(dbsheet, "_search_records", return_value=[rec("CN", {"BN": "陈澍"})]), \
                    mock.patch.object(dbsheet, "_api") as api, \
                    self.assertRaisesRegex(SystemExit, "发现 1 条可能重复"):
                dbsheet.cmd_create(args)
            api.assert_not_called()

    def test_missing_link_display_name_aborts_creation(self):
        args = dbsheet.build_parser().parse_args([
            "create", "--file-id", "file", "--sheet", "12", "--set", "成员=CN",
            "--check-duplicate", "成员"])
        with mock.patch.object(dbsheet, "_load_sheet", return_value=("file", SCHEMA, SHEET)), \
                mock.patch.object(dbsheet, "_build_link_maps", return_value=lambda *_: ["CN"]), \
                mock.patch.object(dbsheet, "_search_records", return_value=[]), \
                mock.patch.object(dbsheet, "_api") as api, \
                self.assertRaisesRegex(SystemExit, "显示名读不到.*已放弃创建"):
            dbsheet.cmd_create(args)
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
