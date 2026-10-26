# 连接、品牌设置与排错

## 1. 先确认设备类型

| 设备 | 是否可用 | 说明 |
|---|---|---|
| Android 手机/平板 | 可用 | 小米/红米/POCO、华为/荣耀（EMUI/MagicOS）、OPPO/一加/realme、vivo/iQOO、三星、Pixel、摩托罗拉等 |
| iPhone / iPad | 不可用 | 用 Xcode / WebDriverAgent，不走 ADB |
| 鸿蒙 NEXT（纯血鸿蒙） | 不可用（ADB） | 需要用 HDC；先和用户确认，不要假装能连 |
| 鸿蒙 4.x / EMUI（兼容安卓） | 通常可用 | 本质仍支持 ADB |

脚本的 `info` 会读取 `ro.product.brand`、`ro.product.model`、`ro.build.version.release`。**不要默认是小米**；先看输出，或者在没连上时问用户品牌 / 系统版本 / USB 还是无线。

---

## 2. 安装 adb

### Windows

1. 下载 Android SDK Platform-Tools（Google 官方 zip），解压到例如：
   `C:\Users\<你>\AppData\Local\Android\Sdk\platform-tools\`。
2. 脚本会自动查找这些位置：
   - `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`
   - `%LOCALAPPDATA%\Android\platform-tools\adb.exe`
   - `%USERPROFILE%\AppData\Local\Android\Sdk\platform-tools\adb.exe`
   - `C:\Android\platform-tools\adb.exe`
   - `C:\platform-tools\adb.exe`
3. 也可以在命令任意位置加 `--adb "D:\tools\platform-tools\adb.exe"`。
4. 验证：`adb version`。
5. Python：用 `python`，不要用 `python3`。Windows 的 `python3` 可能是应用商店存根，会静默失败（退出码 49）。

USB 驱动：
- Pixel / 通用设备：Google USB Driver。
- 小米/红米：通常 Windows 自动识别；不行就装小米 USB 驱动，或改用无线调试。
- 三星/OPPO/vivo/华为：部分机型需要厂商 USB 驱动。无线调试可以绕过驱动问题。

### macOS

```bash
brew install android-platform-tools
# 或使用 Android Studio 自带的：
# ~/Library/Android/sdk/platform-tools/adb
```

脚本会自动查找 `~/Library/Android/sdk/platform-tools/adb`、`/opt/homebrew/bin/adb`、`/usr/local/bin/adb`。验证 `adb version`。Python 用 `python3`；没有就 `brew install python`。

如果是手动解压官方 zip，可能需要给 adb 执行权限：

```bash
chmod +x ~/Library/Android/sdk/platform-tools/adb
```

macOS 一般不需要额外 USB 驱动。若 `adb devices` 为空：换线/换口，确认线支持数据传输；用 `system_profiler SPUSBDataType` 看系统是否识别到手机。

---

## 3. 开启开发者选项与 USB 调试

通用步骤：

1. 设置 → 关于手机 → 连续点击“版本号”7 次。
2. 设置 → 系统/更多设置 → 开发者选项。
3. 打开“USB 调试”。
4. 插线后手机会弹“是否允许 USB 调试”，点允许（建议勾选“始终允许”）。

品牌差异：

- **小米/红米/POCO（MIUI/HyperOS）**：开发者选项里还有“USB 调试（安全设置）”。如果 `input tap/swipe` 没反应、或需要模拟点击/授权类操作，把它也打开（可能需要登录小米账号）。另外确认“USB 安装”不是必须的——本技能不安装 App。
- **华为/荣耀（EMUI/MagicOS）**：USB 调试在开发者选项；部分机型需要开启“仅充电时允许 ADB 调试”。鸿蒙 NEXT 不支持 ADB。
- **OPPO/一加/realme（ColorOS）**：开发者选项 → USB 调试；部分系统需要关闭“权限监控”或开启“禁止权限监控”。
- **vivo/iQOO（OriginOS）**：开发者选项 → USB 调试；部分机型需开启“USB 模拟点击”类选项。
- **三星**：开发者选项 → USB 调试；首次连接需要在手机上允许。
- **Pixel/AOSP**：标准流程。

如果用户不愿意开 USB 调试，说明无法使用本技能；不要尝试绕过系统安全设置。

---

## 4. 无线调试

### Android 11 及以上（推荐）

前提：手机和电脑在同一局域网，手机已开开发者选项。

1. 开发者选项 → 无线调试 → 打开。
2. 点“使用配对码配对设备”，屏幕显示：
   - `IP 地址和端口`（配对端口，例如 `192.0.2.50:37123`；`192.0.2.0/24` 是文档保留地址，替换成手机实际显示的）
   - 6 位配对码
3. 在电脑上：
   ```bash
   python adb_ui.py pair 192.0.2.50:37123 123456
   ```
4. 配对成功后，回到无线调试主页面，找到“IP 地址和端口”（连接端口，**通常不是配对端口**）：
   ```bash
   python adb_ui.py connect 192.0.2.50:41381
   python adb_ui.py devices
   ```

配对码只用于本次配对，不要写进脚本、日志或仓库。
5. 端口在关闭/重开无线调试、切换网络后可能改变。连不上就重新看手机上的端口。
6. 可选：`python adb_ui.py mdns` 查找 `_adb-tls-connect._tcp` 服务。不同 adb 版本对此支持不一致；没有输出就直接用手机显示的 IP:端口。

注意：

- 配对码过期很快，重新生成即可。
- 手机锁屏后无线调试可能断开；重新打开无线调试并 `connect`。
- 同一台手机可能同时出现 `IP:PORT` 和 `..._adb-tls-connect._tcp` 两条，选 IP 那条，后续用 `--serial` 固定。

### Android 10 及以下

先 USB 连接并授权，然后：

```bash
adb -s <SERIAL> tcpip 5555
python adb_ui.py connect <手机IP>:5555
```

之后可以拔线。手机重启后需要重新执行 `adb tcpip 5555`（或重新插线）。

### 网络排查

- 手机和电脑必须同一 Wi-Fi；访客网络/公司网络可能开启 AP 隔离，设备互相不可见。
- VPN/代理（Clash、公司 VPN）可能改变局域网路由；连不上时临时断开 VPN 再试。
- Windows 防火墙可能拦截 adb；允许 adb 的专用网络访问。
- 用手机浏览器访问电脑上的服务、或用 `ping <手机IP>` 验证连通性（部分手机禁 ping，不能仅凭 ping 判断）。
- USB 网络共享/手机热点也可以：电脑连手机热点后，用热点网段 IP 连接。

---

## 5. 多设备与 serial

```bash
python adb_ui.py devices
```

输出里的第一列就是 serial。后续命令固定加：

```bash
--serial 192.0.2.50:41381
```

或者设置环境变量（脚本和 adb 都会读取）：

- macOS / Git Bash：`export ANDROID_SERIAL=192.0.2.50:41381`
- PowerShell：`$env:ANDROID_SERIAL="192.0.2.50:41381"`
- CMD：`set ANDROID_SERIAL=192.0.2.50:41381`

同一台手机出现两条传输（IP + mDNS）不是两台手机。不要盲目取第一台；先看型号，再用 IP 那条。

---

## 6. 常见问题

### `devices` 没有设备

1. 换一根支持数据传输的线 / 换 USB 口（避免只充电线）。
2. 手机通知栏的 USB 模式改成“文件传输/MTP”。
3. 开发者选项里关闭再打开 USB 调试。
4. 撤销 USB 调试授权，重新插线并在手机上允许。
5. Windows 装厂商 USB 驱动；macOS 检查 `system_profiler SPUSBDataType`。
6. 无线调试：检查同一 Wi-Fi、端口、配对是否成功。

### `unauthorized`

- 解锁手机，点“允许 USB 调试”，勾选“始终允许”。
- 没弹窗：撤销 USB 调试授权 → 重插；或 `adb kill-server` 后重试。
- 无线调试的授权弹窗有时不会出现，删掉配对重新配。

### `offline`

- `adb reconnect` / 重插 USB / 重开无线调试。
- `adb kill-server` 再 `devices`。
- 端口变了：重新看手机上的连接端口。

### `more than one device/emulator`

脚本会直接报错并列出 serial；用 `--serial` 指定。不要用 `-s` 猜。

### dump 报错、为空或只有两三个节点

1. 屏幕是否熄灭/锁屏：先运行 `python adb_ui.py screen` 只读检查；本技能**不会**自动唤醒或解锁。请用户手动点亮并解锁后重试。
2. App 是否在前台：`current` 查看；不在就让用户打开，或用 `open`。
3. 等动画结束（1–2 秒）再 dump。
4. 有些 App 用 WebView/Canvas/游戏引擎，不暴露无障碍树；dump 里没有文字是正常的。此时不要编造内容；征得用户同意后才可以截图辅助。
5. 部分系统在 `uiautomator dump` 时要求屏幕保持常亮；可以临时让用户把息屏时间调长（不要改系统设置，除非用户同意）。

### 屏幕锁着 / 需要唤醒

本技能不提供唤醒或解锁能力：

- 不要发送电源键（26）、休眠键（223）或唤醒键（224），也不要做上滑解锁；
- `screen` 只报告 `screen: on/off` 和 `keyguard: showing/not showing`；
- 锁屏或息屏时，请用户手动点亮并输入自己的 PIN/图案/密码；
- 如果用户不在设备旁，停止操作并等待，不要尝试代替用户解锁。

### 点按没反应或点错

1. 坐标是否来自**当前** dump？页面滚动/加载后坐标会变。
2. 分辨率是否变了？`info` 看 screen；不要跨设备复用坐标。
3. 小米/红米：打开“USB 调试（安全设置）”。
4. 控件被弹窗/广告遮挡：先 dump 看最上层节点。
5. 系统手势区域/导航栏可能拦截边缘点按；点控件中心，不要点边缘。
6. 动画进行中：等 1–2 秒再点，点完再 dump 验证。

### 中文乱码

脚本会把 stdout/stderr 设为 UTF-8。若仍有问题，显式设置：

- Windows：`set PYTHONIOENCODING=utf-8`（CMD）或 `$env:PYTHONIOENCODING="utf-8"`（PowerShell）
- macOS：`export PYTHONIOENCODING=utf-8`

### Windows Git Bash 路径被改写

直接执行 `adb shell ... /sdcard/...` 时，MSYS 可能把 `/sdcard` 改成 `C:/Program Files/Git/sdcard`。解决：

```bash
export MSYS_NO_PATHCONV=1
```

本脚本通过 subprocess 参数列表调用 adb，不经过 shell，所以脚本命令不受影响。

### macOS 提示 adb 无法打开/权限问题

- `xattr -d com.apple.quarantine /path/to/adb`（仅从官方 zip 解压时可能需要）。
- 系统设置 → 隐私与安全性 → 仍要打开。
- 用 Homebrew 安装可避免。

---

## 7. 安全边界

- 只连接用户自己的设备；不要连接陌生设备。
- **不自动唤醒、不自动解锁**：不发送电源/唤醒键，不上滑解锁，不调用 `dismiss-keyguard`，不输入 PIN/图案/密码。运行前由用户手动解锁。
- 不要在脚本/命令里放手机号、验证码、密码、Cookie、支付信息；不要保存配对码。
- 遇到登录/绑定/验证码/风控弹窗，立即停手并交还用户；不要尝试绕过，也不要用自动化处理验证或申诉。
- 同一时间只开一个 ADB 采集会话，避免两个流程抢同一台手机。
- 采集的数据按最小化原则保存；不需要就删除原始 dump。
- 本技能不会绕过 App 的登录、付费、权限或反爬机制。
