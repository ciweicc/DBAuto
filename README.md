# 豆瓣自动转存 (DBAuto)

> 基于豆瓣榜单与 TMDB 的影视资源自动转存工具：聚合豆瓣热门榜单 / 豆瓣想看 / TMDB 片单，经 PanSou 检索夸克网盘资源，调用 QAS 自动转存，并支持定时调度与失效链接修复。

## ✨ 功能特性

- 🎬 **豆瓣榜单抓取**：热门电影、最新电影、豆瓣高分、冷门佳片，电视剧 7 个分类 + 综艺 3 个分类
- 📺 **TMDB 数据源**：浏览 TMDB 热门 / 发现片单，按地区与类型筛选，自动标记已转存条目（已整合进「概览」页）
- 💡 **豆瓣想看**：自动转存你的豆瓣「想看」清单到网盘，支持多账号与自定义保存目录
- 🔍 **资源搜索**：集成 PanSou 搜索，一键转存到指定目录，支持链接有效性验证
- ⏰ **定时调度**：每日定时 / Cron 表达式，自动转存与失效链接检测修复
- 📊 **概览面板**：今日转存、下次调度、上次结果、近 7 天成功率条形图，关键指标一目了然
- 🗑️ **历史管理**：转存历史 / 执行历史查看、删除、导出，执行记录一键清除
- 🔐 **安全认证**：PBKDF2 密码哈希、Token 登录保护、登录频率限制、敏感字段加密存储
- 🎨 **深色 / 浅色主题**：紧凑卡片布局，移动端响应式适配
- 🐳 **Docker 部署**：容器化运行，数据目录绑定持久化

## 🚀 快速开始

### Docker 部署

```bash
docker run -d \
  --name dbauto \
  --restart unless-stopped \
  -p 3001:3001 \
  -v /opt/douban-history:/data/douban-history \
  -e AUTH_PASS=your_password \
  ghcr.io/ciweicc/dbauto:latest
```

启动后访问 `http://localhost:3001` 登录，在设置页面（⚙️）中配置 PanSou、QAS 地址和 Token。

### 更新镜像

```bash
docker stop dbauto && docker rm dbauto
docker pull ghcr.io/ciweicc/dbauto:latest
# 然后重新执行上面的 docker run 命令
```

### 忘记密码

如果忘记登录密码，可以通过以下命令重置（无需进入容器）：

```bash
docker run --rm \
  -v /opt/douban-history:/data/douban-history \
  ghcr.io/ciweicc/dbauto:latest \
  --reset-password your_new_password
```

> 请将 `/opt/douban-history` 替换为你实际的数据目录挂载路径，`your_new_password` 替换为新密码。
> 重置后使用新密码登录即可，原有配置和数据不受影响。

如果容器正在运行，也可以用 `docker exec`：

```bash
docker exec -it dbauto python reset_password.py your_new_password
```

> 也可使用 `docker-compose up -d`，详见 [docker-compose.yml](docker-compose.yml)。

### 本地运行

```bash
pip install -r requirements.txt
python main.py
```

服务将在 `http://localhost:3001` 启动。

## 🔧 使用流程

1. **配置服务**：首次登录后，在「设置」页填入 PanSou 地址、QAS 地址与 QAS Token（可选填 TMDB API Key 启用 TMDB 数据源）。
2. **浏览与转存**：在「概览」页浏览豆瓣榜单或 TMDB 片单，勾选条目后一键转存；或在「手动转存」搜索资源并指定保存目录。
3. **自动运行**：在「定时任务」配置每日固定时间或 Cron 表达式，自动执行转存与失效链接检测。
4. **查看结果**：在「执行历史」查看每次运行的明细、成功率与近 7 天条形趋势图。

## 📁 项目结构

```
DBAuto/
├── main.py                 # 服务入口
├── app_modules/            # 后端：路由 Mixin + 调度 + 转存 + 豆瓣/TMDB 抓取
│   ├── routes.py           # HTTP 路由分发（do_GET / do_POST）
│   ├── routes_*.py         # 各模块路由（auth / config / transfer / history / tmdb）
│   ├── scheduler.py        # 定时调度与失效链接检测
│   ├── transfer.py         # 转存核心逻辑
│   ├── douban.py           # 豆瓣榜单 / 想看抓取
│   └── tmdb.py             # TMDB 数据源
├── static/                 # 前端（vanilla JS，构建为单文件 index_new.html）
│   ├── login_new.html      # 登录页
│   └── src/                # 源码：body.html / scripts / styles（build.sh 合并）
├── docs/                   # 接口与部署文档
└── tests/                  # 接口 / 冒烟 / 合并回归测试
```

## 🔌 API 接口

所有 API 响应均为 JSON 格式，需通过 Header `X-Auth-Token` 或 Query 参数 `?token=` 携带 Token 认证（登录接口除外）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/login` | 登录，返回 Token |
| GET | `/api/status` | 检查认证状态 |
| GET | `/api/sse` | SSE 实时推送（日志 / 进度 / 状态变更） |
| GET | `/api/refresh_douban` | 刷新豆瓣缓存（想看清单） |
| GET | `/api/categories` | 获取榜单分类 |
| GET | `/api/search` | PanSou 资源搜索 |
| POST | `/api/search_replace` | 搜索并按标题 / 直链替换资源 |
| POST | `/api/transfer` | 启动批量转存 |
| POST | `/api/transfer_one` | 单条转存（搜索或直链） |
| GET | `/api/transfer/status` | 转存状态与进度 |
| POST | `/api/stop` | 停止当前转存 |
| GET | `/api/schedule` | 获取定时设置 |
| POST | `/api/schedule` | 保存 / 切换 / 立即执行 |
| GET | `/api/check_expired` | 检测失效链接 |
| GET | `/api/fix_expired` | 启动失效链接自动修复 |
| GET | `/api/update_expired` | 更新失效任务信息 |
| GET | `/api/history` | 转存历史列表 |
| GET/POST | `/api/history/manage` | 历史管理（查看 / 删除 / 清空 / 添加 / 更新） |
| GET | `/api/history/export` | 导出历史为文本文件 |
| GET | `/api/exec_history` | 执行历史列表（分页） |
| POST | `/api/exec_history/manage` | 执行历史管理（清除） |
| GET | `/api/tmdb/options` | TMDB 地区 / 片单类型选项 |
| GET | `/api/tmdb/genres` | TMDB 类型标签 |
| GET | `/api/tmdb/list` | TMDB 片单列表（标记已转存） |
| GET | `/api/tmdb/refresh` | 刷新 TMDB 缓存 |
| GET | `/api/config` | 获取系统配置（敏感字段掩码） |
| POST | `/api/config` | 保存系统配置 |
| GET | `/api/dashboard/stats` | 仪表盘统计（单体数据） |
| GET | `/api/dashboard/all` | 概览聚合数据（统计 + 调度状态 + 版本） |
| GET | `/api/settings/all` | 设置聚合数据（配置 + 定时设置） |

详细接口文档参见 [CODE_WIKI.md](docs/CODE_WIKI.md)。

## 依赖服务

| 服务 | 用途 | 默认端口 |
|------|------|----------|
| [PanSou](https://github.com/fish2018/pansou) | 网盘资源搜索 | 8080 |
| [QAS (夸克自动转存)](https://github.com/Cp0204/quark-auto-save) | 转存任务执行 | 5005 |

以上服务地址和 Token 在首次启动后通过设置页面（⚙️）配置即可，无需写在环境变量中。

## 环境变量

环境变量仅在**首次启动**时作为初始值写入 `config.json`，后续以配置文件为准。推荐直接启动后在设置页面配置。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `3001` | 服务端口 |
| `DATA_DIR` | `/data/douban-history` | 数据持久化目录 |
| `AUTH_USER` | `root` | 登录用户名（首次） |
| `AUTH_PASS` | — | 登录密码（首次） |
| `PANSOU` | — | PanSou 地址（首次，可选） |
| `QAS` | — | QAS 地址（首次，可选） |
| `QAS_TOKEN` | — | QAS Token（首次，可选） |
| `TZ` | `Asia/Shanghai` | 时区 |

## 数据目录

`DATA_DIR` 持久化存储以下文件：

| 文件 | 说明 |
|------|------|
| `config.json` | 系统配置（敏感字段加密存储） |
| `settings.json` | 定时任务设置 |
| `app.db` | SQLite 数据库（转存历史、执行历史） |
| `.salt` | 加密密钥盐文件 |

> 首次启动时，如检测到旧版 JSON 历史文件（`transfer_history.json`、`exec_history.json`）会自动迁移到 SQLite。

---

> ⚠️ 本项目仅供学习研究使用，请遵守相关服务的使用条款。
