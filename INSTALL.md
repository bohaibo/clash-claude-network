# 安装与运行

本项目只支持 **Windows 11 x64**。除 **Clash Verge Rev 及其内核**外，Python、PyYAML 和 Node.js 都会自动安装到项目自己的 `.runtime/` 目录，不写系统 PATH、不注册系统 Python、不安装到 Program Files。

## 1. 前置条件

必须由你手动准备：

- **Clash Verge Rev**：从 https://github.com/clash-verge-rev/clash-verge-rev/releases 安装。
- 在 Clash Verge Rev 中导入至少一个订阅，并启动服务 `clash_verge_service`。
- 浏览器：Windows 11 自带 Microsoft Edge；也可以使用已安装的 Chrome。项目不会修改它的默认资料。
- 可访问互联网（首次下载运行环境）。

不需要手动安装：

- Python 3.13（项目的 `.runtime/python313`）
- PyYAML（装进项目 Python）
- Node.js（项目的 `.runtime/node`）

## 2. 一键安装

双击项目根目录的 **一键网络.cmd**。首次启动会：

1. 检查 Clash Verge 服务和数据目录；缺失时停止并给出安装链接。
2. 下载 Python 3.13 embeddable 包到 `.runtime/python313/`。
3. 安装 pip 和 `PyYAML>=6.0.3` 到项目 Python。
4. 下载 Node.js LTS win-x64 zip 到 `.runtime/node/`。
5. 写入 `.local/env.json`，记录项目内解释器路径。
6. 打开 GUI。

安装过程不会：

- 修改系统 PATH 或注册表环境变量；
- 安装系统级 Python/Node/PyYAML；
- 需要管理员权限（只有后续配置/恢复操作会请求 UAC）；
- 安装 Clash Verge 或替换它的内核。

首次下载约 50 MB；网络较慢时可能需要几分钟。下载支持断点续传，失败后再次运行 `一键网络.cmd` 会继续。

## 3. 验证项目环境

在项目根目录运行：

```powershell
.\.runtime\python313\python.exe -c "import sys, yaml; print(sys.version); print(yaml.__file__)"
.\.runtime\node\node.exe --version
```

两个命令都应成功，并且 PyYAML 路径应位于 `E:\...\clash-claude-network\.runtime\python313\Lib\site-packages`，不是用户 AppData。

界面状态页显示“项目内 Python 3.13 / Node.js”即表示运行环境正确。

## 4. 使用

1. 确认 Clash Verge 服务正在运行。
2. 双击 `一键网络.cmd`。
3. 在「一键配置」选择订阅和节点；出口地区会从节点名/订阅名自动填入，可手动修改。配置会按节点地区设置 Windows 系统时区和沙箱浏览器语言；Claude 桌面、Claude 网页和沙箱浏览器读取同一时区。系统时区/Claude 桌面通常需重启，沙箱语言需重新打开沙箱后生效。
4. 点击开始配置，按 Windows UAC 提示确认。
5. 首次配置自动保存恢复点；完成后在「浏览器沙箱」打开浏览器（默认空白页），按需输入网站地址。

命令行备用入口：

```text
一键网络.cmd --menu
```

## 5. 手动/离线安装

如果自动下载被防火墙拦截，可手动准备同样的项目内目录：

### Python 3.13

1. 从 https://www.python.org/downloads/windows/ 下载 **Windows embeddable package (64-bit)**，例如 `python-3.13.17-embed-amd64.zip`。
2. 解压到 `.runtime/python313/`。
3. 编辑 `.runtime/python313/python313._pth`，内容为：

```text
python313.zip
.
Lib\site-packages
..\..\scripts
import site
```

4. 下载 https://bootstrap.pypa.io/get-pip.py 到 `.runtime/downloads/`，运行：

```powershell
.\.runtime\python313\python.exe .\.runtime\downloads\get-pip.py
.\.runtime\python313\python.exe -m pip install --no-user -r requirements.txt
```

### Node.js

1. 从 https://nodejs.org/en/download 下载 **Windows x64 ZIP**。
2. 解压，将其中的 `node.exe` 等文件放到 `.runtime/node/`（`node.exe` 位于该目录根部）。

### 写入环境记录

运行一次 `一键网络.cmd`，或手动创建 `.local/env.json`：

```json
{
  "python": "C:\\Projects\\clash-claude-network\\.runtime\\python313\\python.exe",
  "pythonw": "C:\\Projects\\clash-claude-network\\.runtime\\python313\\pythonw.exe",
  "node": "C:\\Projects\\clash-claude-network\\.runtime\\node\\node.exe"
}
```

路径必须使用绝对路径。

## 6. 更新与卸载

- 更新项目代码后直接运行 `一键网络.cmd`；`.runtime` 会保留。
- 想重装运行环境：关闭界面，删除 `.runtime`，再次运行 `一键网络.cmd`。
- 想彻底卸载：关闭界面，删除项目目录。`.local` 含快照和本机记录，删除前请确认不再需要恢复点。
- 项目不会在系统目录留下 Python/Node；只会在当前用户计划任务中注册规则更新任务（可在界面关闭/改为手动）。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| 提示未检测到 Clash Verge | 先安装 Clash Verge Rev，启动服务，导入订阅后再运行 |
| Python 下载失败/很慢 | 重新运行 `一键网络.cmd` 继续断点续传；或按第 5 节手动放入 `.runtime/python313` |
| `python313._pth` 缺少 `..\..\scripts` | 按第 5 节重写该文件，否则 Python 找不到 `checks.py` |
| PyYAML 装到了用户目录 | 确认设置了 `PYTHONNOUSERSITE=1`；删除用户目录中的 `Python313\site-packages\yaml`，重新运行 bootstrap |
| Node 下载失败 | 按第 5 节手动放入 `.runtime/node/node.exe` |
| 浏览器沙箱打不开 | 确认已安装 Edge 或 Chrome；查看 `.local/browser/engine.log` |
| 设为默认浏览器后没有变化 | Windows 要求在「设置 → 默认应用」中手动确认；界面会打开该页面 |
| 定时任务没有运行 | 确认任务已注册、Windows 用户已登录、间隔天数/时间正确 |

## 8. 安全边界

- GUI 只监听 `127.0.0.1`，使用随机端口、一次性令牌和同源校验。
- 运行环境完全在项目目录内；不要从管理员终端启动，普通权限即可。
- 浏览器沙箱是独立资料目录，不是操作系统级沙箱；仅 WebRTC 保护会写入受支持浏览器的机器级策略，并支持恢复。
- 配置/恢复会修改防火墙、IPv6、区域语言和代理设置；严格防火墙在核心停止时会阻断所有公网。

配置及补充配置会关闭自动定位、自动时区和 Windows 自动校时，按所选地区设置国家/地区、区域格式及预设时区。新增状态在改动前保存为恢复点内的 region-time.json；首次基线不覆盖。恢复还原服务启动/运行/延迟启动状态、校时模式和原地区设置，不回拨备份日期。关闭校时后可能产生误差；实际 Windows 行为及联网校时需实测。
