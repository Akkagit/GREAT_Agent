# GREAT Agent 受邀试用部署设计

用户已接受本聊天上一轮的 Windows 计算节点 + Linux 公网 HTTPS 网关 + 经证书验证的 frp 隧道方案，并确认：尚无域名、尚无云服务器，本机可在试用期间持续开机联网。本文记录已获认可的方案与本次实现范围。

## 目标与边界

- 在现有工作区准备可审查的部署包、检查工具和中文操作手册。当前没有云资源，不实际采购、创建云资源、修改 DNS、安装系统服务、开放端口或发布服务。
- 用户浏览器访问独立域名根路径；Caddy 对所有请求要求每人独立 Basic Auth，并拒绝非本站来源的写请求。
- 网关运行 Caddy 和 frps；Windows 运行 frpc、单 API、单 Solver Worker。分析执行器属于 Worker，知识解析器属于 API，禁止多 API 进程。
- 本机试用实例使用 127.0.0.1:8766，独立 data/trial、runs/trial、knowledge 目录；不复用或迁移当前正式数据库，不停止、重启或改变现有 8765 实例。
- 试用者共用试用实例内数据和权限。首期仅使用管理员准备的 GNSS 数据；合作方需要保密时另开独立实例。
- 保留 Windows 原生执行环境。云端不存放业务 SQLite，不使用网络盘共享 SQLite。

## 替代路线

专用 Windows 云计算节点作为后续迁移选项；本轮先保留现有算力。Linux 全量迁移涉及解算程序、路径与恢复行为回归，不纳入本轮。Cloudflare 正式 Tunnel + Access 可作另行实测后的替代；Quick Tunnel 不支持 SSE，不作为本轮路径。

## 交付接口

- `deploy/gateway/`：Caddyfile、frps TOML、systemd unit、Caddy 环境文件示例。frps 的 18765 仅绑定 127.0.0.1；7000 为经过认证与 TLS 的隧道入口。frp 认证使用已核验的 `auth.tokenSource` 文件方式：网关 `/etc/frp/frps.token`、Windows `deploy/local/frpc.token`。Token 为相同的 64 位十六进制字符串；启动前检查格式和非空，限制文件读取权限。
- `deploy/windows/`：frpc TOML、单 API/Worker/frpc 的 WinSW XML 示例。Windows 隧道回源 127.0.0.1:8766。模型密钥由实际服务账号的环境提供，示例不含真实值。
- `config/trial.example.toml`：保持现有配置 schema 的独立试用配置；管理员复制为被忽略的 `config/local.trial.toml` 后填写程序与输入路径。
- `scripts/deploy_preflight.py`：只读检查配置、路径、运行依赖和静态网页；默认无网络。`--probe` 仅请求配置指定的 127.0.0.1 端口上的 health/readiness/diagnostics；不调用模型、不执行解算、不迁移数据库、不启动服务。JSON 输出不含配置正文、环境变量值、异常正文或 HTTP 错误响应正文。
- `docs/deployment/invited-trial.md`：采购要求、无域名/无服务器时的起点、逐步部署、证书与服务账号、验证、备份与下线。

## 验证与发布门槛

- 先测试再实现检查脚本：非法配置不泄密；没有 --probe 不联网；probe 不跟随重定向、不使用代理、不接受非 loopback 目标；API 存活但 Worker 未就绪不能通过；知识执行器异常不能通过；明确不把静态检查通过写成公网就绪。
- TOML/XML 实际解析；能取得工具时再用 Caddy/frp 自身验证语法，工具缺失则记录未验证，不自动下载或安装。
- 使用隔离测试目录；保持既有业务代码、运行配置和服务不变。用户现有大量未提交改动不纳入本次提交，不创建或切换分支。
- 现有备份检查会拒绝同一 project_root 下其他 API/Worker 尚在运行的情况，即便试用数据目录不同；共享 checkout 的构建目录也会影响当前服务。模板默认路径用于准备配置；正式邀请前应使用完整独立发布副本，并一致调整 project_root、WinSW 和隧道路径，再安装该副本依赖。不得绕过备份保护或擅自停止原服务。
- 域名、服务器、证书、实际服务账号就绪后，另行完成从外网的登录、SSE、上传、PDF、模型取消、解算与重启验收。此门槛前不得声称可对外使用。
