# -*- coding: utf-8 -*-
"""Offline tests for adb_ui.py. No device or adb binary is required."""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import adb_ui  # noqa: E402


SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="12:00" resource-id="com.android.systemui:id/clock"
        class="android.widget.TextView" package="com.android.systemui"
        content-desc="" clickable="false" long-clickable="false"
        scrollable="false" checkable="false" checked="false"
        enabled="true" focused="false" selected="false" password="false"
        bounds="[0,0][80,30]" />
  <node index="1" text="返回" resource-id="com.xingin.xhs:id/back"
        class="android.widget.ImageButton" package="com.xingin.xhs"
        content-desc="返回" clickable="true" long-clickable="false"
        scrollable="false" checkable="false" checked="false"
        enabled="true" focused="false" selected="false" password="false"
        bounds="[0,100][80,180]" />
  <node index="2" text="评论 168" resource-id="com.xingin.xhs:id/comment"
        class="android.widget.TextView" package="com.xingin.xhs"
        content-desc="" clickable="true" long-clickable="false"
        scrollable="false" checkable="false" checked="false"
        enabled="true" focused="false" selected="false" password="false"
        bounds="[100,200][300,260]" />
  <node index="3" text="评论 168" resource-id="com.xingin.xhs:id/comment"
        class="android.widget.TextView" package="com.xingin.xhs"
        content-desc="" clickable="false" long-clickable="false"
        scrollable="false" checkable="false" checked="false"
        enabled="true" focused="false" selected="false" password="false"
        bounds="[100,200][300,260]" />
  <node index="4" text="" content-desc="头像"
        resource-id="com.xingin.xhs:id/avatar"
        class="android.widget.ImageView" package="com.xingin.xhs"
        clickable="true" long-clickable="false" scrollable="false"
        checkable="false" checked="false" enabled="true" focused="false"
        selected="false" password="false" bounds="[400,100][500,200]" />
  <node index="5" text="" content-desc="" resource-id=""
        class="android.view.View" package="com.xingin.xhs"
        clickable="false" long-clickable="false" scrollable="false"
        checkable="false" checked="false" enabled="true" focused="false"
        selected="false" password="false" bounds="[0,0][0,0]" />
</hierarchy>
"""


def fake_proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class ParseNodesTests(unittest.TestCase):
    def test_parse_order_center_and_dedupe(self):
        nodes = adb_ui.parse_nodes(SAMPLE_XML)
        self.assertEqual(
            [(node["text"] or node["desc"]) for node in nodes],
            ["12:00", "返回", "头像", "评论 168"],
        )
        comment = next(node for node in nodes if node["text"] == "评论 168")
        self.assertEqual(comment["box"], [100, 200, 300, 260])
        self.assertEqual(comment["center"], [200, 230])
        self.assertTrue(comment["clickable"], "duplicate should keep the clickable copy")
        self.assertEqual(comment["index"], 3)
        avatar = next(node for node in nodes if node["desc"] == "头像")
        self.assertEqual(avatar["label"], "头像")

    def test_choose_package_ignores_systemui(self):
        nodes = adb_ui.parse_nodes(SAMPLE_XML)
        self.assertEqual(adb_ui.choose_package(nodes), "com.xingin.xhs")

    def test_screen_size(self):
        nodes = adb_ui.parse_nodes(SAMPLE_XML)
        self.assertEqual(adb_ui.screen_from_nodes(nodes), (500, 260))

    def test_filter(self):
        nodes = adb_ui.parse_nodes(SAMPLE_XML)
        args = SimpleNamespace(all=False, contains="评论", regex=None, clickable=False)
        rows = adb_ui.filter_dump_nodes(nodes, args)
        self.assertEqual([node["text"] for node in rows], ["评论 168"])

    def test_invalid_xml(self):
        with self.assertRaises(adb_ui.AdbError):
            adb_ui.parse_nodes("<hierarchy><node></hierarchy>")


class DeviceParsingTests(unittest.TestCase):
    def test_parse_devices(self):
        proc = fake_proc(
            stdout=(
                "List of devices attached\n"
                "192.0.2.50:41381 device product:cepheus model:MI_9 "
                "device:cepheus transport_id:1\n"
                "adb server version (41) doesn't match this client (40); killing...\n"
            )
        )
        with patch.object(adb_ui, "adb_cmd", return_value=proc):
            devices = adb_ui.parse_devices("adb")
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["serial"], "192.0.2.50:41381")
        self.assertEqual(devices[0]["state"], "device")
        self.assertEqual(devices[0]["model"], "MI_9")
        self.assertIn("MI_9", adb_ui.format_devices(devices))

    def test_duplicate_transport_hint(self):
        devices = [
            {"serial": "192.0.2.50:41381", "model": "MI_9", "device": "cepheus"},
            {
                "serial": "adb-abc._adb-tls-connect._tcp",
                "model": "MI_9",
                "device": "cepheus",
            },
        ]
        self.assertIn("IP:PORT", adb_ui.duplicate_hint(devices))
        self.assertEqual(adb_ui.duplicate_hint(devices[:1]), "")


class SafetyTests(unittest.TestCase):
    def test_wake_keys_are_blocked(self):
        self.assertEqual(adb_ui.resolve_keycode("back"), 4)
        self.assertEqual(adb_ui.resolve_keycode("BACK"), 4)
        self.assertEqual(adb_ui.resolve_keycode("66"), 66)
        for raw in ("224", "223", "26"):
            with self.assertRaises(adb_ui.AdbError):
                adb_ui.resolve_keycode(raw)
        for raw in ("wake", "wakeup", "power", "sleep"):
            with self.assertRaises(adb_ui.AdbError):
                adb_ui.resolve_keycode(raw)

    def test_wake_command_is_gone(self):
        parser = adb_ui.build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["wake"])

    def test_cmd_key_rejects_before_touching_device(self):
        with patch.object(adb_ui, "select_serial", return_value="SERIAL"), patch.object(
            adb_ui, "adb_cmd"
        ) as adb_mock:
            with self.assertRaises(adb_ui.AdbError):
                adb_ui.cmd_key("adb", None, SimpleNamespace(key="224", wait=0))
        adb_mock.assert_not_called()

    def test_auto_unlock_primitive_absent_from_script(self):
        source = (ROOT / "scripts" / "adb_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("dismiss-keyguard", source)

    def test_screen_command_is_read_only(self):
        parser = adb_ui.build_parser()
        args = parser.parse_args(["screen"])
        self.assertEqual(args.func, adb_ui.cmd_screen)

    def test_parse_screen_state(self):
        self.assertEqual(
            adb_ui.parse_screen_state("Display Power: state=OFF\n", "mShowingLockscreen=true\n"),
            {"screen": "off", "keyguard": "showing"},
        )
        self.assertEqual(
            adb_ui.parse_screen_state(
                "mWakefulness=Awake\n",
                "mKeyguardShowing=false\nisStatusBarKeyguard=false\n",
            ),
            {"screen": "on", "keyguard": "not showing"},
        )
        self.assertEqual(
            adb_ui.parse_screen_state("", ""),
            {"screen": "unknown", "keyguard": "unknown"},
        )

    def test_cmd_screen_reports_locked_state(self):
        def fake_cmd(_adb, _serial, command, timeout=60, binary=False):
            if "power" in command:
                return fake_proc("Display Power: state=OFF\n")
            return fake_proc("mShowingLockscreen=true\n")

        with patch.object(adb_ui, "select_serial", return_value="SERIAL"), patch.object(
            adb_ui, "adb_cmd", side_effect=fake_cmd
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = adb_ui.cmd_screen(
                    "adb", None, SimpleNamespace(json=False)
                )
        self.assertEqual(rc, 0)
        text = output.getvalue()
        self.assertIn("screen:   off", text)
        self.assertIn("keyguard: showing", text)
        self.assertIn("ready:    no", text)
        self.assertIn("手动点亮并解锁", text)


class CliTests(unittest.TestCase):
    def test_extract_globals(self):
        cleaned, adb_path, serial = adb_ui.extract_globals(
            ["--serial", "abc", "dump", "--json", "--adb", "/opt/adb"]
        )
        self.assertEqual(cleaned, ["dump", "--json"])
        self.assertEqual(serial, "abc")
        self.assertEqual(adb_path, "/opt/adb")

    def test_quote_roundtrip(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = adb_ui.cmd_quote(None, None, SimpleNamespace(text="深圳 找对象"))
        self.assertEqual(rc, 0)
        self.assertEqual(urllib.parse.unquote(output.getvalue().strip()), "深圳 找对象")

    def test_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exc:
                adb_ui.main(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn(adb_ui.VERSION, output.getvalue())


if __name__ == "__main__":
    unittest.main()
