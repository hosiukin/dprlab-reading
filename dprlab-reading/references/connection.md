# Phase 1 详解 — 用 lab-connect 连接 spark

## 链路拓扑

```text
你的电脑 ──EasyConnect(VPN)──> 跳板 10.30.24.1:22 ──ProxyJump──> spark 10.33.250.29:22
                                                                │
                                     TCP 转发: 本地 18000 ──────┘→ spark 侧 127.0.0.1:8000 (vLLM)
```

- vLLM 服务跑在 spark 的 host 网络端口 8000，只从 spark 本机回环访问最稳（转发目标一律
  `127.0.0.1:8000`）。
- 本地端口统一用 **18000**，避免和本机常见服务（8000/8080）冲突。所有工具都用
  `http://127.0.0.1:18000/v1` 访问模型。

## 前置条件

| 条件 | 检查方式 |
|---|---|
| EasyConnect 已连接 | 客户端显示已登录；未连接时一切 SSH 都会失败 |
| Python ≥ 3.10 | `python --version`（lab-connect 启动器可用 winget 装 3.12） |
| OpenSSH 客户端 | `ssh -V`（Windows 10+ / macOS 自带） |
| 跳板账号 + spark 账号 | 各自的名义账号（问用户/管理员开通） |
| SSH 密钥 | `ls ~/.ssh/id_*`；没有就 `ssh-keygen -t ed25519`，lab-connect 向导可代为部署公钥 |

## 安装与启动 lab-connect

```bash
git clone https://github.com/hosiukin/lab-connect.git
```

- **Windows**：解压/clone 后确认 `start-windows.bat` 与 `lab_connect.py` 同目录，双击
  `start-windows.bat`。SmartScreen 拦截 → **More info → Run anyway**。保持启动器窗口开着。
  防火墙询问时允许（web 界面只绑定 127.0.0.1，无外部暴露）。
- **macOS**：双击 `start-macos.command`；被 Gatekeeper 拦 → 右键 → 打开。

浏览器会自动打开本地配置页（127.0.0.1 随机端口 + 会话令牌）。

## 配置 profile（向导里填什么）

新建 profile，**Profile name 填 `spark`**（这个名字直接成为 ssh 别名，之后 `ssh spark`
和 VS Code Remote-SSH 都能用它）：

| 向导字段 | 填写值 |
|---|---|
| Profile name | `spark` |
| Jump host | `10.30.24.1` |
| Jump port | `22` |
| Jump user | 用户自己的跳板账号 |
| Target host | `10.33.250.29` |
| Target port | `22` |
| Target user | 用户自己的 spark 账号 |
| Direction | forward（默认） |

首次保存时向导可代为部署公钥到跳板和目标机（需要输一次密码，密码不落盘）。

## 添加端口转发（Port forwards 页）

| 字段 | 值 |
|---|---|
| Name | `spark-vllm` |
| Local port | `18000` |
| Target-side host | `127.0.0.1` |
| Remote port | `8000` |
| Open mode | forwarding only |

点 **Save and start all forwards**。关掉配置页面**不会**停掉已建立的隧道；隧道由后台 SSH
维持，机器重启后需重新启动 forwards。

生成物（供排障参考）：`~/.lab-connect/config.json`（持久配置）、`~/.ssh/lab-connect.conf`
（生成的 ssh 配置，自动 `Include` 进 `~/.ssh/config`，改配置请走 UI 不要手编）。

## 验证

```bash
python <skill目录>/scripts/check_connection.py
# 或自定义参数
python check_connection.py --base-url http://127.0.0.1:18000/v1 --model clinical-qwen36-35b
```

通过的标准输出：models 列表包含 `clinical-qwen36-35b`（或别名 `clinical-qwen3-4b`），
且 1-token 对话返回非空。也可以手动 curl：

```bash
curl -s http://127.0.0.1:18000/v1/models -H "Authorization: Bearer local-vllm"
```

## 排障

| 症状 | 原因与处置 |
|---|---|
| SSH `exit 255` 反复重连失败 | EasyConnect 没连/掉线（最常见）；或账号密码错、公钥未部署。先确认 EasyConnect 在线 |
| 连接被拒（ConnectionRefused，本地 18000） | 转发没启动：回 lab-connect UI 重新 Start all forwards |
| `check_connection.py` 超时 | EasyConnect/跳板网络不通；或本机代理变量劫持了 127.0.0.1（脚本已强制直连；手动 curl 前设 `NO_PROXY=127.0.0.1,localhost`） |
| HTTP 401 | API key 不对：必须是 `local-vllm`（`Authorization: Bearer local-vllm`） |
| HTTP 404 | base_url 少了 `/v1` 后缀，或打到了错误端口 |
| 模型列表为空/缺模型 | spark 侧 vLLM 服务异常 → 找管理员，不要自行登录服务器处置容器 |
| Windows 启动器闪退 | 看 `%USERPROFILE%\.lab-connect\launcher.log`；确认 `start-windows.bat` 与 `lab_connect.py` 同目录（不要单独下载 bat） |

## 本机代理提醒

实验室内网常有 HTTP 代理（如 `http_proxy` 指向局域网代理）。访问 `127.0.0.1:18000` **必须绕过代理**：
`check_connection.py` 已内置直连（ProxyHandler({})）；用户自己写代码时记得同样处理
（urllib 设 `no_proxy`，requests 用 `proxies={"http": None, "https": None}` 或 trust_env=False）。
