# 豆瓣自动转存 (DBAuto)

基于豆瓣移动端 API 的影视资源自动转存工具，通过 PanSou 搜索夸克网盘资源，调用 QAS 自动转存到网盘。

## 功能特性

- 🎬 **豆瓣榜单抓取**：热门电影、最新电影、豆瓣高分、冷门佳片，涵盖 30 个子榜单
- 📺 **电视剧/综艺**：热门剧集 7 个分类 + 热门综艺 3 个分类
- 🔍 **资源搜索**：集成 PanSou 搜索，一键转存到指定目录，支持链接有效性验证
- ⏰ **定时调度**：支持每日/每周/每月定时转存、失效检测、目录清理
- 📊 **仪表盘**：今日转存量、上次执行状态、近 7 天统计、下次调度时间一目了然
- 🔐 **安全认证**：PBKDF2 密码哈希、Token 登录保护、登录频率限制
- 🎨 **Apple 风格 UI**：深色/浅色主题切换、毛玻璃卡片、iOS 风格组件
- 🐳 **Docker 部署**：容器化运行，数据目录绑定持久化
- ⚡ **高性能**：HTTP 连接池、搜索并发、TTLCache、gzip 压缩等多项优化

## 项目结构

```
main.py                      # 入口文件
app_modules/                 # 后端 Python 模块
├── main.py                  # 启动逻辑（start() 函数）
├── config.py                # 配置管理（ConfigManager 单例，支持环境变量）
├── auth.py                  # 认证模块（PBKDF2 密码哈希、登录频率限制）
├── utils.py                 # HTTP 连接池、TTLCache、原子写、SSE 广播
├── validator.py             # 输入验证（字符串、URL、端口、Cron、时间等）
├── api_client.py            # API 客户端封装（PanSouClient、QASClient、OpenListClient）
├── storage.py               # SQLite 存储（历史记录、执行历史，自动迁移 JSON）
├── douban.py                # 豆瓣榜单（带 1 小时缓存）
├── transfer.py              # 转存执行（搜索并发、失效检测并发、模糊匹配优化）
├── scheduler.py             # 定时调度（croniter 库、事件驱动配置更新）
├── routes_base.py           # 基础路由处理类
├── routes_static.py         # 静态文件路由（gzip 压缩、健康检查）
├── routes_auth.py           # 认证相关路由（输入验证）
├── routes_transfer.py       # 转存相关路由（输入验证）
├── routes_history.py        # 历史记录路由（输入验证）
├── routes_config.py         # 配置 & 调度路由（输入验证）
├── routes.py                # 路由组合（Mixin 多继承）
└── server.py                # ThreadedHTTPServer
static/                      # 前端
├── index_new.html           # 主页面（单文件内联 CSS + JS）
└── login_new.html           # 登录页
tests/                       # 单元测试
├── test_validator.py        # 验证器测试
├── test_utils.py            # TTLCache 测试
└── test_transfer.py         # 历史匹配测试
```

## 依赖服务

| 服务 | 用途 | 端口 |
|------|------|------|
| [PanSou](https://github.com/fish2018/pansou) | 网盘资源搜索 | 8080 |
| [QAS (夸克自动转存)](https://github.com/Cp0204/quark-auto-save) | 转存任务执行 | 5005 |
| [OpenList](https://github.com/openlistteam/openlist) | 网盘文件管理 | 5244 |

以上服务通过设置页面（⚙️）或环境变量配置地址和 Token。

## 快速开始

### Docker Compose 部署（推荐）

```bash
# 创建数据目录
mkdir -p ./data

# 构建并启动
docker-compose up -d --build

# 查看状态
docker-compose ps

# 查看日志
docker-compose logs -f
```

### Docker 命令部署

```bash
docker run -d \
  --name douban-transfer \
  --restart unless-stopped \
  -p 3001:3001 \
  -v /opt/douban-history:/data/douban-history \
  -e DATA_DIR=/data/douban-history \
  -e PORT=3001 \
  -e TZ=Asia/Shanghai \
  -e PANSOU=http://192.168.1.1:8080 \
  -e QAS=http://192.168.1.1:5005 \
  -e QAS_TOKEN=your_token \
  -e AUTH_USER=root \
  -e AUTH_PASS=your_password \
  your-image-name
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATA_DIR` | `/data/douban-history` | 数据存储目录 |
| `PORT` | `3001` | 服务端口 |
| `TZ` | `Asia/Shanghai` | 时区设置 |
| `PANSOU` | `http://192.168.1.1:8080` | PanSou 搜索服务地址 |
| `QAS` | `http://192.168.1.1:5005` | QAS 转存服务地址 |
| `QAS_TOKEN` | — | QAS API Token |
| `OPENLIST_URL` | `http://192.168.1.1:5244` | OpenList 服务地址 |
| `OPENLIST_TOKEN` | — | OpenList API Token |
| `OPENLIST_BASE_PATH` | — | 转存目标基础路径 |
| `AUTH_USER` | `root` | 登录用户名 |
| `AUTH_PASS` | — | 登录密码 |

### 数据目录

`/data/douban-history/` 持久化存储以下文件：

| 文件 | 说明 |
|------|------|
| `app.db` | SQLite 数据库（历史记录、执行历史） |
| `config.json` | 系统配置（API 地址、Token） |
| `settings.json` | 定时任务设置 |

> **注意**：旧版的 `transfer_history.json` 和 `exec_history.json` 会自动迁移到 SQLite。

## API 接口

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 登录，返回 Token |

### 榜单 & 转存

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/categories` | 获取榜单分类 |
| POST | `/api/transfer` | 启动批量转存 |
| POST | `/api/stop` | 停止当前转存 |
| POST | `/api/transfer_one` | 单条转存（搜索用） |
| GET | `/api/status` | 转存状态和进度 |

### 定时任务

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/schedule` | 获取定时设置 |
| POST | `/api/schedule` | 保存定时设置 |
| GET | `/api/check_expired` | 检测失效链接 |
| GET | `/api/dir_cleanup` | 执行目录清理 |

### 历史记录

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/exec_history` | 执行历史列表 |
| GET | `/api/history/manage` | 转存历史管理 |
| GET | `/api/history/export` | 导出历史 JSON |

### 配置 & 搜索

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 获取系统配置 |
| POST | `/api/config` | 保存系统配置 |
| GET | `/api/search` | PanSou 资源搜索 |
| GET | `/health` | 健康检查 |

---

> ⚠️ 本项目仅供学习研究使用，请遵守相关服务的使用条款。