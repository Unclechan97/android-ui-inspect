#!/usr/bin/env python3
"""Cross-platform ADB UI inspector.

Dependencies: Python 3.8+, Android platform-tools (adb), an authorized Android device.
No third-party Python packages are required.

Examples:
    python adb_ui.py devices
    python adb_ui.py screen --serial 192.0.2.50:41381
    python adb_ui.py info --serial 192.0.2.50:41381
    python adb_ui.py dump --serial 192.0.2.50:41381 --contains 评论
    python adb_ui.py find "评论" --serial 192.0.2.50:41381 --tap
    python adb_ui.py open "xhsdiscover://search/result?keyword=..." --serial ...

Safety: this tool never wakes or unlocks the device. Ask the user to light up
and unlock the screen first; locked-screen commands fail or return lock nodes.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
from collections import Counter
from xml.etree import ElementTree as ET

VERSION = "1.1.0"

BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")

# Deliberately no power/wake/sleep keys: the skill must not wake or unlock
# a device. The user lights up and unlocks the screen manually.
KEYCODES = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "tab": 61,
    "delete": 67,
    "backspace": 67,
    "escape": 111,
    "menu": 82,
    "app_switch": 187,
    "recents": 187,
    "volume_up": 24,
    "volume_down": 25,
}

BLOCKED_KEYCODES = {
    26: "power",
    223: "sleep",
    224: "wakeup",
}
BLOCKED_KEY_NAMES = {"power", "wake", "wakeup", "sleep"}


class AdbError(RuntimeError):
    pass


def _blocked_key_error(raw: str) -> AdbError:
    return AdbError(
        f"已拒绝按键 {raw!r}：本技能不自动唤醒或解锁设备。"
        "请让用户手动点亮并解锁屏幕，然后重试。"
    )


def resolve_keycode(raw: str) -> int:
    """Resolve a key name/number, refusing keys that can wake the device."""
    raw = str(raw).strip()
    if raw.lower() in BLOCKED_KEY_NAMES:
        raise _blocked_key_error(raw)
    keycode = KEYCODES.get(raw.lower())
    if keycode is None:
        if re.fullmatch(r"\d+", raw):
            keycode = int(raw)
        else:
            raise AdbError(
                f"未知按键：{raw}。可用：{', '.join(sorted(KEYCODES))}，或直接给数字 keycode。"
            )
    if keycode in BLOCKED_KEYCODES:
        raise _blocked_key_error(f"{raw} (keycode {keycode})")
    return keycode


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def warn(*args) -> None:
    print(*args, file=sys.stderr)


def adb_binary_name() -> str:
    return "adb.exe" if os.name == "nt" else "adb"


def find_adb(explicit: str | None) -> str:
    """Locate adb without relying on the caller's PATH or shell."""
    candidates: list[str] = []

    if explicit:
        expanded = os.path.expanduser(explicit)
        if os.path.isfile(expanded):
            return expanded
        found = shutil.which(explicit)
        if found:
            return found

    env_adb = os.environ.get("ADB")
    if env_adb:
        expanded = os.path.expanduser(env_adb)
        if os.path.isfile(expanded):
            return expanded
        found = shutil.which(env_adb)
        if found:
            return found

    found = shutil.which("adb")
    if found:
        return found

    for env_name in ("ANDROID_SDK_ROOT", "ANDROID_HOME"):
        root = os.environ.get(env_name)
        if root:
            candidates.append(os.path.join(root, "platform-tools", adb_binary_name()))

    home = os.path.expanduser("~")
    local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")

    system = platform.system()
    if os.name == "nt":
        candidates.extend(
            [
                os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe"),
                os.path.join(local, "Android", "platform-tools", "adb.exe"),
                os.path.join(home, "AppData", "Local", "Android", "Sdk", "platform-tools", "adb.exe"),
                r"C:\Android\platform-tools\adb.exe",
                r"C:\platform-tools\adb.exe",
                r"C:\Program Files\Android\platform-tools\adb.exe",
            ]
        )
    elif system == "Darwin":
        candidates.extend(
            [
                os.path.join(home, "Library", "Android", "sdk", "platform-tools", "adb"),
                "/opt/homebrew/bin/adb",
                "/usr/local/bin/adb",
                "/opt/local/bin/adb",
            ]
        )
    else:
        candidates.extend(
            [
                os.path.join(home, "Android", "Sdk", "platform-tools", "adb"),
                "/usr/bin/adb",
                "/usr/local/bin/adb",
            ]
        )

    seen = set()
    for path in candidates:
        path = os.path.expanduser(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path

    raise AdbError(
        "未找到 adb。请安装 Android platform-tools，或用 --adb 指定完整路径。\n"
        "  Windows: 解压 platform-tools，常见位置为 "
        r"%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe" "\n"
        "  macOS:   brew install android-platform-tools\n"
        "  验证:    adb version"
    )


def run_cmd(cmd: list[str], timeout: int = 60, binary: bool = False):
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=not binary,
            encoding=None if binary else "utf-8",
            errors=None if binary else "replace",
        )
    except FileNotFoundError as exc:
        raise AdbError(f"找不到可执行文件: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AdbError(f"命令超时（{timeout}s）: {' '.join(cmd[:6])} ...") from exc


def adb_cmd(adb: str, serial: str | None, args: list[str], timeout: int = 60, binary: bool = False):
    cmd = [adb]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return run_cmd(cmd, timeout=timeout, binary=binary)


def output_text(proc) -> str:
    out = proc.stdout or ""
    err = proc.stderr or ""
    return (out + ("\n" + err if err.strip() else "")).strip()


def require_ok(proc, what: str) -> str:
    text = output_text(proc)
    if proc.returncode != 0:
        raise AdbError(f"{what}失败：\n{text[:3000]}")
    return text


def parse_devices(adb: str) -> list[dict]:
    proc = adb_cmd(adb, None, ["devices", "-l"], timeout=30)
    text = require_ok(proc, "adb devices")
    devices: list[dict] = []
    valid_states = {
        "device",
        "offline",
        "unauthorized",
        "no",
        "recovery",
        "sideload",
        "bootloader",
        "host",
        "unknown",
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state not in valid_states:
            # Avoid treating adb server/client warnings as devices.
            continue
        meta: dict[str, str] = {}
        for item in parts[2:]:
            if ":" in item:
                key, value = item.split(":", 1)
                meta[key] = value
        devices.append(
            {
                "serial": serial,
                "state": state,
                "product": meta.get("product", ""),
                "model": meta.get("model", ""),
                "device": meta.get("device", ""),
                "transport_id": meta.get("transport_id", ""),
            }
        )
    return devices


def format_devices(devices: list[dict]) -> str:
    if not devices:
        return "（没有检测到设备）"
    lines = [f"{'SERIAL':<46} {'STATE':<14} MODEL / PRODUCT / DEVICE"]
    for dev in devices:
        model = dev.get("model") or "?"
        product = dev.get("product") or "?"
        device = dev.get("device") or "?"
        lines.append(f"{dev['serial']:<46} {dev['state']:<14} {model} / {product} / {device}")
    return "\n".join(lines)


def duplicate_hint(devices: list[dict]) -> str:
    for i, first in enumerate(devices):
        for second in devices[i + 1 :]:
            if not first.get("model") or first.get("model") != second.get("model"):
                continue
            if first.get("device") and first.get("device") != second.get("device"):
                continue
            serials = (first["serial"], second["serial"])
            one_mdns = any("_adb-tls-connect._tcp" in s for s in serials)
            one_ip = any(":" in s and "_adb-tls-connect._tcp" not in s for s in serials)
            if one_mdns and one_ip:
                return (
                    "提示：上面可能有同一台手机的两条传输（IP:PORT 与 mDNS），"
                    "优先使用 IP:PORT 那条。"
                )
    return ""


def select_serial(adb: str, override: str | None) -> str:
    devices = parse_devices(adb)
    available = [dev for dev in devices if dev["state"] == "device"]

    chosen = override or os.environ.get("ANDROID_SERIAL")
    if chosen:
        match = next((dev for dev in devices if dev["serial"] == chosen), None)
        if not match:
            raise AdbError(
                f"指定的设备不在列表中：{chosen}\n\n{format_devices(devices)}\n\n"
                "每台手机换线/重连后 serial 可能变化，请重新运行 devices。"
            )
        if match["state"] != "device":
            raise AdbError(
                f"设备 {chosen} 当前状态是 {match['state']}，不是 device。\n"
                "请解锁手机并确认 USB 调试授权；无线设备请重新连接。"
            )
        return chosen

    if len(available) == 1:
        return available[0]["serial"]

    if not available:
        hint = ""
        if any(dev["state"] == "unauthorized" for dev in devices):
            hint = (
                "检测到未授权设备：请在手机上点“允许 USB 调试”（建议勾选“始终允许”），然后重试。"
            )
        elif any(dev["state"] == "offline" for dev in devices):
            hint = "设备处于 offline：重插 USB，或关掉再打开无线调试，必要时 adb kill-server 后重试。"
        else:
            hint = (
                "没有可用设备。请确认：手机已开启 USB 调试 / 无线调试；"
                "已授权本电脑；无线时手机与电脑在同一局域网。详见 references/setup.md。"
            )
        raise AdbError(f"{hint}\n\n{format_devices(devices)}")

    raise AdbError(
        "检测到多台设备，必须用 --serial 指定。\n\n"
        + format_devices(devices)
        + "\n\n"
        + (duplicate_hint(devices) + "\n" if duplicate_hint(devices) else "")
        + "示例：--serial "
        + available[0]["serial"]
    )


def get_size(adb: str, serial: str) -> tuple[int, int] | None:
    try:
        proc = adb_cmd(adb, serial, ["shell", "wm", "size"], timeout=20)
    except AdbError:
        return None
    matches = re.findall(r"(\d+)\s*x\s*(\d+)", proc.stdout or "")
    if not matches:
        return None
    width, height = matches[-1]
    return int(width), int(height)


def get_density(adb: str, serial: str) -> str:
    try:
        proc = adb_cmd(adb, serial, ["shell", "wm", "density"], timeout=20)
    except AdbError:
        return ""
    matches = re.findall(r"(\d+)", proc.stdout or "")
    return matches[-1] if matches else ""


def get_props(adb: str, serial: str) -> dict[str, str]:
    proc = adb_cmd(adb, serial, ["shell", "getprop"], timeout=30)
    text = require_ok(proc, "读取设备属性")
    props: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"\[([^\]]+)\]:\s*\[(.*)\]", line)
        if match:
            props[match.group(1)] = match.group(2)
    return props


def fetch_remote_file(adb: str, serial: str, remote: str) -> bytes:
    proc = adb_cmd(adb, serial, ["exec-out", "cat", remote], timeout=30, binary=True)
    data = proc.stdout or b""
    index = data.find(b"<?xml")
    if index < 0:
        index = data.find(b"<hierarchy")
    if index > 0:
        data = data[index:]
    if b"<hierarchy" in data:
        return data

    # Some older adb builds do not support exec-out; pull as a fallback.
    fd, local_path = tempfile.mkstemp(prefix="adb_ui_", suffix=".xml")
    os.close(fd)
    try:
        pull = adb_cmd(adb, serial, ["pull", remote, local_path], timeout=30)
        if pull.returncode == 0 and os.path.getsize(local_path) > 0:
            with open(local_path, "rb") as handle:
                data = handle.read()
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass

    index = data.find(b"<?xml")
    if index < 0:
        index = data.find(b"<hierarchy")
    if index > 0:
        data = data[index:]
    return data if b"<hierarchy" in data else b""


def dump_xml(adb: str, serial: str, settle: float = 0.6, retries: int = 2) -> str:
    remotes = [
        f"/data/local/tmp/adb_ui_dump_{os.getpid()}.xml",
        f"/sdcard/adb_ui_dump_{os.getpid()}.xml",
    ]
    errors: list[str] = []

    for attempt in range(1, retries + 1):
        if settle:
            time.sleep(settle)
        for remote in remotes:
            proc = adb_cmd(
                adb,
                serial,
                ["shell", "uiautomator", "dump", remote],
                timeout=30,
            )
            message = output_text(proc)
            try:
                data = fetch_remote_file(adb, serial, remote)
            finally:
                try:
                    adb_cmd(adb, serial, ["shell", "rm", "-f", remote], timeout=15)
                except AdbError:
                    pass
            if data:
                try:
                    text = data.decode("utf-8-sig", "replace")
                except Exception:
                    text = ""
                if "<hierarchy" in text:
                    return text
            errors.append(f"第 {attempt} 次 / {remote}: {message[-400:]}")
        time.sleep(0.6)

    raise AdbError(
        "uiautomator dump 失败。\n"
        "请先解锁屏幕、确认目标 App 在前台，等动画结束后重试。\n"
        "部分 App（游戏、Canvas、部分 WebView）不暴露无障碍树，此时只能征得用户同意后截图。\n\n"
        + "\n".join(errors[-3:])
    )


def parse_nodes(xml_text: str) -> list[dict]:
    if xml_text.startswith("\ufeff"):
        xml_text = xml_text[1:]
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise AdbError(f"界面 XML 解析失败：{exc}") from exc

    nodes: list[dict] = []
    for element in root.iter("node"):
        match = BOUNDS_RE.match(element.get("bounds") or "")
        if not match:
            continue
        x1, y1, x2, y2 = (int(value) for value in match.groups())
        text = (element.get("text") or "").strip()
        desc = (element.get("content-desc") or "").strip()
        resource_id = (element.get("resource-id") or "").strip()
        clickable = element.get("clickable") == "true"
        if not text and not desc and not resource_id and not clickable:
            continue

        nodes.append(
            {
                "text": text,
                "desc": desc,
                "label": text or desc,
                "box": [x1, y1, x2, y2],
                "bounds": f"[{x1},{y1}][{x2},{y2}]",
                "center": [(x1 + x2) // 2, (y1 + y2) // 2],
                "clickable": clickable,
                "long_clickable": element.get("long-clickable") == "true",
                "scrollable": element.get("scrollable") == "true",
                "checkable": element.get("checkable") == "true",
                "checked": element.get("checked") == "true",
                "enabled": element.get("enabled") != "false",
                "focused": element.get("focused") == "true",
                "selected": element.get("selected") == "true",
                "password": element.get("password") == "true",
                "class": element.get("class") or "",
                "resource_id": resource_id,
                "package": element.get("package") or "",
            }
        )

    nodes.sort(key=lambda node: (node["box"][1], node["box"][0]))

    seen: dict[tuple, int] = {}
    unique: list[dict] = []
    for node in nodes:
        key = (
            node["text"],
            node["desc"],
            tuple(node["box"]),
            node["resource_id"],
            node["class"],
        )
        if key in seen:
            # Prefer the actionable copy when a container and its child
            # expose identical text and bounds.
            existing_index = seen[key]
            if node["clickable"] and not unique[existing_index]["clickable"]:
                unique[existing_index] = node
            continue
        seen[key] = len(unique)
        unique.append(node)

    for index, node in enumerate(unique):
        node["index"] = index
    return unique


def screen_from_nodes(nodes: list[dict]) -> tuple[int, int]:
    if not nodes:
        return 0, 0
    width = max(max(0, node["box"][2]) for node in nodes)
    height = max(max(0, node["box"][3]) for node in nodes)
    return width, height


def choose_package(nodes: list[dict]) -> str:
    packages = [node["package"] for node in nodes if node["package"]]
    if not packages:
        return ""
    counted = Counter(packages).most_common()
    for package, _count in counted:
        if package != "com.android.systemui":
            return package
    return counted[0][0]


def node_flags(node: dict) -> str:
    flags = []
    if node["clickable"]:
        flags.append("clickable")
    if node["scrollable"]:
        flags.append("scrollable")
    if node["long_clickable"]:
        flags.append("long-clickable")
    if node["checkable"]:
        flags.append("checkable")
    if node["password"]:
        flags.append("password")
    if not node["enabled"]:
        flags.append("disabled")
    return " ".join(flags)


def display_label(node: dict, full: bool = False) -> str:
    label = node["text"] or node["desc"]
    if not label:
        label = f"<{node['resource_id'] or node['class']}>"
    if not full:
        if len(label) > 500:
            label = label[:500] + "…"
    return label


def filter_dump_nodes(nodes: list[dict], args) -> list[dict]:
    regex = None
    if args.regex:
        try:
            regex = re.compile(args.regex, re.IGNORECASE)
        except re.error as exc:
            raise AdbError(f"--regex 无效：{exc}") from exc

    rows = []
    for node in nodes:
        haystack = node["text"] + "\n" + node["desc"]
        if not args.all and not (node["text"] or node["desc"]):
            continue
        if args.contains and args.contains.casefold() not in haystack.casefold():
            continue
        if regex and not regex.search(haystack):
            continue
        if args.clickable and not node["clickable"]:
            continue
        rows.append(node)
    return rows


def cmd_devices(adb: str, serial: str | None, args) -> int:
    devices = parse_devices(adb)
    if args.json:
        print(json.dumps(devices, ensure_ascii=False, indent=2))
        return 0 if any(dev["state"] == "device" for dev in devices) else 1

    print(format_devices(devices))
    hint = duplicate_hint(devices)
    if hint:
        print("\n" + hint)
    if any(dev["state"] == "unauthorized" for dev in devices):
        print("\n有设备未授权：在手机上点“允许 USB 调试”。")
    if any(dev["state"] == "offline" for dev in devices):
        print("\n有设备 offline：重插 USB 或重开无线调试。")
    if not devices:
        print(
            "\n未发现设备。检查 USB 调试/无线调试是否开启、电脑是否已获授权。"
            " 详细步骤见 references/setup.md。"
        )
    return 0 if any(dev["state"] == "device" for dev in devices) else 1


def cmd_info(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    props = get_props(adb, chosen)

    version_proc = adb_cmd(adb, None, ["version"], timeout=15)
    version_line = ""
    for line in (version_proc.stdout or "").splitlines():
        if line.strip().startswith("Version"):
            version_line = line.strip()
            break

    size = get_size(adb, chosen)
    density = get_density(adb, chosen)

    print(f"serial:  {chosen}")
    if version_line:
        print(f"adb:     {version_line}")
    print(f"brand:   {props.get('ro.product.brand', '')}")
    print(f"model:   {props.get('ro.product.model', '')}")
    print(f"product: {props.get('ro.product.name', '')}")
    print(f"device:  {props.get('ro.product.device', '')}")
    print(
        "android: "
        f"{props.get('ro.build.version.release', '?')} "
        f"(SDK {props.get('ro.build.version.sdk', '?')})"
    )
    if size:
        print(f"screen:  {size[0]}x{size[1]}")
    if density:
        print(f"density: {density}")
    return 0


def cmd_dump(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    xml_text = dump_xml(adb, chosen, settle=args.settle, retries=2)

    if args.out:
        out_path = os.path.abspath(os.path.expanduser(args.out))
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(xml_text)
        warn(f"原始 XML 已保存：{out_path}")

    if args.raw:
        sys.stdout.write(xml_text)
        if not xml_text.endswith("\n"):
            sys.stdout.write("\n")
        return 0

    nodes = parse_nodes(xml_text)
    rows = filter_dump_nodes(nodes, args)
    limit = args.limit if args.limit and args.limit > 0 else len(rows)
    shown = rows[:limit]

    size = get_size(adb, chosen)
    if not size:
        size = screen_from_nodes(nodes)
    package = choose_package(nodes)

    if args.json:
        payload = {
            "serial": chosen,
            "package": package,
            "screen": {"width": size[0], "height": size[1]},
            "total": len(nodes),
            "matched": len(rows),
            "shown": len(shown),
            "nodes": shown,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(
        f"# serial={chosen} package={package or '?'} screen={size[0]}x{size[1]} "
        f"total={len(nodes)} matched={len(rows)} shown={len(shown)}"
    )
    print("# box=(x1,y1)-(x2,y2) center=(cx,cy); tap center to activate")
    for node in shown:
        flags = node_flags(node)
        flag_text = f" {flags}" if flags else ""
        print(
            f"[{node['index']}] box=({node['box'][0]},{node['box'][1]})-"
            f"({node['box'][2]},{node['box'][3]}) "
            f"center=({node['center'][0]},{node['center'][1]}){flag_text}"
        )
        label = display_label(node, full=args.full)
        if args.full:
            label = label.replace("\n", "\n    ")
        else:
            label = label.replace("\n", " ⏎ ")
        print(f"    {label}")
    return 0


def compile_finder(args):
    regex = None
    if args.regex:
        try:
            regex = re.compile(args.pattern, re.IGNORECASE)
        except re.error as exc:
            raise AdbError(f"--regex 无效：{exc}") from exc
    return regex


def match_text(value: str, args, regex) -> bool:
    if regex:
        return bool(regex.search(value))
    if args.exact:
        return value.strip().casefold() == args.pattern.strip().casefold()
    return args.pattern.casefold() in value.casefold()


def cmd_find(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    xml_text = dump_xml(adb, chosen, settle=args.settle, retries=2)
    nodes = parse_nodes(xml_text)
    regex = compile_finder(args)

    matches = [
        node
        for node in nodes
        if (match_text(node["text"], args, regex) or match_text(node["desc"], args, regex))
        and (not args.clickable or node["clickable"])
    ]

    size = get_size(adb, chosen)
    if not size:
        size = screen_from_nodes(nodes)

    tapped = None
    if args.tap and matches:
        if len(matches) > 1 and args.index is None:
            if args.json:
                print(
                    json.dumps(
                        {
                            "serial": chosen,
                            "query": args.pattern,
                            "matches": matches,
                            "error": "匹配到多个控件，请用 --index 指定",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            warn(
                f"匹配到 {len(matches)} 个控件，为避免点错，不自动点按。"
                " 请查看列表后用 --index N 指定。"
            )
            return 2
        index = 0 if args.index is None else args.index
        if index < 0 or index >= len(matches):
            raise AdbError(f"--index {index} 超出范围（共 {len(matches)} 个匹配）")
        target = matches[index]
        cx, cy = target["center"]
        proc = adb_cmd(adb, chosen, ["shell", "input", "tap", str(cx), str(cy)], timeout=20)
        require_ok(proc, "点按")
        tapped = {
            "match_index": index,
            "center": [cx, cy],
            "label": target["text"] or target["desc"],
        }
        if args.wait:
            time.sleep(args.wait)

    if args.json:
        print(
            json.dumps(
                {
                    "serial": chosen,
                    "package": choose_package(nodes),
                    "screen": {"width": size[0], "height": size[1]},
                    "query": args.pattern,
                    "matches": matches,
                    "tapped": tapped,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not matches:
            warn(f"没有找到匹配：{args.pattern!r}")
        else:
            print(
                f"# serial={chosen} screen={size[0]}x{size[1]} "
                f"query={args.pattern!r} matches={len(matches)}"
            )
            for index, node in enumerate(matches):
                flags = node_flags(node)
                flag_text = f" {flags}" if flags else ""
                print(
                    f"[{index}] center=({node['center'][0]},{node['center'][1]}) "
                    f"box=({node['box'][0]},{node['box'][1]})-"
                    f"({node['box'][2]},{node['box'][3]}){flag_text}"
                )
                print("    " + display_label(node).replace("\n", " ⏎ "))
        if tapped:
            print(
                f"已点按 [{tapped['match_index']}] "
                f"center=({tapped['center'][0]},{tapped['center'][1]}) "
                f"{tapped['label'][:80]}"
            )

    return 0 if matches else 1


def cmd_tap(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    proc = adb_cmd(
        adb,
        chosen,
        ["shell", "input", "tap", str(args.x), str(args.y)],
        timeout=20,
    )
    require_ok(proc, "点按")
    if args.wait:
        time.sleep(args.wait)
    print(f"ok: tap ({args.x},{args.y})")
    return 0


def cmd_swipe(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    duration = max(50, args.ms)
    proc = adb_cmd(
        adb,
        chosen,
        [
            "shell",
            "input",
            "swipe",
            str(args.x1),
            str(args.y1),
            str(args.x2),
            str(args.y2),
            str(duration),
        ],
        timeout=30,
    )
    require_ok(proc, "滑动")
    if args.wait:
        time.sleep(args.wait)
    print(f"ok: swipe ({args.x1},{args.y1}) -> ({args.x2},{args.y2}) {duration}ms")
    return 0


def cmd_key(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    raw = str(args.key).strip()
    keycode = resolve_keycode(raw)
    proc = adb_cmd(adb, chosen, ["shell", "input", "keyevent", str(keycode)], timeout=20)
    require_ok(proc, "按键")
    if args.wait:
        time.sleep(args.wait)
    print(f"ok: keyevent {keycode} ({raw})")
    return 0


def parse_screen_state(power_text: str = "", policy_text: str = "") -> dict:
    """Parse screen/keyguard state without touching the device."""
    power_text = power_text or ""
    policy_text = policy_text or ""

    screen = "unknown"
    if re.search(r"Display Power:\s*state=OFF\b", power_text, re.I) or re.search(
        r"mWakefulness=Asleep\b", power_text, re.I
    ):
        screen = "off"
    elif re.search(r"Display Power:\s*state=ON\b", power_text, re.I) or re.search(
        r"mWakefulness=Awake\b", power_text, re.I
    ):
        screen = "on"

    keyguard = "unknown"
    flags = []
    for pattern in (
        r"mShowingLockscreen=(true|false)",
        r"mKeyguardShowing=(true|false)",
        r"isStatusBarKeyguard=(true|false)",
    ):
        flags.extend(re.findall(pattern, policy_text, re.I))
    if flags:
        keyguard = "showing" if any(value.lower() == "true" for value in flags) else "not showing"

    return {"screen": screen, "keyguard": keyguard}


def cmd_screen(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    power_text = ""
    policy_text = ""
    try:
        power_text = output_text(
            adb_cmd(adb, chosen, ["shell", "dumpsys", "power"], timeout=30)
        )
    except AdbError:
        pass
    try:
        policy_text = output_text(
            adb_cmd(adb, chosen, ["shell", "dumpsys", "window", "policy"], timeout=30)
        )
    except AdbError:
        pass

    state = parse_screen_state(power_text, policy_text)
    ready = state["screen"] == "on" and state["keyguard"] == "not showing"
    if args.json:
        print(
            json.dumps(
                {"serial": chosen, **state, "ready": ready},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"screen:   {state['screen']}")
    print(f"keyguard: {state['keyguard']}")
    print(f"ready:    {'yes' if ready else 'no'}")
    if not ready:
        print("请让用户手动点亮并解锁屏幕；本技能不会自动唤醒或解锁。")
    return 0


def cmd_open(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    url = args.url
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", url):
        raise AdbError("URL 必须以 scheme:// 开头，例如 xhsdiscover://... 或 https://...")

    command = [ "shell", "am", "start", "-W", "-f", "0x10000000" ]
    if args.package:
        command.extend(["-p", args.package])
    command.extend(["-a", "android.intent.action.VIEW", "-d", url])

    proc = adb_cmd(adb, chosen, command, timeout=45)
    text = output_text(proc)
    print(text)
    if "Error:" in text or "Exception" in text:
        raise AdbError("打开链接失败，见上方输出。")
    if args.wait:
        time.sleep(args.wait)
    return 0


def validate_package(package: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.]+", package or ""):
        raise AdbError(f"包名格式不对：{package!r}")
    return package


def start_app(adb: str, serial: str, package: str) -> int:
    package = validate_package(package)
    resolve = adb_cmd(
        adb,
        serial,
        ["shell", "cmd", "package", "resolve-activity", "--brief", package],
        timeout=30,
    )
    component = ""
    for line in (resolve.stdout or "").splitlines():
        line = line.strip()
        if "/" in line and not line.startswith("package:"):
            component = line
    if component:
        proc = adb_cmd(adb, serial, ["shell", "am", "start", "-W", "-n", component], timeout=45)
        print(output_text(proc))
        return 0

    # Fallback for older Android versions that lack `cmd package resolve-activity`.
    proc = adb_cmd(
        adb,
        serial,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
        timeout=45,
    )
    text = output_text(proc)
    print(text)
    if "Events injected: 1" not in text:
        raise AdbError("无法启动 App，请确认包名，或让用户手动打开。")
    return 0


def cmd_app(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    package = validate_package(args.package)
    if args.app_command == "start":
        return start_app(adb, chosen, package)

    proc = adb_cmd(adb, chosen, ["shell", "am", "force-stop", package], timeout=30)
    require_ok(proc, "强制停止 App")
    print(f"ok: force-stop {package}")
    return 0


def cmd_current(adb: str, serial: str | None, args) -> int:
    chosen = select_serial(adb, serial)
    keys = ("mCurrentFocus", "mFocusedApp", "mResumedActivity", "topResumedActivity")

    def collect(command: list[str]) -> list[str]:
        proc = adb_cmd(adb, chosen, command, timeout=30)
        found = []
        for line in (proc.stdout or "").splitlines():
            stripped = line.strip()
            if any(key in stripped for key in keys):
                found.append(stripped)
        return found

    lines = collect(["shell", "dumpsys", "window"])
    if not lines:
        lines = collect(["shell", "dumpsys", "activity", "activities"])

    seen = set()
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        print(line)
    if not lines:
        print("未读取到当前焦点信息（部分系统限制了 dumpsys 输出）。")
    return 0


def cmd_connect(adb: str, serial: str | None, args) -> int:
    proc = adb_cmd(adb, None, ["connect", args.target], timeout=30)
    text = output_text(proc)
    print(text)
    if proc.returncode != 0 or "failed" in text.lower() or "cannot" in text.lower():
        raise AdbError("连接失败，检查 IP/端口、同一 Wi-Fi、无线调试是否开启。")
    print("下一步：python adb_ui.py devices")
    return 0


def cmd_pair(adb: str, serial: str | None, args) -> int:
    proc = adb_cmd(adb, None, ["pair", args.target, args.code], timeout=60)
    text = output_text(proc)
    print(text)
    if proc.returncode != 0 or "failed" in text.lower() or "cannot" in text.lower():
        raise AdbError("配对失败：配对码过期或端口不对，请在手机上重新生成配对码。")
    print(
        "配对成功。配对端口不是连接端口：回无线调试页，用显示的 IP:连接端口 执行 connect。"
    )
    return 0


def cmd_mdns(adb: str, serial: str | None, args) -> int:
    proc = adb_cmd(adb, None, ["mdns", "services"], timeout=30)
    text = output_text(proc)
    services = [
        line.strip()
        for line in text.splitlines()
        if "_adb" in line or "_tcp" in line
    ]
    if services:
        print("\n".join(services))
    elif text.strip() and "List of discovered mdns services" not in text:
        print(text)
    else:
        print("没有发现 mDNS 服务。可直接用手机上显示的 IP:端口 执行 connect。")
    if "_adb-tls-connect._tcp" in text:
        print("\n找到无线调试连接服务；用对应的 IP:端口 执行 connect。")
    return 0 if proc.returncode == 0 else 1


def cmd_quote(adb: str | None, serial: str | None, args) -> int:
    print(urllib.parse.quote(args.text, safe=""))
    return 0


def extract_globals(argv: list[str]) -> tuple[list[str], str | None, str | None]:
    adb_path = None
    serial = None
    cleaned: list[str] = []
    index = 0

    while index < len(argv):
        arg = argv[index]
        if arg in ("--serial", "-s"):
            if index + 1 >= len(argv):
                raise AdbError(f"{arg} 缺少参数值")
            serial = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--serial="):
            serial = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--adb":
            if index + 1 >= len(argv):
                raise AdbError("--adb 缺少参数值")
            adb_path = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--adb="):
            adb_path = arg.split("=", 1)[1]
            index += 1
            continue
        cleaned.append(arg)
        index += 1

    return cleaned, adb_path, serial


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adb_ui.py",
        description=(
            "ADB + uiautomator dump 界面读取与只读导航（Windows/macOS）。"
            "全局参数 --serial SERIAL、--adb PATH 可放在任意位置。"
        ),
        epilog=(
            "常用示例：\n"
            "  python adb_ui.py devices\n"
            "  python adb_ui.py dump --serial <SERIAL> --contains 评论\n"
            "  python adb_ui.py find 头像 --serial <SERIAL> --tap\n"
            "  python adb_ui.py open 'xhsdiscover://search/result?keyword=%E6%B7%B1%E5%9C%B3' "
            "--serial <SERIAL>\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    devices = subparsers.add_parser("devices", help="列出 ADB 设备")
    devices.add_argument("--json", action="store_true", help="以 JSON 输出")
    devices.set_defaults(func=cmd_devices)

    info = subparsers.add_parser("info", help="显示品牌、型号、系统版本、分辨率")
    info.set_defaults(func=cmd_info)

    screen = subparsers.add_parser("screen", help="只读检查屏幕/锁屏状态（不唤醒/解锁）")
    screen.add_argument("--json", action="store_true", help="JSON 输出")
    screen.set_defaults(func=cmd_screen)

    dump = subparsers.add_parser("dump", help="读取当前界面的文字/控件/坐标")
    dump.add_argument("--json", action="store_true", help="JSON 输出")
    dump.add_argument("--contains", help="只显示包含该子串的节点（不区分大小写）")
    dump.add_argument("--regex", help="只显示匹配该正则的节点（不区分大小写）")
    dump.add_argument("--clickable", action="store_true", help="只显示可点击节点")
    dump.add_argument("--all", action="store_true", help="包含没有 text/content-desc 的节点")
    dump.add_argument("--limit", type=int, default=200, help="最多显示多少个节点，0 表示不限（默认 200）")
    dump.add_argument("--full", action="store_true", help="不截断长文本")
    dump.add_argument("--settle", type=float, default=0.6, help="dump 前等待秒数（默认 0.6）")
    dump.add_argument("--out", help="把原始 XML 保存到指定文件")
    dump.add_argument("--raw", action="store_true", help="直接输出原始 XML")
    dump.set_defaults(func=cmd_dump)

    find = subparsers.add_parser("find", help="按文字/content-desc 查找控件")
    find.add_argument("pattern", help="要查找的文字")
    find.add_argument("--regex", action="store_true", help="把 pattern 当作正则")
    find.add_argument("--exact", action="store_true", help="完全匹配（不区分大小写）")
    find.add_argument("--clickable", action="store_true", help="只要可点击节点")
    find.add_argument("--tap", action="store_true", help="找到后点按（多个匹配需 --index）")
    find.add_argument("--index", type=int, default=None, help="点按第几个匹配（从 0 开始）")
    find.add_argument("--json", action="store_true", help="JSON 输出")
    find.add_argument("--settle", type=float, default=0.6, help="dump 前等待秒数")
    find.add_argument("--wait", type=float, default=1.0, help="点按后等待秒数（默认 1.0）")
    find.set_defaults(func=cmd_find)

    tap = subparsers.add_parser("tap", help="点按屏幕坐标")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.add_argument("--wait", type=float, default=0.0, help="点按后等待秒数")
    tap.set_defaults(func=cmd_tap)

    swipe = subparsers.add_parser("swipe", help="滑动")
    swipe.add_argument("x1", type=int)
    swipe.add_argument("y1", type=int)
    swipe.add_argument("x2", type=int)
    swipe.add_argument("y2", type=int)
    swipe.add_argument("--ms", type=int, default=400, help="滑动时长毫秒（默认 400）")
    swipe.add_argument("--wait", type=float, default=0.0, help="滑动后等待秒数")
    swipe.set_defaults(func=cmd_swipe)

    key = subparsers.add_parser(
        "key", help="发送导航按键（back/home/enter 等；不含电源/唤醒键）"
    )
    key.add_argument("key")
    key.add_argument("--wait", type=float, default=0.0)
    key.set_defaults(func=cmd_key)

    open_parser = subparsers.add_parser("open", help="用 deep link 打开页面")
    open_parser.add_argument("url")
    open_parser.add_argument("--package", help="限定包名")
    open_parser.add_argument("--wait", type=float, default=1.0, help="启动后等待秒数")
    open_parser.set_defaults(func=cmd_open)

    app = subparsers.add_parser("app", help="启动/停止 App")
    app_sub = app.add_subparsers(dest="app_command", required=True)
    app_start = app_sub.add_parser("start", help="启动 App")
    app_start.add_argument("package")
    app_stop = app_sub.add_parser("stop", help="强制停止 App（风控停手用）")
    app_stop.add_argument("package")
    app.set_defaults(func=cmd_app)

    current = subparsers.add_parser("current", help="查看当前前台窗口")
    current.set_defaults(func=cmd_current)

    connect = subparsers.add_parser("connect", help="连接无线调试")
    connect.add_argument("target", help="IP:PORT")
    connect.set_defaults(func=cmd_connect)

    pair = subparsers.add_parser("pair", help="无线调试配对")
    pair.add_argument("target", help="IP:配对端口")
    pair.add_argument("code", help="6 位配对码")
    pair.set_defaults(func=cmd_pair)

    mdns = subparsers.add_parser("mdns", help="列出无线调试 mDNS 服务")
    mdns.set_defaults(func=cmd_mdns)

    quote = subparsers.add_parser("quote", help="URL 编码文字（用于搜索深链）")
    quote.add_argument("text")
    quote.set_defaults(func=cmd_quote)

    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    raw_argv = list(sys.argv[1:] if argv is None else argv)

    try:
        cleaned, adb_path, serial = extract_globals(raw_argv)
    except AdbError as exc:
        warn("错误：", str(exc))
        return 2

    parser = build_parser()
    args = parser.parse_args(cleaned)

    try:
        if args.command == "quote":
            return cmd_quote(None, serial, args)
        adb = find_adb(adb_path)
        return args.func(adb, serial, args)
    except AdbError as exc:
        warn("错误：", str(exc))
        return 2
    except KeyboardInterrupt:
        warn("\n已中断。")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
