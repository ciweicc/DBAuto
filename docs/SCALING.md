# 横向扩展与单实例约束（Scaling & Single-Instance）

> 本文说明 DBAuto 的进程内状态模型及其对水平扩展（多副本 / 多 worker）的限制。
> 相关约束已在服务启动时以日志提示（见 `app_modules/main.py` 的 `_log_single_instance_mode`）。

## 当前架构：单进程 / 单实例

DBAuto 基于 Python 标准库 `http.server` 的 `ThreadedHTTPServer` 运行：

- **单 OS 进程**：整个服务在一个进程中运行；
- **多线程**：`ThreadingMixIn` 为每个请求派生线程，**所有线程共享同一进程内存**。

以下状态均为**进程内单例**（存储在 Python 模块的全局变量 / 类中）：

| 状态 | 位置 | 说明 |
|------|------|------|
| 认证 Token 表 | `app_modules/auth.py` (`AuthManager._tokens`) | 登录后颁发的会话 Token |
| 登录频率限制计数 | `app_modules/auth.py` (`_login_attempts`) | 按 IP 的限速窗口 |
| 配置 / 调度设置缓存 | `app_modules/config.py` (`ConfigManager`) | 单例，进程内缓存 |
| 调度器 | `app_modules/scheduler.py` | 后台调度线程 |
| 转存 / 链接检测状态 | `app_modules/transfer.py` / `link_check.py` | 运行中的任务状态 |
| SSE 客户端集合 | `app_modules/utils.py` (`sse_clients`) | 实时推送订阅者 |
| 各类内存缓存 | `storage.py` (`_history_cache` 等)、`utils.py` (`TTLCache`) | 历史 / 请求缓存 |
| 加密密钥 | `DATA_DIR/.salt` + 进程内派生 | Fernet 密钥（见下） |

**结论**：在「单进程 + 多线程」模型下，上述状态在进程内完全一致，行为正确。

## 水平扩展的限制（请勿跨进程共享状态）

上述状态**仅在同一进程内**保持一致。如果你运行：

- 多个 OS 进程（例如用 Gunicorn / uWSGI 起多个 worker，或 `WEB_CONCURRENCY>1`）；
- 多个容器副本（Kubernetes / Docker Swarm / 多 `docker run` 实例）；
- 同一 `DATA_DIR` 被多个实例挂载；

则每个进程 / 副本会各自持有**独立的内存状态**，导致：

- 在一个实例登录拿到的 Token，在另一个实例上无效（被拒绝）；
- 登录频率限制、调度器各自独立，失去全局限速与去重；
- SSE 推送只到达连接到该实例的客户端；
- 配置 / 历史缓存可能在实例间不一致（最终以 `DATA_DIR` 落盘文件为准）。

## 运维建议

1. **单实例部署**：生产环境保持 **单副本 / 单进程**。Docker 默认单容器即满足。
2. **DATA_DIR 必须持久化且独占**：将 `DATA_DIR`（容器内默认 `/data/douban-history`）挂载为独立 volume，
   并**确保同一挂载只被一个实例使用**。请勿让多个副本共用同一 `DATA_DIR`。
3. **反向代理**：如需在前面加 Nginx / Cloudflare Tunnel，请仅在可信反代后才将
   `TRUST_PROXY=true`（见 README「环境变量」），否则攻击者可伪造 `X-Forwarded-For` 绕过 IP 限速。
4. **重启安全**：重启单个实例是安全的——Token 等内存状态会清空（用户需重新登录），
   但 `DATA_DIR` 中的配置与历史会保留。
5. **多副本需求（未来工作）**：真正的高可用 / 水平扩展需要将上述进程内状态外置为
   共享存储（如 Redis 保存 Token / 限速；数据库 / 消息队列协调调度；对象存储共享媒体等）。
   这属于较大的架构重构，**当前版本不在范围内**，请勿在单实例之外运行多个副本。

## 加密密钥注意事项

敏感字段（`qas_token` / `auth_pass` / `douban_cookie`）使用 Fernet 加密，密钥派生自
`DATA_DIR/.salt`（首次启动随机生成，权限 `0600`）。该密钥**不是环境变量**。请务必：

- 持久化并备份 `DATA_DIR/.salt`；
- 若更换挂载 / 重装导致 `.salt` 丢失或变化，已加密字段将**无法解密**（需重新在设置页填写）。

详见 README「数据目录」与「环境变量」章节。
