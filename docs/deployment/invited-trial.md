# GREAT Agent 受邀试用部署操作手册

本文交付的是部署准备材料，尚未购买域名或服务器，也没有已上线的试用地址。下面的安装、迁移、开放端口和验收命令，供管理员在对应前提完成后手动执行；它们不属于只读检查脚本的行为。

采用已确定的路线：当前 Windows 电脑保留解算环境；Linux 云主机仅运行 Caddy 和 frps，浏览器经 HTTPS、每人独立 Basic Auth、frp 隧道访问 Windows 上的独立试用实例。电脑需要持续开机、联网并关闭试用期间的自动睡眠。首期只提供管理员准备的 GNSS 输入，所有试用者共用实验、对话、知识库和产物；Basic Auth 账号没有租户、角色或资源隔离能力。不能把需互相保密的合作方放进同一个实例。

## 1. 采购与网络前提

按以下顺序操作，先确认接入条件，再付款：

1. 确定运营主体、试用人所在地区、数据是否允许经过所选网关地区，以及单位和本地网络是否允许向外建立长期 TCP 隧道。向云厂商确认受邀 Web 试用、反向代理和 frp 符合其服务条款，确认公网 IPv4、TCP 80/443/7000、出站 DNS/HTTPS 的可用性，以及域名实名、备案和接入要求。
2. 注册一个自己管理的域名，开启注册商账号 MFA、自动续费和到期通知。准备两个子域名：下文 `trial.example.com` 表示浏览器入口，`relay.example.com` 表示隧道服务器身份；都必须换成实际购买域名下的名称。文中的 `example.com` 不能用于实际申请证书或访问。
3. 购买小规格 Linux 网关：Ubuntu 22.04 LTS 或 24.04 LTS、x86_64，独享或可稳定使用的公网 IPv4。可从约 1–2 vCPU、1–2 GiB 内存、20 GiB 系统盘起步，再按连接数和流量调整；这是容量起点，不是性能或价格承诺。确认流量额度、带宽、超额计费和续费价格。此路线不需要购买 Windows 云计算节点或 GPU；GREAT、SQLite、知识文件和模型资源继续留在 Windows。
4. 中国内地节点应先通过实际接入商完成适用的 ICP 备案及接入手续，再开放网站。不要假定“受邀”“加密码”“只是 API”自动豁免。阿里云官方流程明确区分内地节点与香港/境外节点，并要求使用其内地节点时在其平台办理；换厂商应按实际接入商要求办理。[阿里云 ICP 流程](https://help.aliyun.com/en/icp-filing/basic-icp-service/user-guide/icp-filing-application-overview)、[通信管理局办事指南](https://zjca.miit.gov.cn/bsfw/bszn/fjyxhlwxxfwbahz/art/2020/art_27ba712270224ad2a6a9db8e7703dfb9.html)
5. 如选择香港或境外网关，先让真实试用网络测试可达性和延迟；厂商对其境外节点的 ICP 说明不替代运营主体、数据或单位的其他要求。不要承诺跨运营商、跨境网络一定稳定。Windows 主动连出 7000，不需要在家用路由器上转发 8766，也不应把该端口开放到局域网或公网。

服务器先只开放受限来源 IP 的 SSH，使用密钥登录并确认第二个 SSH 会话可用。业务配置验证完成前保留云防火墙的默认拒绝策略；不要为排障关闭整机防火墙。正式需要的入口如下，云安全组和主机防火墙必须同时核对：

| 位置 | 端口 | 可达范围与用途 |
| --- | --- | --- |
| Linux | TCP 22 或实际 SSH 端口 | 仅管理员出口 IP；变更前保留可用连接 |
| Linux | TCP 80、443 | 公网；Caddy 证书签发、HTTP 跳转、HTTPS |
| Linux | TCP 7000 | Windows frpc 的出站来源；能固定来源时限制 IP，动态来源需记录并监控 |
| Linux | TCP 18765 | **仅 127.0.0.1**；frps 回源口，安全组不放行 |
| Windows | TCP 8766 | **仅 127.0.0.1**；试用 API |
| Windows | TCP 8765 | 已有实例；本手册不改变它 |

HTTP/3 的 UDP 443 不是本路线的必要条件。不要开放 frp 管理面板、Caddy 管理 API 或数据库端口。网关管理员仍能访问回源口，因此仅给可信管理员 Linux 访问权限。

## 2. 固定接口和模板清单

仓库样例以 `F:\GREAT_Agent` 为根，试用数据接口为：

| 项目 | 仓库样例值 |
| --- | --- |
| 配置 | `config\trial.example.toml` 复制为私有 `config\local.trial.toml` |
| 监听 | `127.0.0.1:8766` |
| 数据库 | `F:\GREAT_Agent\data\trial\great_agent.db` |
| 解算与 API 日志 | `F:\GREAT_Agent\runs\trial` |
| 知识原件与缓存 | `F:\GREAT_Agent\data\trial\knowledge` |
| 并发 | `max_concurrency = 1`，`pppflt.default_concurrency = 1` |
| Windows 守护 | `GreatAgentTrialApi`、`GreatAgentTrialWorker`、`GreatAgentTrialTunnel` |
| 隧道 | Windows `127.0.0.1:8766` → Linux `127.0.0.1:18765`，控制连接 TCP 7000 |

模板到运行文件的对应关系如下。只编辑私有运行副本，不把口令、模型密钥、Token、证书私钥或服务账号密码写进样例和版本控制。

| 仓库文件 | 部署位置 |
| --- | --- |
| `deploy/gateway/Caddyfile.example` | Linux `/etc/caddy/Caddyfile` |
| `deploy/gateway/caddy.env.example` | Linux `/etc/caddy/trial.env` |
| `deploy/gateway/frps.example.toml` | Linux `/etc/frp/frps.toml` |
| `deploy/gateway/frps.service` | Linux `/etc/systemd/system/frps.service` |
| `deploy/windows/frpc.example.toml` | Windows `<试用根>\deploy\local\frpc.toml` |
| `deploy/windows/GreatAgentTrialApi.xml.example` | Windows `<试用根>\deploy\local\GreatAgentTrialApi.xml` |
| `deploy/windows/GreatAgentTrialWorker.xml.example` | Windows `<试用根>\deploy\local\GreatAgentTrialWorker.xml` |
| `deploy/windows/GreatAgentTrialTunnel.xml.example` | Windows `<试用根>\deploy\local\GreatAgentTrialTunnel.xml` |

frp 使用相同的私有 Token 文件：Linux `/etc/frp/frps.token` 与 Windows `<试用根>\deploy\local\frpc.token`。无需 `FRP_TOKEN` 环境变量；不要把旧版环境变量说明与本套 `auth.tokenSource` 配置混用。Caddy 环境变量为 `TRIAL_HOST`、`TRIAL_ADMIN_HASH`、`TRIAL_INVITEE_01_HASH`，模型密钥环境变量为 `GREAT_AGENT_MODEL_API_KEY`。

只运行一个 API、一个 Solver Worker 和一个 frpc。知识解析执行器由 API 管理，分析执行器由 Worker 管理；不要另开第三个分析服务、使用 Uvicorn 多 Worker 或增加共享同一数据库的 Worker。

## 3. 固化当前工作区，选择独立发布根

当前目录可能有大量未提交或未跟踪的业务改动。`git clone`/`git archive HEAD` 不能代表当前已验证版本，也不要通过提交、清理、重置或切换分支来准备试用。先暂停编辑，在磁盘保留**当前文件内容的完整代码快照**，同时记录锁文件、构建、验证报告和文件摘要；业务数据与密钥另行备份。

**推荐在发出邀请前使用独立完整发布目录。** 原目录的 API 会读取共享 `frontend/dist`，而已有备份工具还会拒绝同一 `project_root` 下的任何存活 API/Worker，即使数据库路径不同。独立发布根同时解决构建覆盖和离线备份阻塞。以下 PowerShell 从当前工作区复制文件，不启动服务；发布目录放在已排除的 `local` 下，避免复制自身：

```powershell
Set-Location -LiteralPath 'F:\GREAT_Agent'
$sourceRoot = (Get-Location).Path
$releaseStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$trialRoot = Join-Path $sourceRoot "local\trial-release\$releaseStamp"
if (Test-Path -LiteralPath $trialRoot) { throw '发布目录必须为新目录。' }
New-Item -ItemType Directory -Path $trialRoot | Out-Null
$excludedDirs = @('.git', '.venv', '.worktrees', '.pytest_cache', '.tmp', 'tmp', 'output', 'runs', 'data', 'local', '.codex', '.codex_work', '.agents', '.aws', 'node_modules', '__pycache__')
& robocopy $sourceRoot $trialRoot /E /XJ /XD $excludedDirs /XF 'local*.toml' '.env*' '*.token' '*.key' '*.pem' '*.pfx' '*.p12' '*.pyc' /R:1 /W:1
if ($LASTEXITCODE -ge 8) { throw '代码快照复制失败；先解决错误，不继续。' }
git -C $sourceRoot rev-parse HEAD | Set-Content -LiteralPath (Join-Path $trialRoot 'RELEASE_HEAD.txt')
git -C $sourceRoot status --short | Set-Content -LiteralPath (Join-Path $trialRoot 'RELEASE_WORKTREE.txt')
Set-Location -LiteralPath $trialRoot
```

人工对照工作区清单，确认新增业务文件、前端资源、`config/agent`、部署材料、脚本、`pyproject.toml`、`uv.lock`、`frontend/package-lock.json` 均已复制。上面的排除目录是运行数据、依赖和本地工具状态；如果其中实际存放了必要源代码，要审查后另行纳入快照。`local` 下的 GREAT 程序、模板、GNSS 输入、本地模型和秘密文件按第 4 节单独登记，不能把“代码快照”当作整机备份。检查快照中没有真实密钥或私人配置；忽略规则不能代替秘密检查。

仅在独立发布根中按已有锁文件准备新环境，避免改变原实例依赖。提前安装合适版本的 Python（项目支持 3.11/3.12）、uv、Node.js 与 npm；下面两步会安装依赖，须在管理员选定的发布目录执行：

```powershell
uv sync --locked
if ($LASTEXITCODE -ne 0) { throw '锁定 Python 依赖安装失败。' }
npm --prefix frontend ci
if ($LASTEXITCODE -ne 0) { throw '锁定前端依赖安装失败。' }
.\scripts\verify.ps1 -Report "output\trial-verify-$releaseStamp.json"
if ($LASTEXITCODE -ne 0) { throw '发布门槛失败，停止部署。' }
npm --prefix frontend run build
if ($LASTEXITCODE -ne 0) { throw '发布构建失败。' }
$releaseFiles = @('backend\src', 'frontend\src', 'frontend\dist', 'config\agent', 'deploy\gateway', 'deploy\windows', 'scripts', 'pyproject.toml', 'uv.lock', 'frontend\package-lock.json')
Get-ChildItem -LiteralPath $releaseFiles -File -Recurse |
    Where-Object { $_.FullName -notmatch '\\__pycache__\\' } |
    Get-FileHash -Algorithm SHA256 |
    Export-Csv -LiteralPath "output\trial-release-sha256-$releaseStamp.csv" -NoTypeInformation -Encoding utf8
```

`scripts/verify.ps1` 是锁定发布门槛，执行离线 Python/前端测试、类型检查、lint 和隔离构建；先通过它，再生成正式 `frontend/dist`。保留报告和关联日志，修改代码后重新运行，不能用只读部署预检替代。检查 `frontend/dist/index.html` 与它引用的资源真实存在，并将最终文件清单和 SHA256 连同报告归档。没有 `uv`/`npm` 或依赖时应先完成环境准备，不要跳过验证。

若管理员明确选择继续使用 `F:\GREAT_Agent` 本身，则设 `$trialRoot = 'F:\GREAT_Agent'`。在这个根运行 `npm --prefix frontend run build` 会改动原实例共用的 `frontend/dist`，必须先安排原实例维护窗口；需要原实例持续运行时，选择独立发布根。不要执行 `scripts/start.ps1`：它的构建与 `data/pids` 属于共享启动流程，`-Config` 不能将这些位置全部隔离。

## 4. 建立私有配置并核对路径

后续 Windows 命令均在选定的 `$trialRoot` 下执行；换终端时先恢复这个变量和工作目录。以下操作只创建试用目录和私有副本：

```powershell
Set-Location -LiteralPath $trialRoot
if (Test-Path -LiteralPath 'config\local.trial.toml') { throw '已有私有配置，请人工审阅，不覆盖。' }
Copy-Item -LiteralPath 'config\trial.example.toml' -Destination 'config\local.trial.toml'
New-Item -ItemType Directory -Force -Path 'data\trial\knowledge', 'runs\trial', 'deploy\local\bin', 'deploy\local\tls', 'deploy\local\logs\api', 'deploy\local\logs\worker', 'deploy\local\logs\tunnel', 'output' | Out-Null
Copy-Item -LiteralPath 'deploy\windows\frpc.example.toml' -Destination 'deploy\local\frpc.toml'
foreach ($serviceName in @('GreatAgentTrialApi', 'GreatAgentTrialWorker', 'GreatAgentTrialTunnel')) {
    Copy-Item -LiteralPath "deploy\windows\$serviceName.xml.example" -Destination "deploy\local\$serviceName.xml"
}
```

用本地编辑器逐项修改运行副本。独立发布根必须统一调整下列路径，不能只改 `project_root`：

- `config/local.trial.toml` 的 `project_root`、`database_path`、`run_root`、`knowledge.root`；后三者必须分别为该根下 `data/trial/great_agent.db`、`runs/trial`、`data/trial/knowledge`。
- 三份 WinSW XML 的 Python/frpc 可执行路径、`workingdirectory`、`--config` 绝对路径和 `logpath`。
- `frpc.toml` 的 `auth.tokenSource.file.path`、`transport.tls.trustedCaFile`，以及自己的 `serverAddr`/`transport.tls.serverName`。
- 保留 `host = '127.0.0.1'`、端口 8766 和两个并发值 1。不要使用 `0.0.0.0` 或原实例端口 8765。

GREAT 可执行文件、XML 模板、观测根、产品根及可选模型路径应指向管理员已经准备并许可试用的真实文件。它们可以是原电脑上的只读共享路径；不要把这些路径机械替换成新根后留下不存在的文件，也不要把正式数据库、正式知识目录复制为试用数据。记录程序/模板/模型版本及摘要。服务账号需要读取输入、模型和 `.venv` 的权限，只能在独立试用数据、日志和运行目录中写入。

TOML 不放模型密钥；不要把带 Token 的 URL 当作模型 `base_url`，不要把密码写进路径。首期建议保留样例中关闭的语义模型选项，只有实际本地模型文件和内存条件都核验后才开启；未提供模型资源时，不让服务启动时临时联网下载。

## 5. 只读部署预检与首次迁移

先静态检查，此命令默认不联网、不启动 API/Worker、不调用模型、不执行 GREAT，也不创建或迁移数据库：

```powershell
.\.venv\Scripts\python.exe scripts\deploy_preflight.py --config config\local.trial.toml --report "output\trial-preflight-static-$releaseStamp.json"
if ($LASTEXITCODE -ne 0) { throw '静态预检有失败项，先修复。' }
```

`--report` 仅接受尚不存在的 `.json` 文件，拒绝覆盖；重复运行应换时间戳。退出 0 表示没有失败项，仍须阅读警告；缺少交互终端中的模型密钥可能是警告，因为实际服务环境另行配置。报告的 `ready_for_public_access=false` 是有意保留的边界，不能改为上线凭据。

人工再次核对四个试用根、8766、无目录链接/重解析点、未复用正式数据，并确认该试用库尚未有 API/Worker 使用，再执行唯一的初始迁移命令：

```powershell
.\.venv\Scripts\python.exe -m great_agent.cli migrate --config config\local.trial.toml
if ($LASTEXITCODE -ne 0) { throw '试用库迁移失败，停止部署。' }
```

这一步会写入试用库，不是只读检查。禁止省略 `--config`、改用 `config/local.toml`，或对正式库做演练。以后升级先停止试用服务、完成第 11 节备份和恢复演练，再迁移。

## 6. 准备经过验证的 frp TLS 身份与 Token

浏览器 HTTPS 和 frp TLS 是两层独立证书：Caddy 负责浏览器公信证书；frpc 必须通过自己持有的 CA 公钥证书验证 frps 的服务器证书及名称。仅写 `transport.tls.enable=true` 不够，frp 官方说明默认 TLS 加密并不自动校验服务端证书。[frp TLS 文档](https://gofrp.org/en/docs/features/common/network/network-tls/)

为真实的 `relay.<自己的域名>` 签发带 **DNS SAN** 的服务端证书；它必须与 `frpc.toml` 的 `transport.tls.serverName` 一致。仅 CN 匹配不足。可在独立受保护的管理员 Linux/OpenSSL 环境执行以下示例；先将示例域名改成真实域名，CA 私钥使用交互口令保护：

```bash
umask 077
mkdir trial-pki
cd trial-pki
openssl genpkey -algorithm RSA -aes-256-cbc -pkeyopt rsa_keygen_bits:4096 -out relay-ca.key
openssl req -x509 -new -sha256 -days 1825 -key relay-ca.key -out relay-ca.crt -subj '/CN=GREAT Trial Relay CA' -addext 'basicConstraints=critical,CA:TRUE' -addext 'keyUsage=critical,keyCertSign,cRLSign'
openssl req -new -newkey rsa:3072 -nodes -keyout relay.key -out relay.csr -subj '/CN=relay.example.com'
cat > relay.ext <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:relay.example.com
EOF
openssl x509 -req -in relay.csr -CA relay-ca.crt -CAkey relay-ca.key -CAcreateserial -out relay.crt -days 90 -sha256 -extfile relay.ext
openssl verify -CAfile relay-ca.crt -verify_hostname relay.example.com relay.crt
openssl x509 -in relay.crt -noout -dates -ext subjectAltName
```

只向 Linux 网关安装 `relay.crt` 和 `relay.key`，路径为 `/etc/frp/tls/relay.crt`、`/etc/frp/tls/relay.key`；只向 Windows 分发 `relay-ca.crt`，路径为 `<试用根>\deploy\local\tls\relay-ca.crt`。通过已验证的 SSH/SFTP 或其他受控方式传输，另核对 SHA256 指纹。**CA 私钥 `relay-ca.key` 不分发到客户端或网关**，加密离线保管；服务端私钥 `relay.key` 只给网关 frp 服务账号读取，不发给试用者，不放进代码包。

在管理员受保护终端生成 32 个随机字节并编码为 64 个十六进制字符的 Token，例如 Linux `openssl rand -hex 32 > frps.token`。将完全相同的内容私密复制到 Windows `frpc.token`；文件可以有结尾换行，核验时按所用 frp 版本的读取规则处理。Token 不进命令行参数、聊天、截图或公共日志，不需要输出内容来证明一致。注意 frp 自身未必拒绝空 Token，因此必须在安装/启动前独立确认两个文件各含非空、格式正确且一致的值。

记录 CA 和服务器证书的签发/到期日、续签责任人和 Token 轮换步骤；提前至少两周更换 90 天叶证书，核验 SAN 和链后重启 frps/frpc 并重做外网验收。CA 轮换要先在客户端部署新信任，再切服务器证书；不能用关闭验证来临时恢复服务。

## 7. 准备 Linux 网关并先做语法验证

选择并记录 Caddy、frp 和 WinSW 的明确版本与发布资产 SHA256，不使用不受控的“最新版”下载脚本。本套 TOML 使用 frp `auth.tokenSource`，按官方相应版本支持情况准备**两端相同版本**；当前模板以 frp 0.72.0 接口为基准。Caddy 的 `basic_auth` 要求 2.8 及以上；Windows XML 以 WinSW 2.x 为基准。[Caddy 安装文档](https://caddyserver.com/docs/install)、[frp 官方发布](https://github.com/fatedier/frp/releases)、[WinSW 2.12.0 文档](https://github.com/winsw/winsw/tree/v2.12.0)

此处采用官方静态 Caddy 二进制，避免发行版安装包自动启动默认站点。把核验后的 Linux x64 `caddy`、`frps`，仓库的 gateway 模板以及 [Caddy 官方 systemd unit](https://github.com/caddyserver/dist/blob/master/init/caddy.service) 放到网关管理员专用暂存目录 `/tmp/trial-upload`；该目录应仅管理员可访问。取 unit 时记录其版本/提交，检查 `ExecStart`/`ExecReload` 与实际 `/usr/bin/caddy`、`/etc/caddy/Caddyfile` 一致。去掉官方 unit 的 `ExecStart` 中 `--environ`，避免将运行环境输出到日志。

下面假设网关是新机器，用户/目录尚未存在；已有同名资源时先核实归属，不覆盖其他服务。创建账号和文件不等于安装或启动服务：

```bash
sudo useradd --system --user-group --home-dir /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
sudo useradd --system --user-group --home-dir /etc/frp --no-create-home --shell /usr/sbin/nologin frp
sudo install -m 0755 /tmp/trial-upload/caddy /usr/bin/caddy
sudo install -m 0755 /tmp/trial-upload/frps /usr/local/bin/frps
sudo install -d -m 0750 -o root -g caddy /etc/caddy
sudo install -d -m 0750 -o root -g frp /etc/frp /etc/frp/tls
sudo install -m 0640 -o root -g caddy /tmp/trial-upload/Caddyfile.example /etc/caddy/Caddyfile
sudo install -m 0600 -o root -g root /tmp/trial-upload/caddy.env.example /etc/caddy/trial.env
sudo install -m 0640 -o root -g frp /tmp/trial-upload/frps.example.toml /etc/frp/frps.toml
sudo install -m 0640 -o root -g frp /tmp/trial-upload/relay.crt /etc/frp/tls/relay.crt
sudo install -m 0640 -o root -g frp /tmp/trial-upload/relay.key /etc/frp/tls/relay.key
sudo install -m 0640 -o root -g frp /tmp/trial-upload/frps.token /etc/frp/frps.token
```

在 Linux 本地编辑 `/etc/caddy/trial.env`：

```text
TRIAL_HOST='trial.example.com'
TRIAL_ADMIN_HASH='$2a$...由caddy生成的真实bcrypt摘要...'
TRIAL_INVITEE_01_HASH='$2a$...另一人的真实bcrypt摘要...'
```

先换真实域名；分别运行 `/usr/bin/caddy hash-password`，在交互提示中输入每个人不同的强密码，把输出摘要本地填入相应字段。单引号保留 bcrypt 中的 `$` 字符。以上省略号不是合法摘要；空摘要或占位值不得启动。新增受邀者时在私有 Caddyfile 中增加独立用户名与摘要，不共享管理员账号。`basic_auth` 应覆盖全部请求，不能给 `/api`、`/docs`、原 PDF 或 SSE 留免登录例外。[Caddy Basic Auth 文档](https://caddyserver.com/docs/caddyfile/directives/basic_auth)

检查 Caddyfile 中的 Origin 与站点都引用同一个 `TRIAL_HOST`：`POST/PUT/PATCH/DELETE` 等写请求必须具备且只具备 `Origin: https://<实际试用域名>`，缺失或异源都拒绝。命令行客户端也要带这个 Origin。保留 API 配置的 50 MiB 上传限制和代理的流式刷新、Range 头、断连传播；当前 Caddy 样例不另设请求体上限，不要把上传能力误认为网关限流。不要为了上传/SSE 绕过认证。Caddy 的流式代理相关行为应结合实际版本验证。[反向代理文档](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

**先完成以下验证，全部成功后再安装任何 systemd/Windows 服务：**

```bash
sudo -u frp /usr/local/bin/frps verify -c /etc/frp/frps.toml
sudo bash <<'BASH'
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import re
token = Path('/etc/frp/frps.token').read_text(encoding='ascii').strip()
if not re.fullmatch(r'[0-9a-fA-F]{64}', token):
    raise SystemExit('Invalid trial token file')
PY
set -a
. /etc/caddy/trial.env
set +a
/usr/bin/caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
BASH
```

Token 校验只报告通过/失败，不能 `cat` 私密文件。frp 的 `verify` 只验证配置，不能替代真实证书信任、连通和访问控制验收；保持默认严格模式。[frp 配置验证](https://gofrp.org/en/docs/features/common/configure/)

## 8. Windows 单进程服务准备与本机验收

把验证过的 Windows x64 frpc 放入 `<试用根>\deploy\local\bin\frpc.exe`。将同一份 WinSW 2.x 二进制各复制一份并重命名为 `GreatAgentTrialApi.exe`、`GreatAgentTrialWorker.exe`、`GreatAgentTrialTunnel.exe`，与第 4 节同名 XML 放在 `deploy/local`，不要重命名为已有服务名。WinSW 包装的三个命令都应保持前台运行，不能在 XML 中再调用 `Start-Process`、`start.ps1` 或后台化的批处理。[WinSW 使用方式](https://github.com/winsw/winsw/blob/v2.12.0/README.md)

创建专用低权限本地服务账号（示例名 `GreatTrialSvc`），为其授予“作为服务登录”权限。它需要读取代码、虚拟环境、程序、模板、管理员批准的 GNSS 输入和模型；修改权限仅授予自己的试用数据库/知识、运行和日志目录。不要给该账号访问正式业务数据库的权限。外部映射盘通常不在服务会话中出现，优先使用本地绝对路径；Windows F 盘也必须在未登录状态可用。

交互用户的 `$env:GREAT_AGENT_MODEL_API_KEY`、用户级环境变量和已登录浏览器状态不会自动成为服务环境。三份样例不携带秘密，也不预设生产服务账号。可以在**仅本机私有运行 XML** 中用 WinSW `<env>` 分别为 API/Worker 注入 `GREAT_AGENT_MODEL_API_KEY`（本地编辑并正确 XML 转义）；Tunnel 从受保护 Token 文件读取，无需模型密钥。不要在模板内写值、全机设置公共密钥、把口令作为 `sc.exe` 命令行参数，或打印环境用于排障。WinSW 支持 `<env>` 和明确工作目录；实际身份与环境必须重新验证。[WinSW XML 文档](https://github.com/winsw/winsw/blob/v2.12.0/doc/xmlConfigFile.md)

在录入任何秘密前限制运行目录 ACL，例如管理员 PowerShell：

```powershell
$trialAccount = "$env:COMPUTERNAME\GreatTrialSvc"
$privateRuntime = Join-Path $trialRoot 'deploy\local'
icacls $privateRuntime /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "${trialAccount}:(OI)(CI)RX"
if ($LASTEXITCODE -ne 0) { throw '私密目录 ACL 设置失败。' }
icacls (Join-Path $privateRuntime 'logs') /grant:r "${trialAccount}:(OI)(CI)M"
if ($LASTEXITCODE -ne 0) { throw '日志 ACL 设置失败。' }
```

对配置、试用数据库/知识、runs 目录分别核验读写 ACL，并限制其他普通账号访问；如果仍在原项目根中，不能递归修改整个 `F:\GREAT_Agent` 的 ACL。先验证专用账号能使用 `.venv\Scripts\python.exe`，无需交互登录即可读到批准的资源。不要通过提升为 LocalSystem 来掩盖路径/环境错误。

用该账号可读取的实际文件验证 Windows 配置；不要打印 XML 或 Token 正文：

```powershell
.\.venv\Scripts\python.exe -c "import pathlib,tomllib,xml.etree.ElementTree as ET; tomllib.loads(pathlib.Path('config/local.trial.toml').read_text(encoding='utf-8')); tomllib.loads(pathlib.Path('deploy/local/frpc.toml').read_text(encoding='utf-8')); [ET.parse(p) for p in pathlib.Path('deploy/local').glob('GreatAgentTrial*.xml')]; print('TOML/XML parsed')"
if ($LASTEXITCODE -ne 0) { throw '运行文件解析失败。' }
.\.venv\Scripts\python.exe -c "from pathlib import Path; import re; token=Path('deploy/local/frpc.token').read_text(encoding='ascii').strip(); raise SystemExit(0 if re.fullmatch(r'[0-9a-fA-F]{64}',token) else 1)"
if ($LASTEXITCODE -ne 0) { throw '试用 Token 文件格式错误。' }
.\deploy\local\bin\frpc.exe verify -c deploy\local\frpc.toml
if ($LASTEXITCODE -ne 0) { throw 'frpc 配置验证失败。' }
```

确认 Linux 第 7 节与 Windows 上述检查均通过，再在管理员终端安装三个新服务（此时仍是 Manual，不启动）：

```powershell
.\deploy\local\GreatAgentTrialApi.exe install
if ($LASTEXITCODE -ne 0) { throw 'API 服务安装失败。' }
.\deploy\local\GreatAgentTrialWorker.exe install
if ($LASTEXITCODE -ne 0) { throw 'Worker 服务安装失败。' }
.\deploy\local\GreatAgentTrialTunnel.exe install
if ($LASTEXITCODE -ne 0) { throw 'Tunnel 服务安装失败。' }
```

打开 `services.msc`，仅为这三个新服务设置“登录”账号 `GreatTrialSvc`，在系统受保护界面录入服务账号密码。核对每个服务的映像路径、账号与 Manual 启动方式，禁止在默认身份未核验时先启动。先启动 API 和 Worker：

```powershell
Start-Service -Name GreatAgentTrialApi
Start-Service -Name GreatAgentTrialWorker
.\.venv\Scripts\python.exe scripts\deploy_preflight.py --config config\local.trial.toml --probe --report "output\trial-preflight-live-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
if ($LASTEXITCODE -ne 0) { throw '本机存活/就绪检查失败，保持外网入口关闭。' }
```

若心跳尚未建立，观察对应私有日志后用新报告文件重试；不能降级为 `require_worker=false` 来获得通过。`--probe` 只请求配置的 `127.0.0.1:8766` 上 `/api/v1/health`、`/api/v1/readiness`、`/api/v1/diagnostics`，不使用代理、不跟随重定向，也不测试公网域名。必须有 schema、近期 Worker 心跳与知识执行器就绪；health 200 单独不足。当前终端缺少模型环境变量的警告不能证明服务密钥是否有效，模型调用要在第 10 节以真实试用页面单独验收。

检查 8766 监听仍为 `127.0.0.1`，原有 8765 实例状态不变。此时不启动 Tunnel，先完成 Linux 的安装。

## 9. 安装网关服务、DNS 和隧道

第 7/8 节全部语法验证通过后，在 Linux 安装先前审阅的 unit 和 Caddy 环境文件 drop-in：

```bash
sudo install -m 0644 /tmp/trial-upload/caddy.service /etc/systemd/system/caddy.service
sudo install -m 0644 /tmp/trial-upload/frps.service /etc/systemd/system/frps.service
sudo install -d -m 0755 /etc/systemd/system/caddy.service.d
sudo tee /etc/systemd/system/caddy.service.d/trial-env.conf > /dev/null <<'EOF'
[Service]
EnvironmentFile=/etc/caddy/trial.env
EOF
sudo systemd-analyze verify /etc/systemd/system/caddy.service /etc/systemd/system/frps.service
sudo systemctl daemon-reload
```

确认 Caddy unit 已移除 `--environ`、frps unit 使用低权限 frp 身份及正确 Token 检查，且日志中不会输出 Token。给真实 `trial` 和 `relay` 子域名分别新增指向网关公网 IPv4 的 A 记录；只有 IPv6 确实可用时才加 AAAA，避免证书验证走向错误地址。关闭未验证的 CDN/代理功能；frp 7000 必须能直接 TCP 连通。

此时按第 1 节放行 80/443/7000，继续保持 18765 关闭。Caddy 需要正确 DNS、可达的 80/443 和可持久写入的证书存储才能自动签发与续期浏览器证书；不要把它的数据目录放在临时盘。[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)

```bash
sudo systemctl enable --now frps
sudo systemctl enable --now caddy
sudo systemctl status frps caddy --no-pager
sudo ss -lntp
```

在 Windows 启动 `GreatAgentTrialTunnel`，核对其使用专用身份和新根下的 frpc/CA/Token 文件；在 Linux 看到新建监听只能是 `127.0.0.1:18765`。如果是 `0.0.0.0:18765` 或 `[::]:18765`，立即停止试用 Tunnel/frps，修正 `proxyBindAddr`，不要依赖“暂时没人知道端口”。

```powershell
Start-Service -Name GreatAgentTrialTunnel
Get-Service -Name GreatAgentTrialApi, GreatAgentTrialWorker, GreatAgentTrialTunnel
```

从 Linux 本机直接请求 18765 仅可用于可信管理员排障，不能作为试用入口；公网必须经过 Caddy。通过外网检查实际域名的浏览器证书链/名称有效、HTTP 自动跳到 HTTPS、未认证请求为 401。SSE、上传和模型调用仍需继续验收。

## 10. 外网人工验收后才发邀请

使用手机移动网络或另一条真实外网，按下列清单记录时间、浏览器/网络、发布根、代码/构建摘要、软件版本及结果。不要记录密码、Authorization 头、模型密钥或完整诊断响应。所有项通过前保持“待验收”，不能把静态报告、health 或语法解析成功称为已可对外使用。

| 项目 | 操作与通过条件 |
| --- | --- |
| 全路径认证 | 新隐私窗口，未登录访问 `/`、真实 JS 资源、`/docs`、`/openapi.json`、health/readiness/diagnostics、真实 PDF 原件和 SSE 地址，均不能取得正文；HTTPS 应为 401。错误密码失败，两个独立账号可登录。账号停用后新请求失败。 |
| 同源写保护 | 已认证但无 Origin 的写请求、异源 Origin 的写请求均为 403；本站网页的正常创建/上传/取消请求可用。认证失败不能穿透。 |
| 不可旁路 | 外网连接网关 18765、Windows 8766 均失败；网关上只在 loopback 监听 18765。无远程免认证 health、文档或下载例外。 |
| SSE | 登录后创建试用实验，观察任务/实验事件流，Network 可见 `text/event-stream`，新事件及时到达，持续观察超过常见代理空闲超时；断开网络再恢复能重新建立流，页面不会永远卡在等待。不要只看一帧或把本机测试当公网结果。 |
| 上传上限 | 使用管理员批准且满足页数限制的有效 PDF，验证正常小文件、接近并达到 50 MiB（52,428,800 字节）的文件；超过上限 1 字节应由 API 明确拒绝（413），不会成为可用文档，也不遗留失控的分片文件。测试须穿过 HTTPS 网关，不能只直连本机。 |
| PDF 原页/Range | 文档解析完成后在页面打开真实 PDF，前后翻页及证据定位正确；用真实原件 URL 发 `Range: bytes=0-1023`，应返回 206 和正确 Content-Range。未认证同样请求为 401。 |
| 模型与取消 | 以实际服务身份经页面调用模型并返回有效回复。分别测试取消等待、取消知识问答/翻译、浏览器断连；页面及时恢复可操作，diagnostics 无永久占槽、没有重复落库或陈旧回复覆盖新请求。确认服务账号读到了正确密钥及其允许的出站网络。 |
| GREAT 人工授权 | 只选管理员批准的小数据例，先生成 XML 预览，逐项核对 XML 全文、实际程序路径/SHA256、参数、输入和输出；在明确勾选并点击“确认并开始解算”或当前预览内单独明确确认前，不能有实际解算进程。模型回复不构成授权。首次执行成功后检查日志、FLT、分析与图像，并测试停止流程；重试仍需新预览和明确确认。 |
| 重启与恢复 | 无任务时分别重启三个试用服务和网关；再安排一次管理员已明确授权的小任务恢复演练。验证旧进程身份/恢复占槽可解释、结果可读取、Worker 心跳恢复，不自动重跑任务。电脑未交互登录也能启动、读输入并保存试用结果。不得重启原有 8765 服务。 |
| 备份恢复 | 按第 11 节停止试用写入、备份、verify，并在新目录恢复验证，原试用根和原 8765 数据均未覆盖。 |

可用以下 Bash 只验证网关写保护，不创建业务资源；先把 `TRIAL_HOST` 换成真实域名，`curl --user` 会交互询问密码，不在命令里附密码：

```bash
TRIAL_HOST='trial.example.com'
curl --user invitee_01 -i -X POST "https://${TRIAL_HOST}/api/v1/health"
curl --user invitee_01 -i -X POST -H 'Origin: https://wrong.example.com' "https://${TRIAL_HOST}/api/v1/health"
curl --user invitee_01 -i -X POST -H "Origin: https://${TRIAL_HOST}" "https://${TRIAL_HOST}/api/v1/health"
```

前两次期望 403；第三次通过网关 Origin 检查后由只支持 GET 的 health 路由返回 405，而不是创建记录。PDF 原件当前路由为 `/api/v1/knowledge/documents/<实际文档UUID>/original`；SSE 与引用 ID 从该试用页面真实请求中取得，不使用编造 ID。

功能验收通过后，在管理员 PowerShell 中只将三个试用服务设为延迟自动启动：

```powershell
foreach ($serviceName in @('GreatAgentTrialApi', 'GreatAgentTrialWorker', 'GreatAgentTrialTunnel')) {
    & sc.exe config $serviceName start= delayed-auto
    if ($LASTEXITCODE -ne 0) { throw "试用服务启动方式设置失败：$serviceName" }
}
```

在 `services.msc` 中逐一确认启动类型为“自动（延迟启动）”、登录身份为专用账号，并在“恢复”页核对失败后重启设置；不要改同机其他服务。若以后重新安装服务，样例 XML 的 Manual 会再次生效，需要重新执行此设置。最后在已经安排好的整机维护窗口验证 Windows 开机后**无人交互登录**也能恢复三个试用服务、本机 readiness、隧道和 HTTPS。整机重启会中断原有 8765，不能为试用演练擅自重启电脑；没有该维护窗口时保留此项待验收。

全套验收完成后仅把真实 HTTPS 地址和各自账号私密交给受邀者，并明确共用数据、文件大小、并发 1、电脑维护时不可用、输入数据范围及管理员联系方式。Basic Auth 浏览器可能缓存凭证，不能把关闭标签页当作可靠退出；公用电脑不要使用。

## 11. 备份、升级与恢复

正常备份顺序：暂停新请求、让已授权任务完成或明确停止、先停试用 Tunnel，再停试用 Worker 和 API。不能仅以 WinSW 显示 Stopped 判断 GREAT 子进程已经安全退出；按任务恢复信息核对程序、参数、创建时间和输出位置，身份不明时停止操作并人工调查。不要按进程名杀死所有 Python/GREAT。

```powershell
Set-Location -LiteralPath $trialRoot
Stop-Service -Name GreatAgentTrialTunnel
Stop-Service -Name GreatAgentTrialWorker
Stop-Service -Name GreatAgentTrialApi
Get-Service -Name GreatAgentTrialApi, GreatAgentTrialWorker, GreatAgentTrialTunnel
$backupStamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = "F:\GREAT_Trial_Backups\$backupStamp"
.\scripts\backup.ps1 -Operation backup -Config config\local.trial.toml -Destination $backupRoot
if ($LASTEXITCODE -ne 0) { throw '离线备份失败；不要迁移、删除数据或强制绕过。' }
.\scripts\backup.ps1 -Operation verify -Backup $backupRoot
if ($LASTEXITCODE -ne 0) { throw '备份完整性验证失败。' }
```

选择磁盘空间足够、访问受控的独立备份目录，可用其他实际备份盘替换示例。备份包含数据库、全部 `runs/trial`、私有配置、知识原 PDF/缓存；切片、翻译等数据库内容随库保留，向量索引按既有策略重建。备份会保留私有配置，必须加密保存并限制访问，最好另存离线/异机副本。

**同根限制：**现有 `backup_service` 只要发现同 `project_root` 的任何 API/Worker 就拒绝备份，也会在无法安全检查进程时拒绝。若试用仍在 `F:\GREAT_Agent`，原有 8765 可能导致备份在停止三个试用服务后仍失败。这是需要独立发布根的原因，不代表可以停止原服务。不要调用 `scripts/stop.ps1`（固定读取共享 `data/pids`）、修改正式配置、伪造 PID、关闭检查或直接复制活跃 SQLite/WAL。先将试用按本手册迁到完整独立发布根；如原根仍需维护，应另行安排，不在本手册中扩大停止范围。

数据备份**不包含**应用代码、`frontend/dist`、虚拟环境、GREAT 程序、GNSS 输入/产品、本地模型、WinSW/frp/Caddy 二进制、TLS 私钥或系统服务配置。分别保存第 3 节当前工作区代码快照、锁文件与验证/构建摘要；登记外部资源版本/路径/校验值；私密备份运行配置、Token 与证书恢复材料。单独保护离线 CA 私钥，不与客户端安装包一起分发。

恢复演练使用全新、非现有业务目录，继续保持试用服务停止：

```powershell
$recoveryRoot = "F:\GREAT_Trial_Recovered\$backupStamp"
.\scripts\backup.ps1 -Operation restore -Backup $backupRoot -Destination $recoveryRoot
if ($LASTEXITCODE -ne 0) { throw '恢复失败；保留材料，人工检查。' }
```

工具不会覆盖已有运行目录，会生成恢复配置并重定位历史结果路径。先用对应代码/依赖版本检查恢复报告、数据库、知识原件、人工核对缓存与历史结果；不要直接把恢复配置当正式启动配置。激活前须人工审查新 `project_root`、试用固定子路径、端口、GREAT/GNSS/model 路径以及三份服务 XML/隧道文件，重新完成预检和外网验收。旧执行预览不代替新任务的人工授权。

升级先保留可恢复的旧代码/构建和已验证数据备份，在新的完整发布快照上运行 `scripts/verify.ps1`，再构建；停止试用服务后针对独立配置迁移。启动顺序为 API、Worker、本机 probe、Tunnel、外网验收。若失败，保持入口关闭，回到旧代码/构建和新目录恢复的数据，不把旧版本强行接到已升级数据库。

## 12. 下线与日常维护

临时停止邀请时先停止 `GreatAgentTrialTunnel`；计划维护时按第 11 节停试用写入、备份。Linux 可仅停止本站 Caddy/frps；在共用网关上先核对服务归属，不能停止其他站点。永久下线后撤销账号、轮换/销毁试用 Token、移除对应 DNS/防火墙入口、关闭试用服务的自动启动，再按保留策略处理数据和云资源。不要删除原有 8765 的数据库、运行文件或 PID。

日常检查 Windows 电源/联网/磁盘空间、Worker 心跳、知识执行器、隧道重连和恢复占槽；检查网关日志、证书到期、域名/服务器续费、流量账单与备份可恢复性。日志访问要受控；不要开启请求体、Authorization 或完整环境转储排障。改密码后重新生成摘要并校验 Caddy 配置，再 reload；改域名要同时调整 DNS、`TRIAL_HOST`/Origin 和需要变更的 relay SAN/serverName，重新验收。

本文官方来源于 2026-11-16 核验。云厂商接入要求、软件版本与官方模板可能变化，实际采购和安装时应对照上述官方页面复核。当前交付不包含公网 TLS、真实服务账号、模型调用、GREAT 执行或异网端到端验收结果。
