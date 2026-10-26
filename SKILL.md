---
name: android-ui-inspect
description: 连接安卓手机并用 ADB + uiautomator dump 读取界面文字、控件与坐标，完成 App 信息采集（搜索页、主页、评论区、列表等）；不依赖截图，不自动唤醒或解锁，适配 Windows 和 macOS。用户说“连接我的手机/安卓手机”“用 ADB 看屏幕、抓取 App 内容”“不截图获取信息”“自动点按安卓界面”“从小红书评论区找账号/看主页”，或需要用小米、红米、华为、OPPO、vivo、三星等手机做只读采集时使用。
---

# Android UI Inspect

用 `uiautomator dump` 获取**无障碍树**，把屏幕上的文字、content-desc、bounds 解析成结构化数据，再用坐标点按导航。默认不截图；适合人工式、低频率的浏览采集，不适合批量爬取。

## 不可越过的边界

- 只操作用户**本人拥有且已授权**的设备，只采集用户指定范围内的、屏幕上可见的信息。
- 默认**只读**：dump、查找、翻页、点头像/标题等导航操作。不要点赞、收藏、关注、发私信、改设置。
- 绝不代输手机号、验证码、密码、支付信息；遇到登录/绑定/安全验证立即停手，把手机交还用户。
- **绝不自动唤醒或解锁**：不发送电源/唤醒键，不做上滑解锁，不调用 `dismiss-keyguard`，不输入 PIN/图案/密码。运行前由用户手动点亮并解锁；检测到锁屏就停手等待。
- 同一会话保持低频率：每次点按/翻页后等 1–3 秒；不做批量滚动或循环抓取；被限流就停机冷却 10–30 分钟；同一时间只开一个采集会话。
- iOS 不支持 ADB；鸿蒙 NEXT 用 HDC 而不是 ADB。先确认，再决定是否继续。
- 平台条款和当地法律优先。采集结果只用于用户明确说明的目的，不转售、不公开扩散。

## 第 0 步：确认设备（不要默认小米）

脚本位于本技能目录的 `scripts/adb_ui.py`。下面示例假设已 `cd` 到 `scripts/`；否则请用绝对路径，例如：

```bash
# macOS / Git Bash
python3 "$HOME/.claude/skills/android-ui-inspect/scripts/adb_ui.py" devices
```

```powershell
# Windows PowerShell
python "$env:USERPROFILE\.claude\skills\android-ui-inspect\scripts\adb_ui.py" devices
```

先运行 `devices`，脚本会自动找到 adb，并列出型号/传输。**只有没连上、需要指导开启调试时，才问用户品牌和系统版本**；品牌本身不影响 ADB 用法。

```bash
# Windows 用 python；macOS 用 python3（下同）
python adb_ui.py devices
```

需要更完整的设备信息时：

```bash
python adb_ui.py info --serial <SERIAL>
```

需要确认屏幕是否亮着、是否锁屏时，用只读的 `screen`（它**不会**唤醒或解锁）：

```bash
python adb_ui.py screen --serial <SERIAL>
```

如果用户没有明确说 Android：先问一句是安卓还是 iPhone。若是鸿蒙 NEXT，说明 ADB 不可用，不要硬试。

## 第 1 步：连接

### USB（最稳）

1. 手机开启开发者选项 → USB 调试。
2. 插线，手机上点“允许 USB 调试”（建议勾选“始终允许”）。
3. `devices` 应显示 `device`，不是 `unauthorized` 或 `offline`。

### 无线（Android 11+，推荐）

1. 手机与电脑同一 Wi-Fi；开发者选项 → 无线调试。
2. 点“使用配对码配对设备”，得到 `IP:配对端口` 和 6 位配对码。
3. `python adb_ui.py pair <IP>:<配对端口> <配对码>`
4. 回到无线调试页，用那里显示的 `IP:连接端口` 连接：
   `python adb_ui.py connect <IP>:<连接端口>`
5. 配对端口 ≠ 连接端口；端口可能每次开关无线调试后变化。连不上时用 `python adb_ui.py mdns` 查看 `_adb-tls-connect._tcp` 服务。

### 无线（Android 10 及以下）

先用 USB 连好并授权，再：

```bash
adb -s <SERIAL> tcpip 5555
python adb_ui.py connect <手机IP>:5555
```

### 多设备 / 重复传输

同一台手机可能同时出现 `IP:PORT` 和 `..._adb-tls-connect._tcp` 两条，看起来像两台设备。优先用 `IP:PORT` 那条，并在后续命令固定传 `--serial`（或设 `ANDROID_SERIAL`）。不要默认取第一台。

详细连接、各品牌开关和排错见 [references/setup.md](references/setup.md)。

## 第 2 步：进入目标页面

优先让用户把 App 打开到目标页；需要跳转时用 App 的 deep link。XHS 示例：

```bash
KEYWORD=$(python adb_ui.py quote "深圳找对象")
python adb_ui.py open "xhsdiscover://search/result?keyword=$KEYWORD" --serial <SERIAL>
```

PowerShell 对应写法：`$KEYWORD = python adb_ui.py quote "深圳找对象"`，再把它拼进 URL。

跳转后先 `dump`，**不要用上一页的坐标直接点**。深链可能重置任务栈，按返回键不一定回到上一页。重新发深链还可能换一批搜索结果，旧卡片/旧坐标不要再用；需要刷新就当作新批次重新 dump。

## 第 3 步：读屏（核心）

```bash
python adb_ui.py dump --serial <SERIAL>
python adb_ui.py dump --serial <SERIAL> --contains 评论
python adb_ui.py dump --serial <SERIAL> --json --limit 80
python adb_ui.py dump --serial <SERIAL> --out evidence.xml
```

输出中每个节点包含：

- `box=(x1,y1)-(x2,y2)`：控件外框；
- `center=(cx,cy)`：可直接用于 `tap` 的中心点；
- `clickable/scrollable/...`：控件属性；
- 文本：优先 `text`，没有则 `content-desc`。

按文字定位并点按，比记坐标稳得多：

```bash
python adb_ui.py find "评论" --serial <SERIAL>
python adb_ui.py find "头像" --serial <SERIAL> --tap
python adb_ui.py find "^评论 168$" --serial <SERIAL> --regex --tap --index 0
```

**坐标规则**

- 坐标只对当前分辨率、当前页面有效；每次导航后重新 dump。
- `find --tap` 匹配到多个时会要求用 `--index` 指定，避免点错；只有一个时才直接点。
- 不要点底部右侧的点赞/收藏/关注，除非用户明确要求。误触后不要乱点“撤销”，先 dump 确认状态，再问用户。
- 页面在动画中时 dump 可能不完整：等 1–2 秒再 dump；还不行就 `key back` 后重进。
- 如果 dump 只有极少节点：先运行 `screen` 看是否息屏/锁屏；如果是，请用户手动点亮并解锁，不要尝试自动解锁。再确认 App 在前台；WebView/游戏/canvas 可能本来就不暴露文字，此时不要假装读到了内容，向用户说明并征求是否允许截图。

## 第 4 步：采集与核验

1. 先写清楚要采哪些字段，只取这些字段。
2. 用平台内的**稳定 ID**（如小红书号）做主键，昵称会重名。
3. 记录每条数据的来源：主页、笔记正文、评论区，还是搜索结果。
4. 相对时间（“3 天前”“昨天”）要结合抓取时间换算；不确定就标“约”。
5. 交叉核验：从笔记头像进主页 → 核对 ID/昵称/笔记是否对应 → 再回笔记看评论。不要只凭搜索结果标题判断。
6. 只记录屏幕上真实出现的文字；不推断隐藏字段、手机号、住址等隐私信息。

## 第 5 步：风控与停手

出现下列任一情况，**立即停止当前账号的操作**：

- 登录、绑定手机号、短信验证码、人脸识别；
- “安全验证”“操作频繁”“系统繁忙”“网络异常”；
- “该账号疑似存在风险”“账号异常”；
- 弹窗要求下载 App、加入群聊、填写联系方式。

处理方式：不要点弹窗里的任何确认按钮，也不要输入任何信息。能安全返回就按一次返回；否则 `app stop <包名>` 强制停止 App，等 10–30 分钟，再问用户是否继续。**绝不解决验证码、绝不绕过登录，也不要用自动化处理验证或申诉。**

## 第 6 步：输出

给用户的结果至少包含：采集对象、平台 ID、来源页面、抓取时间、关键字段原文、结论（确认/待定/排除及原因）。区分“屏幕原文”和“推断”。不要声称“100% 真人/官方认证”，只说明证据强度。

建议结构：

```text
对象：<昵称> | ID：<平台ID> | 来源：<主页/笔记/评论>
抓取时间：YYYY-MM-DD HH:MM
主页信息：<IP属地/地区/性别年龄/关注粉丝赞藏/简介>
意向证据：<原帖或评论原文 + 时间>
主页内容：<内容类型 + 发帖时间分布>
风险检查：<置顶广告/群聊/报名/模板复用/风控提示>
结论：确认 / 待定（缺什么证据）/ 排除（原因）
```

## 平台差异

| 项目 | Windows | macOS |
|---|---|---|
| Python | `python`（不要用 `python3` 的 Store 存根） | `python3` |
| ADB | 脚本自动查找 `%LOCALAPPDATA%\Android\...`；也可 `--adb` 指定 | 自动查找 `~/Library/Android/sdk/...`、Homebrew；或 `brew install android-platform-tools` |
| USB 驱动 | 可能需要 OEM 驱动；无线调试可绕过 | 通常免驱 |
| Shell | Git Bash 里直接跑 adb 时设 `MSYS_NO_PATHCONV=1`；优先用本脚本，避免路径转换 | zsh/bash 无此问题 |
| 网络 | 同一 Wi-Fi，关闭 AP 隔离；VPN/代理异常时临时直连 | 同左 |

脚本内部用参数列表调用 adb，不经过 shell，所以 Windows 路径含空格/中文也安全。所有 adb 命令都自动带 `-s <serial>`。

## 命令速查

| 命令 | 作用 |
|---|---|
| `devices [--json]` | 列出设备及状态 |
| `info` | 品牌、型号、Android 版本、分辨率、密度 |
| `screen [--json]` | **只读**检查屏幕亮灭/锁屏状态 |
| `connect HOST:PORT` / `pair HOST:PORT CODE` / `mdns` | 无线连接/配对/发现服务 |
| `dump [--json] [--contains X] [--regex R] [--clickable] [--all] [--limit N] [--out FILE] [--raw]` | 解析当前界面 |
| `find TEXT [--regex] [--exact] [--clickable] [--tap] [--index N] [--json]` | 按文字找控件，可点按 |
| `tap X Y` / `swipe X1 Y1 X2 Y2 [--ms N]` / `key back\|home\|enter\|<数字>` | 手势与导航按键（电源/唤醒键会被拒绝） |
| `open URL [--package PKG]` | 用 deep link 打开页面 |
| `app start PKG` / `app stop PKG` | 启动/强制停止 App（`stop` 用于风控停手） |
| `current` | 查看当前前台窗口 |
| `quote TEXT` | URL 编码（用于搜索深链） |

全局参数 `--serial <SERIAL>`、`--adb <PATH>` 可以放在命令任意位置。

## 参考

- [连接、各品牌设置与排错](references/setup.md)
- [小红书采集配方与真伪初筛](references/xiaohongshu.md)
