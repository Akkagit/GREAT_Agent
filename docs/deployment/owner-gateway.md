# 当前所有者的远程访问网关

2026-11-18 启用。入口为 `https://great-assistant.com/?workspace=research`，浏览器先完成网关 Basic Auth 登录，再进入现有三个工作区。账号为 `owner`，随机密码仅保存在部署机器的 `deploy/local/gateway-owner-credentials.json`，不进入 Git、文档、命令行或截图。

## 实际部署边界

```text
浏览器 → HTTPS / Caddy（阿里云香港）
       → 云端 127.0.0.1:18765
       → 专用账号的反向 SSH 隧道
       → Windows 127.0.0.1:8765 / 现有 API
       → 本机数据库、文献、代码及独立 research-worker
```

本次接通的是当前所有者已有的数据和配置，没有把后端或数据库迁往云端，也没有创建空的试用库。与 `invited-trial.md` 中的独立 `8766 + FRP` 方案不同，这里复用本机 `8765`，使用受限反向 SSH，不增加 FRP 服务或公网 7000 端口。云端只承担 HTTPS、登录保护和转发。

科研分析仍仅提供关联读取、诊断和建议。部署不启动 solver；核验不创建课题、不发起提问、不调用模型、不运行实验。真正提问时，由现有科研分析队列和独立 worker 处理。

## 服务和凭据

- 云端 Caddy 2.11.9 使用官方 Linux amd64 包，按官方 SHA-512 清单校验；由 `caddy.service` 开机启动，证书由 Caddy 自动管理。
- HTTP 自动转向 HTTPS；包括首页、静态资源及健康接口在内的 HTTPS 请求均先经过 Basic Auth。转发时移除 `Authorization`，不把网关密码传入应用。写请求要求 `Origin: https://great-assistant.com`。
- 反向隧道使用独立 `great-tunnel` 用户和独立本机密钥；只允许远程转发到云端 `127.0.0.1:18765`。`GatewayPorts no`、`MaxSessions 0`、无密码登录、无交互终端，不能用该密钥执行云端命令。
- 云端配置在 `/etc/caddy/Caddyfile`、`/etc/caddy/great-agent.env`、`/etc/ssh/great-agent-tunnel.conf` 和对应 systemd unit。认证环境文件只保存密码哈希；本机凭据和隧道私钥使用受限 Windows ACL。
- SSH 主配置修改前副本位于 `/var/backups/great-agent-gateway/20261118T124314Z/`。修改 SSH 后已通过 `sshd -t` 并保持原管理入口可用。

## Windows 启停及维护

Windows Task Scheduler 中注册三个隐藏任务：`GREATAgent-ResearchApi`、`GREATAgent-ResearchWorker`、`GREATAgent-ResearchTunnel`。任务以当前 Windows 用户的 InteractiveToken 和普通权限运行，无保存的登录密码；只在该用户已登录时运行。包装器等待真实子进程，避免退出 Codex 工具调用时连带结束服务。任务配置登录触发、失败重试及无限执行时间；隧道另有每分钟重复触发并忽略重复实例，用于断网后的重连。

使用本机私有管理入口查询状态：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File F:\GREAT_Agent\deploy\local\manage-research-tasks.ps1 -Action status
```

备份、迁移或升级前，先执行：

```powershell
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File F:\GREAT_Agent\deploy\local\manage-research-tasks.ps1 -Action stop
```

该入口先禁用全部三个任务再停止，并核对进程创建时间、可执行文件和完整参数，仅清理本次登记的子进程。确认本机 `8765` 端口关闭后再维护。只运行旧的 `scripts/stop.ps1` 无法阻止 Task Scheduler 重启，不能用于这套部署的维护停机。

完成维护后，以相同入口执行 `-Action start`，再核对本机与公网健康状态。启动包装器会检查实际 schema `0010`；存在排队/运行中的科研任务或未完成的模型调用记录时拒绝自动启动，须先检查调用结果，避免重复计费。不要通过清库或删除调用记录绕过检查。

**当前可用性依赖 Windows 电脑保持开机、联网且部署用户已登录。** 云服务器可独立运行，但电脑休眠、关机或用户注销后，认证成功的页面可能返回 502。若后续需要全天候访问，应单独规划完整后端迁移或 Windows 系统服务化及计算资源迁移。

## 核验记录

本机私有证据位于 `data/activations/public-gateway-20261118/`，不随源码发布：

- `public-https-verification.json`：HTTP 308；HTTPS 首页、health、API-only readiness 未认证为 401、正确认证为 200；错误密码 401；TLS 1.3，Let's Encrypt 证书校验通过。仅向不存在的探测路径发送 POST，缺失或异站 Origin 返回 403、同站 Origin 返回应用 405，未触发业务写入。
- `research-windows-tasks.json`、各角色 runtime 记录和任务定义核验：三个任务与其实际子进程身份、维护方式和会话可用性边界。
- `browser-readonly-public-gateway.json` 及桌面/移动截图：正式 Chrome 认证后页面 200、三个工作区导航与空科研课题表单正常、390px 宽度无横向溢出。全程 7 个同源 GET，页面/HTTP 错误、业务写入、模型及实验操作均为 0；不记录认证头、密码、HTML、HAR 或 trace。

API 的已有诊断标签仍可能显示 revision `0009`；此处迁移和启动依据是 SQLite `alembic_version=0010` 及实际运行 schema 校验。不要仅依据展示标签执行降级。

阿里云 Domain MCP 已加入当前客户端配置，但其 OAuth issuer/callback 兼容问题仍独立待解决；本次没有依赖该 MCP 写 DNS，现有域名解析已指向云服务器。
