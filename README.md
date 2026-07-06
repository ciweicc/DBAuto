# 豆瓣自动转存 (DBAuto)

基于豆瓣移动端 API 的影视资源自动转存工具，通过 PanSou 搜索夸克网盘资源，调用 QAS 自动转存到网盘。

## 功能特性

- 🎬 **豆瓣榜单抓取**：热门电影、最新电影、豆瓣高分、冷门佳片，电视剧 7 个分类 + 综艺 3 个分类
- 🔍 **资源搜索**：集成 PanSou 搜索，一键转存到指定目录，支持链接有效性验证
- ⏰ **定时调度**：支持每日定时 / Cron 表达式，自动转存与失效检测
- 📊 **仪表盘**：今日转存量、近 7 天统计、下次调度时间一目了然
- 🗑️ **历史管理**：转存历史查看 / 删除 / 导出，执行记录一键清除
- 🔐 **安全认证**：PBKDF2 密码哈希、Token 登录保护、登录频率限制、敏感字段加密存储
- 🎨 **Apple 风格 UI**：深色 / 浅色主题切换、毛玻璃卡片、响应式布局
- 🐳 **Docker 部署**：容器化运行，数据目录绑定持久化

## 依赖服务

| 服务 | 用途 | 默认端口 |
|------|------|----------|
| [PanSou](https://github.com/fish2018/pansou) | 网盘资源搜索 | 8080 |
| [QAS (夸克自动转存)](https://github.com/Cp0204/quark-auto-save) | 转存任务执行 | 5005 |

以上服务通过设置页面（⚙️）或环境变量配置地址和 Token。

## 快速开始

### Docker 部署

```bash
docker run -d \
  --name douban-transfer \
  --restart unless-stopped \
  -p 3001:3001 \
  -v /opt/douban-history:/data/douban-history \
  -e PANSOU=http://192.168.1.1:8080 \
  -e QAS=http://192.168.1.1:5005 \
  -e QAS_TOKEN=your_token \
  -e AUTH_USER=root \
  -e AUTH_PASS=your_password \
  ghcr.io/ciweicc/dbauto:latest
```

> 也可使用 `docker-compose up -d`，详见 [docker-compose.yml](docker-compose.yml)。

### 本地运行

```bash
pip install -r requirements.txt
python main.py
```

服务将在 `http://localhost:3001` 启动。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `3001` | 服务端口 |
| `DATA_DIR` | `/data/douban-history` | 数据持久化目录 |
| `PANSOU` | — | PanSou 搜索服务地址 |
| `QAS` | — | QAS 转存服务地址 |
| `QAS_TOKEN` | — | QAS API Token |
| `AUTH_USER` | `root` | 登录用户名 |
| `AUTH_PASS` | — | 登录密码 |
| `TZ` | `Asia/Shanghai` | 时区 |

### 数据目录

`DATA_DIR` 持久化存储以下文件：

| 文件 | 说明 |
|------|------|
| `config.json` | 系统配置（敏感字段加密存储） |
| `settings.json` | 定时任务设置 |
| `app.db` | SQLite 数据库（转存历史、执行历史） |
| `.salt` | 加密密钥盐文件 |

> 首次启动时，如检测到旧版 JSON 历史文件（`transfer_history.json`、`exec_history.json`）会自动迁移到 SQLite。

## API 接口

所有 API 响应均为 JSON 格式，需通过 Header `X-Auth-Token` 或 Query 参数 `?token=` 携带 Token 认证（登录接口除外）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 登录，返回 Token |
| GET | `/api/status` | 检查认证状态 |
| GET | `/api/categories` | 获取榜单分类 |
| POST | `/api/transfer` | 启动批量转存 |
| POST | `/api/stop` | 停止当前转存 |
| GET | `/api/transfer/status` | 转存状态和进度 |
| POST | `/api/transfer_one` | 单条转存（搜索或直链） |
| GET | `/api/search` | PanSou 资源搜索 |
| GET | `/api/schedule` | 获取定时设置 |
| POST | `/api/schedule` | 保存 / 切换 / 立即执行 |
| GET | `/api/check_expired` | 检测失效链接 |
| GET | `/api/fix_expired` | 启动失效链接自动修复 |
| GET | `/api/dashboard/stats` | 仪表盘统计数据 |
| GET | `/api/history` | 转存历史列表 |
| GET/POST | `/api/history/manage` | 历史管理（查看 / 删除 / 清空 / 添加 / 更新） |
| GET | `/api/history/export` | 导出历史为文本文件 |
| GET | `/api/exec_history` | 执行历史列表（分页） |
| POST | `/api/exec_history/manage` | 执行历史管理（清除） |
| GET | `/api/config` | 获取系统配置（敏感字段掩码） |
| POST | `/api/config` | 保存系统配置 |
| GET | `/api/sse` | SSE 实时推送（日志 / 进度 / 状态变更） |

详细接口文档参见 [CODE_WIKI.md](docs/CODE_WIKI.md)。

---

> ⚠️ 本项目仅供学习研究使用，请遵守相关服务的使用条款。
