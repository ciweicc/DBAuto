# 豆瓣自动转存 (DBAuto) - Code Wiki

## 目录

1. [项目概述](#项目概述)
2. [系统架构](#系统架构)
3. [目录结构](#目录结构)
4. [核心模块详解](#核心模块详解)
5. [关键类与函数](#关键类与函数)
6. [API 接口文档](#api-接口文档)
7. [数据存储](#数据存储)
8. [依赖关系](#依赖关系)
9. [部署与运行](#部署与运行)
10. [测试](#测试)
11. [前端页面](#前端页面)

---

## 项目概述

### 项目简介

**豆瓣自动转存 (DBAuto)** 是一个基于豆瓣移动端 API 的影视资源自动转存工具。它通过 PanSou 搜索夸克网盘资源，调用 QAS (夸克自动转存) 服务将资源自动转存到用户的网盘中。

### 功能特性

- 🎬 **豆瓣榜单抓取**：热门电影、最新电影、豆瓣高分、冷门佳片，涵盖 30 个子榜单
- 📺 **电视剧/综艺**：热门剧集 7 个分类 + 热门综艺 3 个分类
- 🔍 **资源搜索**：集成 PanSou 搜索，一键转存到指定目录，支持链接有效性验证
- ⏰ **定时调度**：支持每日/每周/每月定时转存、失效检测
- 📊 **仪表盘**：今日转存量、上次执行状态、近 7 天统计、下次调度时间一目了然
- 🗑️ **历史管理**：一键清除执行历史、支持历史记录导出
- 🔐 **安全认证**：PBKDF2 密码哈希、Token 登录保护、登录频率限制
- 🎨 **Apple 风格 UI**：深色/浅色主题切换、毛玻璃卡片、iOS 风格组件
- 🐳 **Docker 部署**：容器化运行，数据目录绑定持久化
- ⚡ **高性能**：HTTP 连接池、搜索并发、Cron 缓存、gzip 压缩等多项优化

### 技术栈

- **后端**：Python 3.11+ / 标准库 HTTP 服务器
- **前端**：原生 HTML/CSS/JavaScript (单文件内联)
- **数据存储**：SQLite (主要) + JSON (兼容)
- **外部依赖**：
  - [PanSou](https://github.com/fish2018/pansou) - 网盘资源搜索服务
  - [QAS (夸克自动转存)](https://github.com/Cp0204/quark-auto-save) - 转存任务执行服务

---

## 系统架构

### 整体架构图

```
┌───────────────────────────────────────────────────────────┐
│                        前端 UI                            │
│  (index_new.html / login_new.html)                        │
└──────────────────────────┬────────────────────────────────┘
                           │ HTTP / SSE
                           ▼
┌───────────────────────────────────────────────────────────┐
│                   HTTP 服务器层                            │
│  ThreadedHTTPServer → RouteHandler (Mixin 多继承)         │
└──────────────────────────┬────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  认证模块    │  │  转存模块    │  │  配置模块    │
│  auth.py     │  │  transfer.py │  │  config.py   │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
       └─────────────────┼─────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
    ┌─────────┐    ┌─────────┐    ┌──────────┐
    │ utils.py│    │storage.py│   │scheduler.py│
    │ (工具类)│    │ (数据层) │    │ (调度器)  │
    └─────────┘    └────┬────┘    └─────┬────┘
                        │               │
                        ▼               ▼
                   ┌─────────┐    ┌──────────┐
                   │ SQLite  │    │  croniter│
                   └─────────┘    └──────────┘

         ┌──────────────────────────────────┐
         │          外部服务                 │
         │  ┌─────────┐      ┌──────────┐  │
         │  │ PanSou  │      │   QAS    │  │
         │  │ (搜索)  │      │ (转存)   │  │
         │  └─────────┘      └──────────┘  │
         └──────────────────────────────────┘
```

### 架构设计特点

1. **Mixin 多继承路由模式**：路由处理通过多个 Mixin 类组合实现，每个 Mixin 负责一类功能
2. **单例模式**：配置管理、认证管理等核心类使用线程安全的单例模式
3. **多级缓存**：内存缓存 (TTLCache) + 磁盘缓存 (DiskCache) + 二级缓存 (TwoLevelCache)
4. **线程模型**：
   - 主线程：HTTP 服务器 (ThreadedHTTPServer)
   - 后台线程：定时调度器 (scheduler_loop)
   - 工作线程：转存任务执行 (ThreadPoolExecutor)
5. **SSE 实时推送**：使用 Server-Sent Events 向前端推送实时进度和日志

---

## 目录结构

```
/workspace/
├── main.py                      # 项目入口文件
├── mcp_server.py                # MCP Server (stdio + SSE 双模式)
├── reset_password.py            # 密码重置脚本
├── VERSION                      # 版本号文件 (1.0.0)
├── requirements.txt             # Python 依赖
├── requirements-dev.txt         # 开发依赖 (含 pytest)
├── Dockerfile                   # Docker 镜像构建
├── docker-compose.yml           # Docker Compose 配置
├── docker-entrypoint.sh         # Docker 入口脚本
├── .dockerignore                # Docker 忽略文件
├── .gitignore                   # Git 忽略文件
│
├── app_modules/                 # 后端 Python 模块
│   ├── main.py                  # 启动逻辑 (start() 函数)
│   ├── config.py                # 配置管理 (ConfigManager 单例)
│   ├── auth.py                  # 认证模块 (PBKDF2、登录频率限制)
│   ├── utils.py                 # 工具模块 (HTTP、缓存、加密、SSE)
│   ├── storage.py               # SQLite 存储 (历史记录迁移)
│   ├── douban.py                # 豆瓣榜单 API (带 1 小时缓存)
│   ├── transfer.py              # 转存执行 (搜索并发、失效检测)
│   ├── scheduler.py             # 定时调度 (Cron 解析、精确睡眠)
│   ├── api_client.py            # API 客户端 (PanSouClient, QASClient)
│   ├── validator.py             # 参数验证器
│   ├── server.py                # ThreadedHTTPServer
│   ├── routes_base.py           # 路由基类 + 通用工具
│   ├── routes_static.py         # 静态文件路由 + SSE (Mixin)
│   ├── routes_auth.py           # 认证相关路由 (Mixin)
│   ├── routes_transfer.py       # 转存相关路由 (Mixin)
│   ├── routes_history.py        # 历史记录路由 (Mixin)
│   ├── routes_config.py         # 配置 & 调度路由 (Mixin)
│   └── routes.py                # 路由组合 (主 Handler 类)
│
├── static/                      # 前端静态文件
│   ├── index_new.html           # 主页面 (单文件内联 CSS + JS)
│   ├── login_new.html           # 登录页面
│   └── favicon.svg              # 网站图标
│
├── tests/                       # 测试文件
│   ├── test_utils.py            # utils 模块测试
│   ├── test_validator.py        # validator 模块测试
│   └── test_transfer.py         # transfer 模块测试
│
├── docs/                        # 文档
│   ├── CODE_WIKI.md             # 本文档
│   └── compose/
│       └── plans/
│           └── 2026-06-13-transfer-improvements.md
│
└── .github/
    └── workflows/
        └── docker-build.yml     # GitHub Actions 工作流
```

---

## 核心模块详解

### 1. config.py - 配置管理

**职责**：统一管理系统配置和调度设置，线程安全，支持敏感字段加密存储。

**核心组件**：

- `ConfigManager` 类：单例模式的配置管理器
- `_SENSITIVE_FIELDS`：敏感字段集合 (qas_token, auth_pass)
- `DEFAULT_CONFIG`：默认系统配置
- `DEFAULT_SETTINGS`：默认调度设置
- `CATEGORIES`：豆瓣榜单分类定义

**配置项说明**：

| 配置项 | 类型 | 说明 | 默认值 |
|--------|------|------|--------|
| pansou | string | PanSou 服务地址 | http://192.168.1.1:8080 |
| qas | string | QAS 服务地址 | http://192.168.1.1:5005 |
| qas_token | string | QAS API Token | - |
| auth_user | string | 登录用户名 | root |
| auth_pass | string | 登录密码 | - |

**调度设置结构**：

```python
{
  "transfer": {
    "enabled": False,      # 是否启用定时转存
    "time": "02:00",       # 每日执行时间
    "cron": "",            # Cron 表达式 (优先级高于 time)
    "limit": 5,            # 每次转存上限
    "tasks": [],           # 任务列表
    "filters": {           # 筛选条件
      "min_rating": 0,     # 最低评分
      "sort_by": "rating", # 排序方式
      "year_from": 0,      # 起始年份
      "year_to": 0         # 结束年份
    }
  },
  "expired_check": {
    "enabled": False,      # 是否启用失效检测
    "time": "03:00",       # 每日执行时间
    "cron": "",            # Cron 表达式
    "directories": []      # 检测目录范围
  }
}
```

---

### 2. auth.py - 认证管理

**职责**：用户认证、Token 管理、登录频率限制。

**核心组件**：

- `AuthManager` 类：单例模式的认证管理器
- `TOKEN_TTL = 86400`：Token 有效期 (24 小时)
- `LOGIN_MAX_ATTEMPTS = 5`：登录窗口内最大尝试次数
- `LOGIN_WINDOW = 60`：登录频率检测窗口 (秒)
- `LOGIN_LOCK_DURATION = 300`：登录锁定时长 (秒)

**安全特性**：

1. **密码哈希**：使用 PBKDF2-HMAC-SHA256 算法，100000 次迭代，16 字节随机盐
2. **Token 认证**：随机生成 32 字符十六进制 Token
3. **登录频率限制**：60 秒内最多 5 次尝试，失败后锁定 5 分钟
4. **Token 过期清理**：每次验证时自动清理过期 Token
5. **多渠道 Token 提取**：支持 Header (X-Auth-Token / Authorization: Bearer) 和 Query 参数

---

### 3. utils.py - 工具模块

**职责**：提供通用工具函数和类，包括缓存、HTTP、加密、日志、SSE 等。

**核心类**：

#### TTLCache
带 TTL 和最大条目数的线程安全内存缓存。

```python
cache = TTLCache(ttl=300, max_size=100)
cache.set("key", "value")
value = cache.get("key")
```

#### DiskCache
磁盘缓存，支持持久化存储，数据以 JSON 文件形式存储。

#### TwoLevelCache
二级缓存：内存缓存 (快速) + 磁盘缓存 (持久化)。

**核心函数**：

| 函数 | 说明 |
|------|------|
| `hash_password(password)` | PBKDF2 密码哈希 |
| `verify_password(password, hashed)` | 密码验证 |
| `encrypt_secret(plaintext)` | Fernet 对称加密 |
| `decrypt_secret(encrypted)` | Fernet 对称解密 |
| `http_get(url, timeout, referer)` | HTTP GET 请求 (带重试) |
| `http_post(url, data, timeout)` | HTTP POST 请求 (带重试) |
| `atomic_write_json(filepath, data)` | 原子写入 JSON 文件 |
| `log(msg)` | 日志记录 (同时输出到控制台、内存队列、SSE) |
| `sse_broadcast(evt, data)` | SSE 消息广播 |

---

### 4. storage.py - 数据存储

**职责**：SQLite 数据库操作，历史记录存储，支持从 JSON 自动迁移。

**数据库表**：

#### transfer_history 表
转存历史记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| title | TEXT (PK) | 影视标题 |
| date | TEXT | 转存日期 |
| status | TEXT | 状态 (ok/skipped/failed 等) |
| category | TEXT | 分类 (movie/tv/variety) |

#### exec_history 表
执行历史记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT (PK) | 记录 ID (8 位十六进制) |
| type | TEXT | 类型 (transfer/expired_check/history/config/schedule) |
| detail | TEXT | 详情描述 |
| status | TEXT | 状态 (ok/fail/running) |
| time | TEXT | 执行时间 |
| data | TEXT | 附加数据 (JSON) |

**性能优化**：

- 使用 WAL 模式 (PRAGMA journal_mode=WAL)
- 同步级别设置为 NORMAL (PRAGMA synchronous=NORMAL)
- 执行历史建立 time 字段索引
- 内存缓存 + 数据库双层存储

---

### 5. douban.py - 豆瓣榜单

**职责**：调用豆瓣移动端 API 获取榜单数据，带 1 小时缓存。

**API 端点**：`https://m.douban.com/rexxar/api/v2/subject/recent_hot/{type}`

**支持的榜单分类**：

| 大类 | 子类 | 路径 | 类型 |
|------|------|------|------|
| 电影 | 热门电影 | movie/hot | 全部/华语/欧美/韩国/日本 |
| 电影 | 最新电影 | movie/latest | 全部/华语/欧美/韩国/日本 |
| 电影 | 豆瓣高分 | movie/top | 全部/华语/欧美/韩国/日本 |
| 电影 | 冷门佳片 | movie/underrated | 全部/华语/欧美/韩国/日本 |
| 电视剧 | 热门剧集 | tv/drama | 综合/国产剧/欧美剧/日剧/韩剧/动画/纪录片 |
| 综艺 | 热门综艺 | tv/variety | 综合/国内/国外 |

**核心函数**：`get_douban_list(path, sub_type, limit, min_rating, sort_by, year_from, year_to)`

**功能**：
- 从豆瓣 API 获取原始数据
- 按最低评分筛选
- 按年份范围筛选
- 按评分或年份排序
- 结果限制数量
- 1 小时 TTL 缓存

---

### 6. transfer.py - 转存执行

**职责**：核心转存逻辑，包括 PanSou 搜索、QAS 转存、失效检测、历史去重。

**核心状态**：`transfer_status` 全局状态字典

```python
{
  "running": False,        # 是否正在运行
  "summary": None,         # 完成后摘要
  "start_time": None,      # 开始时间
  "stats": {               # 统计信息
    "searched": 0,         # 已搜索数
    "ok": 0,               # 成功数
    "skipped": 0,          # 跳过数
    "failed": 0,           # 失败数
    "total": 0             # 总数
  },
  "thread_id": None,       # 执行线程 ID
  "stop": False            # 停止标志
}
```

**主要流程**：

1. **构建任务列表**：从豆瓣榜单获取影视列表，去重
2. **历史去重**：检查转存历史和 QAS 任务缓存，跳过已存在的
3. **并发搜索**：使用 ThreadPoolExecutor (3 并发) 调用 PanSou 搜索资源
4. **逐个转存**：按顺序调用 QAS 执行转存，每个间隔 3 秒
5. **结果记录**：保存转存历史，更新执行记录

**并发配置**：
- 搜索并发数：`SEARCH_CONCURRENCY = 3`
- 失效检测并发数：`EXPIRED_CHECK_CONCURRENCY = 5`

**历史去重算法**：
- 精确匹配：标题完全相同
- 模糊匹配：移除特殊字符后比较，支持包含关系 (长度 >= 3)
- QAS 缓存匹配：与 QAS 已有任务名比较

---

### 7. scheduler.py - 定时调度

**职责**：定时任务调度，支持每日时间和 Cron 表达式。

**调度任务类型**：

| 任务类型 | 说明 | 默认时间 |
|----------|------|----------|
| transfer | 定时转存 | 02:00 |
| expired_check | 失效链接检测 | 03:00 |

**调度方式**：
1. **每日时间**：`HH:MM` 格式，每天同一时间执行
2. **Cron 表达式**：标准 5 字段 Cron 格式 (分 时 日 月 周)

**核心功能**：
- `scheduler_loop()`：主调度循环，精确睡眠等待
- `_next_fire_time(time_str, cron_str)`：计算下次触发时间
- `_warmup_douban(tasks)`：转存前 3 分钟预热豆瓣数据
- `notify_settings_changed()`：通知调度器设置已变更

**调度精度**：
- 使用 `Event.wait()` 实现可中断睡眠
- 设置变更时立即重新计算下次触发时间
- 触发时间误差控制在 2 秒内

---

### 8. api_client.py - API 客户端

**职责**：封装 PanSou 和 QAS 服务的 API 调用。

**类层次结构**：

```
APIClient (基类)
├── PanSouClient
│   ├── search(keyword)          # 搜索资源
│   └── check_links(urls)        # 批量检查链接有效性
└── QASClient
    ├── get_data()               # 获取任务列表数据
    ├── get_share_detail(url)    # 获取分享链接详情
    ├── add_task(...)            # 添加转存任务
    ├── run_script_now(tasks)    # 立即执行脚本
    ├── run_script_now_stream()  # 流式执行 (SSE)
    └── update(data)             # 更新任务数据
```

---

### 9. validator.py - 参数验证

**职责**：提供统一的参数验证函数。

**验证函数列表**：

| 函数 | 说明 | 返回值 |
|------|------|--------|
| `validate_string(value, min_len, max_len, allow_empty)` | 字符串验证 | (bool, str) |
| `validate_url(value, required)` | URL 格式验证 | (bool, str) |
| `validate_port(value)` | 端口号验证 | (bool, str) |
| `validate_cron(value, required)` | Cron 表达式验证 | (bool, str) |
| `validate_time(value, required)` | 时间格式验证 (HH:MM) | (bool, str) |
| `validate_positive_int(value, min_val, max_val)` | 正整数验证 | (bool, str) |
| `validate_list(value, min_len, max_len, item_validator)` | 列表验证 | (bool, str) |
| `validate_dict(value, required_keys, allowed_keys)` | 字典验证 | (bool, str) |
| `validate_task(task)` | 转存任务验证 | (bool, str) |

所有验证函数返回 `(is_valid, error_message)` 元组。

---

### 10. 路由模块

采用 Mixin 多继承模式，将路由按功能拆分为多个模块：

| 模块 | 类名 | 职责 |
|------|------|------|
| routes_base.py | BaseRouteHandler | 基类，通用工具方法 |
| routes_static.py | StaticRouteMixin | 静态文件、健康检查、SSE |
| routes_auth.py | AuthRouteMixin | 登录、认证状态 |
| routes_transfer.py | TransferRouteMixin | 转存、搜索、失效检测 |
| routes_history.py | HistoryRouteMixin | 历史记录管理 |
| routes_config.py | ConfigRouteMixin | 系统配置、调度管理 |
| routes.py | H | 主 Handler，整合所有 Mixin |

**路由处理流程 (GET)**：

```
do_GET()
  ├── 静态文件 & 健康检查 (无需认证)
  ├── 认证相关 (登录状态、SSE)
  ├── 认证检查
  ├── 转存 & 搜索
  ├── 历史记录
  ├── 配置 & 调度
  └── 404
```

---

## 关键类与函数

### ConfigManager 类

**位置**：[config.py](file:///workspace/app_modules/config.py#L75-L178)

**方法**：

| 方法 | 说明 |
|------|------|
| `get_instance()` | 获取单例实例 |
| `get_config()` | 获取系统配置 |
| `set_config(cfg)` | 保存系统配置 |
| `get_settings()` | 获取调度设置 |
| `set_settings(settings)` | 保存调度设置 |
| `reload()` | 重新加载配置 |

**属性 (property)**：

- `pansou` / `qas` / `qas_token` / `auth_user` / `auth_pass`

---

### AuthManager 类

**位置**：[auth.py](file:///workspace/app_modules/auth.py#L13-L111)

**方法**：

| 方法 | 说明 |
|------|------|
| `get_instance()` | 获取单例实例 |
| `extract_token(handler)` | 从请求中提取 Token |
| `check_auth(handler)` | 检查认证状态 |
| `login(username, password)` | 登录，返回 Token |
| `check_login_rate(ip)` | 检查登录频率 |
| `hash_password(password)` | 静态方法，密码哈希 |
| `get_client_ip(handler)` | 静态方法，获取客户端 IP |

---

### TTLCache 类

**位置**：[utils.py](file:///workspace/app_modules/utils.py#L9-L50)

线程安全的内存缓存，支持 TTL 过期和最大条目数限制。

**方法**：
- `get(key)` - 获取值
- `set(key, value)` - 设置值
- `clear()` - 清空缓存
- `__contains__(key)` - in 运算符支持
- `__len__()` - len() 支持

---

### PanSouClient 类

**位置**：[api_client.py](file:///workspace/app_modules/api_client.py#L34-L42)

PanSou 搜索服务客户端。

**方法**：
- `search(keyword)` - 搜索夸克网盘资源
- `check_links(urls)` - 批量检查链接有效性

---

### QASClient 类

**位置**：[api_client.py](file:///workspace/app_modules/api_client.py#L45-L73)

夸克自动转存服务客户端。

**方法**：
- `get_data()` - 获取所有任务数据
- `get_share_detail(shareurl)` - 获取分享链接详情
- `add_task(taskname, shareurl, savepath, pattern, replace)` - 添加任务
- `run_script_now(tasklist)` - 立即执行脚本
- `run_script_now_stream(tasklist)` - 流式执行脚本
- `update(data)` - 更新任务数据

---

### 核心函数

#### `run_transfer(task_list, limit)`

**位置**：[transfer.py](file:///workspace/app_modules/transfer.py#L408-L539)

执行批量转存的核心函数。

**参数**：
- `task_list`：任务列表，每项包含 title, savepath, category
- `limit`：转存成功数量上限

**流程**：
1. 创建执行记录 (状态: running)
2. 更新 transfer_status 全局状态
3. 加载历史记录，构建去重索引
4. 过滤已存在的任务
5. 并发搜索资源 (3 线程)
6. 逐个执行转存
7. 保存历史，更新执行记录

---

#### `check_expired_tasks(limit)`

**位置**：[transfer.py](file:///workspace/app_modules/transfer.py#L173-L215)

检测失效链接。

**参数**：
- `limit`：检测数量上限 (可选)

**返回值**：失效任务列表

---

#### `scheduler_loop()`

**位置**：[scheduler.py](file:///workspace/app_modules/scheduler.py#L124-L175)

主调度循环，在后台线程中运行。

**功能**：
- 计算下次触发时间
- 精确睡眠等待
- 转存前预热豆瓣数据
- 触发定时任务
- 响应设置变更

---

## API 接口文档

### 基础信息

- **Base URL**：`http://host:3001`
- **认证方式**：Header `X-Auth-Token` 或 `Authorization: Bearer <token>` 或 Query 参数 `?token=`
- **响应格式**：JSON

---

### 认证接口

#### POST /api/login
登录获取 Token。

**请求体**：
```json
{
  "username": "root",
  "password": "your_password"
}
```

**响应**：
```json
{
  "success": true,
  "token": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

**错误响应**：
- 400：参数验证失败
- 401：凭证无效
- 429：登录尝试过于频繁

---

#### GET /api/status
检查当前认证状态。

**响应**：
```json
{
  "auth": true
}
```

---

### 榜单 & 转存接口

#### GET /api/categories
获取豆瓣榜单分类。

**响应**：
```json
{
  "movie": {
    "name": "电影",
    "icon": "🎬",
    "subs": { ... }
  },
  "tv": { ... },
  "variety": { ... }
}
```

---

#### POST /api/transfer
启动批量转存。

**请求体**：
```json
{
  "tasks": [
    {
      "path": "movie/hot",
      "type": "全部",
      "savepath": "/电影/热门"
    }
  ],
  "limit": 5,
  "filters": {
    "min_rating": 0,
    "sort_by": "rating",
    "year_from": 0,
    "year_to": 0
  }
}
```

**响应**：
```json
{
  "success": true,
  "message": "started 20"
}
```

---

#### POST /api/stop
停止当前转存任务。

**响应**：
```json
{
  "success": true,
  "message": "stopping"
}
```

---

#### GET /api/transfer/status
获取转存状态和进度。

**响应**：
```json
{
  "running": true,
  "start_time": "2024-01-01 02:00:00",
  "stats": {
    "searched": 10,
    "ok": 3,
    "skipped": 2,
    "failed": 1,
    "total": 20
  },
  "progress": ["[02:00:01] 开始转存..."]
}
```

---

#### POST /api/transfer_one
单条转存。

**请求体** (搜索转存)：
```json
{
  "title": "电影名称",
  "savepath": "/电影",
  "category": "movie"
}
```

**请求体** (直接转存)：
```json
{
  "title": "电影名称",
  "savepath": "/电影",
  "shareurl": "https://pan.quark.cn/s/xxxxxx"
}
```

---

#### GET /api/search
PanSou 资源搜索。

**参数**：
- `keyword` / `q`：搜索关键词
- `category`：分类 (默认 movie)

**响应**：
```json
{
  "success": true,
  "results": [
    {
      "title": "资源标题",
      "url": "https://pan.quark.cn/s/xxxxxx",
      "source": "夸克网盘"
    }
  ]
}
```

---

### 定时任务接口

#### GET /api/schedule
获取定时任务设置和状态。

**响应**：
```json
{
  "transfer": { ... },
  "expired_check": { ... },
  "_status": {
    "transfer_next": "2024-01-02 02:00",
    "expired_check_next": "2024-01-02 03:00",
    "last_transfer": "2024-01-01 02:00:00",
    "last_expired_check": "2024-01-01 03:00:00"
  },
  "_next_runs": {
    "transfer": "2024-01-02 02:00",
    "expired_check": "2024-01-02 03:00"
  }
}
```

---

#### POST /api/schedule
保存或操作定时设置。

**动作 1: 保存设置**
```json
{
  "action": "save",
  "transfer": {
    "enabled": true,
    "time": "02:00",
    "limit": 10,
    "tasks": [...]
  },
  "expired_check": {
    "enabled": false
  }
}
```

**动作 2: 开关切换**
```json
{
  "action": "toggle",
  "section": "transfer",
  "enabled": true
}
```

**动作 3: 立即执行**
```json
{
  "action": "run_now",
  "section": "transfer"
}
```

---

#### GET /api/check_expired
检测失效链接。

**参数**：
- `limit`：检测数量上限 (默认 20，最大 500)

**响应**：
```json
{
  "expired": [
    {
      "taskname": "失效任务名",
      "shareurl": "https://pan.quark.cn/s/xxxxxx",
      "savepath": "/电影/xxx"
    }
  ]
}
```

---

#### GET /api/fix_expired
启动失效链接修复。

**响应**：
```json
{
  "success": true,
  "message": "已启动失效链接修复"
}
```

---

### 历史记录接口

#### GET /api/dashboard/stats
获取仪表盘统计数据。

**响应**：
```json
{
  "today_count": 5,
  "week_ok": 20,
  "week_fail": 2,
  "week_total": 30,
  "last_status": "success",
  "last_time": "2024-01-01 02:05:00"
}
```

---

#### GET /api/history
获取转存历史。

**响应**：
```json
{
  "total": 100,
  "items": {
    "电影标题": {
      "date": "2024-01-01",
      "status": "ok",
      "category": "movie"
    }
  }
}
```

---

#### GET /api/history/manage
获取分类整理的历史记录。

---

#### POST /api/history/manage
历史记录管理。

**动作: delete**
```json
{
  "action": "delete",
  "titles": ["电影1", "电影2"]
}
```

**动作: clear**
```json
{
  "action": "clear"
}
```

**动作: add**
```json
{
  "action": "add",
  "title": "新电影",
  "shareurl": "https://...",
  "category": "movie"
}
```

**动作: update**
```json
{
  "action": "update",
  "title": "电影名",
  "shareurl": "https://..."
}
```

---

#### GET /api/history/export
导出历史记录为文本文件。

---

#### GET /api/exec_history
获取执行历史列表。

**参数**：
- `limit`：每页数量 (默认 50)
- `page`：页码 (默认 1)

**响应**：
```json
{
  "total": 100,
  "items": [
    {
      "id": "abcdef12",
      "type": "transfer",
      "detail": "转存完成 成功5 失败0 跳过3",
      "status": "ok",
      "time": "2024-01-01 02:05:00",
      "data": { ... }
    }
  ]
}
```

---

#### POST /api/exec_history/manage
执行历史管理。

**动作: clear**
```json
{
  "action": "clear"
}
```

---

### 配置接口

#### GET /api/config
获取系统配置 (敏感字段已掩码)。

**响应**：
```json
{
  "pansou": "http://192.168.1.1:8080",
  "qas": "http://192.168.1.1:5005",
  "qas_token": "***",
  "auth_user": "root",
  "auth_pass": "***"
}
```

---

#### POST /api/config
保存系统配置。

**请求体**：
```json
{
  "pansou": "http://...",
  "qas": "http://...",
  "qas_token": "new_token",
  "auth_user": "admin",
  "auth_pass": "new_password"
}
```

---

### 其他接口

#### GET /health
健康检查。

**响应**：
```json
{
  "status": "ok",
  "time": 1704067200.0
}
```

---

#### GET /version
获取版本号。

**响应**：
```json
{
  "version": "1.0.0"
}
```

---

#### GET /api/sse
SSE 实时推送接口。

**事件类型**：
- `log`：日志消息
- `transfer_progress`：转存进度更新
- `auth`：认证状态变更
- `history_update`：历史记录更新
- `config_update`：配置更新
- `schedule_update`：调度更新
- `exec_history_update`：执行历史更新

---

## 数据存储

### 数据目录

默认路径：`/data/douban-history/` (可通过 `DATA_DIR` 环境变量修改)

### 文件列表

| 文件 | 说明 | 格式 |
|------|------|------|
| config.json | 系统配置 | JSON (敏感字段加密) |
| settings.json | 定时任务设置 | JSON |
| app.db | SQLite 数据库 | SQLite3 |
| .salt | 加密密钥盐 | Binary |
| cache/ | 磁盘缓存目录 | 目录 |

### 数据迁移

首次启动时，如果检测到旧版 JSON 格式的历史文件：
- `transfer_history.json` → `transfer_history` 表
- `exec_history.json` → `exec_history` 表

迁移完成后 JSON 文件保留但不再使用。

---

## 依赖关系

### Python 依赖

| 包名 | 版本要求 | 用途 |
|------|----------|------|
| requests | >= 2.28.0 | HTTP 请求 |
| cryptography | >= 41.0.0 | 加密 (Fernet) |
| croniter | >= 1.3.0 | Cron 表达式解析 |
| mcp | >= 1.0.0 | MCP 协议 Server (含 uvicorn, starlette) |

### 开发依赖

| 包名 | 版本要求 | 用途 |
|------|----------|------|
| pytest | >= 7.0.0 | 测试框架 |

### 外部服务依赖

| 服务 | 用途 | 默认端口 |
|------|------|----------|
| PanSou | 网盘资源搜索 | 8080 |
| QAS (夸克自动转存) | 转存任务执行 | 5005 |

### 内部模块依赖图

```
main.py
  ├── config.py
  ├── auth.py ────┐
  ├── utils.py ◄──┘
  ├── storage.py ─┤
  ├── douban.py ──┤
  ├── transfer.py ┤
  ├── scheduler.py┤
  ├── server.py   │
  └── routes.py ──┘
        │
        ├── routes_base.py
        ├── routes_static.py
        ├── routes_auth.py
        ├── routes_transfer.py
        ├── routes_history.py
        └── routes_config.py
```

---

## 部署与运行

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `3001` | 服务端口 |
| `DATA_DIR` | `/data/douban-history` | 数据目录 |
| `PANSOU` | `http://192.168.1.1:8080` | PanSou 服务地址 |
| `QAS` | `http://192.168.1.1:5005` | QAS 服务地址 |
| `QAS_TOKEN` | - | QAS API Token |
| `AUTH_USER` | `root` | 登录用户名 |
| `AUTH_PASS` | - | 登录密码 |
| `TZ` | `Asia/Shanghai` | 时区 |

### Docker 部署

#### 使用 docker run

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
  your-image-name
```

#### 使用 docker-compose

```bash
docker-compose up -d
```

### 本地运行

#### 安装依赖

```bash
pip install -r requirements.txt
```

#### 启动 Web 服务

```bash
python main.py
```

服务将在 `http://localhost:3001` 启动。

#### 启动 MCP 服务 (stdio 模式)

```bash
python mcp_server.py
```

#### 启动 MCP 服务 (SSE 模式)

```bash
python mcp_server.py --sse --port 8765
```

SSE 模式将在 `http://localhost:8765/sse` 启动。

#### 密码重置

```bash
python reset_password.py your_new_password
```

### MCP 部署

#### Docker 启动 MCP Server (SSE 模式)

```bash
docker run -d \
  --name dbauto-mcp \
  -p 8765:8765 \
  -v /opt/douban-history:/data/douban-history \
  ghcr.io/ciweicc/dbauto:latest \
  --mcp --sse --port 8765
```

#### Claude Desktop 配置 (stdio 模式)

编辑 `claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "dbauto": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/DBAuto",
      "env": {
        "DATA_DIR": "/data/douban-history"
      }
    }
  }
}
```

### 健康检查

Docker 健康检查通过 `/health` 端点实现：
- 间隔：30 秒
- 超时：3 秒
- 启动宽限期：10 秒
- 重试次数：3 次

---

## 测试

### 测试框架

使用 pytest 测试框架。

### 安装开发依赖

```bash
pip install -r requirements-dev.txt
```

### 运行测试

```bash
cd /workspace
pytest tests/ -v
```

### 测试文件

| 文件 | 测试内容 |
|------|----------|
| [test_utils.py](file:///workspace/tests/test_utils.py) | TTLCache 缓存功能测试 |
| [test_validator.py](file:///workspace/tests/test_validator.py) | 参数验证器测试 |
| [test_transfer.py](file:///workspace/tests/test_transfer.py) | 历史去重算法测试 |

### 测试覆盖

**test_utils.py** - 6 个测试用例
- 缓存设置和获取
- TTL 过期机制
- 最大条目数限制
- 清空缓存
- contains 运算符
- len() 支持

**test_validator.py** - 8 个测试用例
- 字符串验证
- URL 验证
- 端口验证
- Cron 表达式验证
- 时间格式验证
- 正整数验证
- 列表验证
- 任务结构验证

**test_transfer.py** - 7 个测试用例
- 精确匹配
- 无匹配
- 相似标题匹配
- 部分匹配 (长标题)
- 部分匹配 (短标题，长度 <3 不匹配)
- 大小写不敏感
- 特殊字符移除

---

## 前端页面

### 页面列表

| 文件 | 路径 | 说明 |
|------|------|------|
| [index_new.html](file:///workspace/static/index_new.html) | `/` 或 `/index.html` | 主应用页面 |
| [login_new.html](file:///workspace/static/login_new.html) | `/login.html` | 登录页面 |

### 技术特点

- **单文件架构**：HTML + CSS + JavaScript 全部内联在一个文件中
- **Apple 风格 UI**：毛玻璃效果、圆角卡片、iOS 风格组件
- **深色/浅色主题**：通过 `data-theme` 属性切换
- **响应式设计**：适配移动端和桌面端
- **SSE 实时更新**：通过 Server-Sent Events 接收实时进度

### 主要功能模块

1. **仪表盘**：今日统计、近 7 天统计、上次执行状态、下次调度时间
2. **榜单转存**：选择榜单分类、设置筛选条件、启动转存
3. **资源搜索**：手动搜索资源、单条转存
4. **历史记录**：查看、删除、导出转存历史
5. **失效检测**：检测和修复失效链接
6. **定时设置**：配置定时转存和失效检测
7. **系统配置**：配置 PanSou、QAS、登录密码等

---

## 性能优化

1. **HTTP 连接池**：使用 requests.Session + HTTPAdapter，池大小 20
2. **多级缓存**：
   - 豆瓣数据：1 小时内存缓存
   - PanSou 搜索：10 分钟 TTLCache
   - QAS 任务：启动时全量缓存
   - 静态文件：内存缓存 + gzip 压缩
3. **并发处理**：
   - 搜索并发：3 线程
   - 失效检测并发：5 线程
4. **调度优化**：
   - Cron 解析结果隐式缓存
   - 转存前 3 分钟预热豆瓣数据
   - 精确睡眠，减少 CPU 占用
5. **数据库优化**：
   - WAL 模式
   - 同步级别 NORMAL
   - 内存缓存层

---

## 安全特性

1. **密码安全**：PBKDF2-HMAC-SHA256 哈希，100000 次迭代
2. **敏感数据加密**：配置文件中的 Token 和密码使用 Fernet 加密
3. **Token 认证**：随机 32 字符十六进制 Token，24 小时过期
4. **登录频率限制**：60 秒 5 次，锁定 5 分钟
5. **输入验证**：所有 API 参数都经过 validator 验证
6. **路径安全**：静态文件路径检查，防止目录遍历

---

## MCP Server

### 概述

**位置**：[mcp_server.py](file:///workspace/mcp_server.py)

DBAuto 内置 MCP (Model Context Protocol) Server，可将转存系统的核心能力暴露给 AI 客户端（如 Claude Desktop、Cursor、VS Code），通过自然语言对话即可搜索资源、执行转存、查看历史等。

### 运行模式

| 模式 | 命令 | 适用场景 |
|------|------|----------|
| stdio | `python mcp_server.py` | Claude Desktop 等本地客户端 |
| SSE | `python mcp_server.py --sse --port 8765` | 远程/网络客户端 |
| Docker SSE | `docker run ... --mcp --sse --port 8765` | 容器化部署 |

### 可用 Tools (11 个)

| Tool | 说明 | 参数 |
|------|------|------|
| `get_categories` | 获取豆瓣榜单分类 | 无 |
| `get_douban_list` | 获取豆瓣榜单数据 | path, sub_type, limit, min_rating, sort_by |
| `search_resources` | 搜索夸克网盘资源 | keyword |
| `transfer_one` | 搜索并转存单部影视 | title, savepath, category |
| `transfer_by_url` | 通过分享链接直接转存 | title, shareurl, savepath, category |
| `get_transfer_status` | 获取转存状态和进度 | 无 |
| `get_history` | 获取转存历史 | category (可选) |
| `get_exec_history` | 获取执行历史 | limit |
| `check_expired` | 检测失效链接 | limit |
| `get_dashboard_stats` | 获取仪表盘统计 | 无 |
| `get_schedule` | 获取定时任务设置 | 无 |

### 架构设计

- **异步执行**：所有同步业务函数通过 `asyncio.to_thread()` 在后台线程执行，不阻塞 MCP 协议通信
- **状态检查**：转存操作前检查 `is_transfer_running()` 防止并发冲突
- **配置初始化**：启动时调用 `ConfigManager.get_instance()` 和 `load_config()` 加载配置
- **数据共享**：与 Web 服务共享同一 `DATA_DIR`，读写同一 `config.json`、`app.db`

### 模块依赖

```
mcp_server.py
  ├── mcp.server (Server, stdio_server, NotificationOptions)
  ├── config.py (ConfigManager, CATEGORIES, load_config)
  ├── douban.py (get_douban_list)
  ├── transfer.py (search_pansou, transfer_one, check_expired_tasks, ...)
  ├── storage.py (load_history, load_exec_history)
  └── scheduler.py (_next_fire_time, _now_local)
```

---

*文档生成时间：2026-07-10*
*项目版本：1.0.0*
