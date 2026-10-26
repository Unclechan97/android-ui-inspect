# Android UI Inspect

用 **ADB + `uiautomator dump`** 读取 Android 屏幕上的文字、`content-desc`、控件属性和坐标，再用文字定位或坐标点按完成只读导航。默认不截图，不依赖 OCR，适合人工式、低频率的浏览和公开信息采集，不适合批量爬取。

这是一个 [Claude Code](https://claude.ai/code) skill，也可以作为独立的 Python 命令行工具使用。Windows 和 macOS 均可用。

> **安全边界：只操作本人拥有且已授权的设备；默认只读；不自动唤醒、不自动解锁；不代输手机号/验证码/密码；遇到登录或风控立即停手。**

---

## 能做什么

- 列出设备、型号、Android 版本、分辨率和无线调试服务；
- dump 当前界面的无障碍树，按文字/正则/可点击性过滤；
- 按文字找到控件中心点并点按，避免硬编码坐标；
- 发送返回、Home 等导航按键，滑动，打开 deep link；
- 只读检查屏幕和锁屏状态；
- 在明确授权下，用上述能力做小规模、低频的 App 页面采集。

它**不**提供唤醒/解锁、输入密码、绕过登录、处理验证码、批量刷取或自动私信的能力。

---

## 安装

### 作为 Claude Code skill

把仓库放到 Claude Code 的 skills 目录，或建立链接：

```bash
# macOS / Linux
git clone https://github.com/Unclechan97/android-ui-inspect.git "$HOME/android-ui-inspect"
mkdir -p "$HOME/.claude/skills"
ln -s "$HOME/android-ui-inspect" "$HOME/.claude/skills/android-ui-inspect"
```

Windows PowerShell（Git Bash 的 `ln -s` 可能静默退化成复制，建议用 junction）：

```powershell
git clone https://github.com/Unclechan97/android-ui-inspect.git "$HOME\android-ui-inspect"
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.claude\skills" | Out-Null
New-Item -ItemType Junction `
  -Path "$env:USERPROFILE\.claude\skills\android-ui-inspect" `
  -Target "$HOME\android-ui-inspect"
```

Claude Code 会自动发现 `~/.claude/skills/android-ui-inspect/SKILL.md`。也可以直接复制整个目录。

### 作为独立 CLI

只需要 Python 3.8+ 和 Android platform-tools，不需要安装第三方 Python 包：

```bash
python scripts/adb_ui.py --version
python scripts/adb_ui.py devices
```

脚本会自动寻找 `adb`，也可以用 `--adb PATH` 指定。

---

## 快速开始

手机先由**用户手动点亮并解锁**，再执行：

```bash
# 1) 看设备是否已授权
python scripts/adb_ui.py devices

# 2) 看屏幕/锁屏状态（只读，不会唤醒）
python scripts/adb_ui.py screen --serial <SERIAL>

# 3) 读取当前界面文字和坐标
python scripts/adb_ui.py dump --serial <SERIAL> --contains 评论

# 4) 按文字点按；多个匹配时必须用 --index 明确指定
python scripts/adb_ui.py find "评论" --serial <SERIAL> --tap --index 0

# 5) 打开 App 的 deep link（示例）
KEYWORD=$(python scripts/adb_ui.py quote "关键词")
python scripts/adb_ui.py open "xhsdiscover://search/result?keyword=$KEYWORD" --serial <SERIAL>
```

Windows 用 `python`，不要用可能指向应用商店存根的 `python3`。Git Bash 下直接调用 `adb shell` 时可设 `MSYS_NO_PATHCONV=1`；本脚本通过参数列表调用 adb，不受该路径转换影响。

---

## 命令

| 命令 | 作用 |
|---|---|
| `devices [--json]` | 列出设备及状态 |
| `info` | 品牌、型号、Android 版本、分辨率、密度 |
| `screen [--json]` | **只读**检查屏幕亮灭和锁屏状态 |
| `connect HOST:PORT` / `pair HOST:PORT CODE` / `mdns` | 无线连接、配对、发现服务 |
| `dump [--json] [--contains X] [--regex R] [--clickable] [--all] [--limit N] [--out FILE] [--raw]` | 解析当前界面 |
| `find TEXT [--regex] [--exact] [--clickable] [--tap] [--index N] [--json]` | 按文字找控件，可点按 |
| `tap X Y` / `swipe X1 Y1 X2 Y2 [--ms N]` | 手势 |
| `key back\|home\|enter\|<数字>` | 导航按键（电源/唤醒键会被拒绝） |
| `open URL [--package PKG]` | 用 deep link 打开页面 |
| `app start PKG` / `app stop PKG` | 启动/强制停止 App |
| `current` | 查看当前前台窗口 |
| `quote TEXT` | URL 编码（用于搜索深链） |

全局参数 `--serial <SERIAL>`、`--adb <PATH>` 可以放在命令任意位置。完整帮助见 `python scripts/adb_ui.py --help`。

---

## 设计说明

- **文字优先，坐标只作兜底。** 每次页面变化后重新 dump；旧坐标会失效。
- **去重时保留可点击副本。** 容器和子控件常常有相同的文字和 bounds，脚本会优先保留可点击的那个。
- **混合树需要自己收窄范围。** 从浮层/头像进入新页面时，dump 可能同时包含底层页面；按 `package`、标题或子树过滤后再操作。
- **默认不截图。** WebView、Canvas、游戏等可能不暴露无障碍树；此时应说明限制，不要在没读到内容时编造。
- **锁屏只报告，不处理。** `screen` 只读取状态；需要用户手动点亮和解锁。
- **不默认第一台设备。** 多设备时必须用 `--serial`，避免点错手机。
- 无第三方依赖，所有 adb 调用都使用参数列表，不经过 shell。

---

## 小红书配方

[`references/xiaohongshu.md`](references/xiaohongshu.md) 记录了一套可选的、只读的小红书页面导航经验：搜索结果、笔记→主页、评论区的定位方式，以及深链换批、混合树、评论面板返回等坑。

它只在用户明确要求在**本人账号、本人设备**上做低频、公开信息浏览时使用。不要用它批量抓取、绕过登录、处理验证或风控，也不要把采集结果公开扩散。

---

## 测试

测试完全离线，不需要手机或 adb：

```bash
python -m unittest discover -s tests -v
```

覆盖：XML 节点解析/去重/排序、设备列表解析、重复传输提示、按键安全限制、屏幕状态解析、CLI 全局参数和 URL 编码。测试不依赖设备或 adb，可直接接入任意 CI。

---

## 已知限制

1. **不是所有界面都有无障碍树。** WebView、游戏、Canvas、部分自绘控件可能只暴露很少节点。
2. **动画期间 dump 可能不完整。** 等 1–2 秒再读；必要时返回后重进。
3. **坐标与分辨率/页面强相关。** 不要跨设备复用坐标。
4. **OEM 限制。** 小米/红米可能需要开启“USB 调试（安全设置）”；部分厂商会限制 `input` 或 `dumpsys`。
5. **iOS 不支持 ADB；鸿蒙 NEXT 需要 HDC。** 先确认设备类型，不要硬试。
6. **不适合批量爬取。** 这是人工式低频工具；请遵守平台条款、当地法律和账号风控边界。
7. **无线调试端口会变化。** 配对端口 ≠ 连接端口；重连后重新看手机上的端口。

---

## 隐私

- `dump --out` 保存的 XML、截图和任何页面快照都可能包含昵称、ID、评论、地理位置等个人信息。
- 不要把真实页面快照、账号信息、配对码或验证码提交到仓库；`.gitignore` 已默认忽略常见 dump/截图格式。
- 只采集用户明确要求、屏幕上公开可见的字段；不需要的原始数据应及时删除。
- 采集结果只用于用户说明的目的，不转售、不公开扩散。

---

## License

[MIT](LICENSE)
