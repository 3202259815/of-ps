# 用 Docker Compose 部署 of-ps

端口全部由 `.env` 控制，不再写死 25565/25566。

## 0. 前置

- 服务器：Linux，装好 `docker` 与 `docker compose`（v2）
- 放行端口：`HTTP_PORT`（默认 25565，PC 登录器 + 手机 SDK 都连它）、`GATE_PORT`（默认 25566，游戏数据）
- 内存：1.5 GB 起（100 人同时在线实测 ~250 MB）

### 0.1 国内服务器：先解决镜像拉取（必看）

如果 `docker compose build` 报这类错，说明连不上 Docker Hub：

```
failed to resolve source metadata for docker.io/docker/dockerfile:1: dial tcp ...: i/o timeout
failed to do request: Head "https://registry-1.docker.io/v2/...": i/o timeout
```

两条路，任选：

**A. 配镜像加速（推荐，一次配好）**

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://mirror.ccs.tencentyun.com"
  ]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
docker pull python:3.12-slim    # 验证能拉
```

**B. 只给本项目换基础镜像源**

在 `.env` 里加一行（镜像站任选，能拉就行）：

```
PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim
```

> `Dockerfile` 里已经**没有** `# syntax=docker/dockerfile:1` 这行，所以不会再额外去拉 BuildKit 前端镜像；apt 与 pip 也都换成了国内源。


## 1. 上传代码

```bash
git clone https://github.com/3202259815/of-ps.git ofps
cd ofps
```

## 2. 准备配置表（必须，仓库里没有）

`resources/data` 是 118 个 JSON 配置表，被 `.gitignore` 排除，必须从本地拷上去：

```bash
# 本机 Windows（PowerShell / scp 都行）
scp -r "D:\OpenSpace_Extract_unity\新的\resources\data" user@服务器:/path/ofps/resources/
```

目录结构应为 `ofps/resources/data/Item.json` 等 118 个文件。

## 3. 配端口

```bash
cp .env.example .env
vi .env
```

关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `HTTP_PORT` | 25565 | dispatch / 登录器 / 手机 SDK |
| `GATE_PORT` | 25566 | 游戏 TCP 网关 |
| `PUBLIC_IP` | 0.0.0.0 | `0.0.0.0` = 用客户端访问 dispatch 的 Host 自动回填；填公网 IP/域名 = 强制下发 |
| `RES_VERSION` | `2026-10-21-14-05-50_2026-11-25-19-54-41` | 必须两段式，第二段等于客户端资源版本 |
| `ACCOUNT_STRICT_TOKEN` | false | true 时 gate 要求 token 来自 `/account/login` |

> `PUBLIC_IP` 留 `0.0.0.0` 时，客户端拿到的 gate 地址就是它访问 dispatch 用的那个 Host。用公网 IP 直连最省事；如果前面套了域名/反代，就显式填公网 IP。

## 4. 启动

```bash
docker compose up -d --build
docker compose logs -f ofps
```

看到这两行就正常：

```
HTTP server started at http://0.0.0.0:25565
Game server started on port 25566
```

自检：

```bash
curl -s -X POST http://127.0.0.1:25565/dispatch/region_info \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data "version=2026-10-21-14-05-50&version2=76616492f01f64f45b94abc671d4fd76&accountType=9999&os=0&lastloginsdkuid=1"
```

应返回 `gate_tcp_ip` / `gate_tcp_port` / `currentVersion` 的 JSON。

## 5. 重启 / 升级 / 备份

```bash
docker compose restart ofps          # 重启
git pull && docker compose up -d --build   # 升级
docker compose down                  # 停止（存档在 ./data/player.db）
```

存档 = 宿主机 `./data/player.db`（每 2 秒自动提交，规则见 `docs/数据库与存档优化方案.md`）。
备份直接 `cp data/player.db data/player.db.$(date +%F)`；有热备份接口 `db.backup_to()`。

## 6. 玩家怎么连

- **PC**：`OpenSpaceLauncher.exe` → 「服务器」填 `服务器IP:25565` → 账号/密码（没有会自动注册）→ 登录并启动游戏。
  登录器会在玩家本机起反代（18881/443）、首次运行提权装 CA + 写 hosts，把客户端的官网请求导到这台服务器。
- **手机**：游戏「设置」里填 `http://服务器IP:25565`（注意是 HTTP_PORT，不是 GATE_PORT），gate 端口由 `region_info` 自动下发。

## 7. 常见问题

**Q: 客户端报「服务器连接失败」**
先看 `docker compose logs` 有没有 dispatch 请求；再确认 `25566` 也在防火墙放行（客户端要直连它）。

**Q: 手机报 connect 超时**
手机填的端口错了：要填 `HTTP_PORT`（25565），不是 `GATE_PORT`（25566）。

**Q: 进游戏后角色数据是新的**
`DB_PATH` 指到了空库。把旧 `player.db` 放到 `./data/` 下再 `docker compose restart`。

**Q: 改了 `.env` 不生效**
`docker compose up -d`（会重建容器），不是 `restart`。

**Q: 想做多频道 / 多场景**
`server/scene_data.py` 的 `get_scene_id()` 现在写死 `9999`，属于已知限制。
