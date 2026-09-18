# openlist.py — OpenList（AList 分支）客户端：媒体库目录浏览 / 删除 / 任务对账
#
# 背景（issue #27 追问）：
#   用户问「可以通过调用 openlist 进行管理吗，但我同时需要管理夸克自动转存的自动任务
#   条目的需求。当前通过夸克自动转存的媒体库访问速度太慢了」。
#
# 拆成两件事：
#   1. 「慢」的根因不是 DBAuto 的渲染，而是**目录数据要经 QAS 向夸克实时拉取**
#      （链路：浏览器 → DBAuto → QAS → 夸克开放接口）。QAS 的
#      /get_savepath_detail 每次都真的去夸克列目录，没有任何缓存，
#      目录一大就要等几秒。引入 OpenList 后，目录浏览改为读 OpenList 自己的
#      文件系统视图（OpenList 对夸克驱动有列表缓存，且支持 Refresh 显式绕过），
#      并且在多次浏览之间复用登录 token，省掉每请求一轮会话成本。
#   2. 「同时管理 QAS 自动任务条目」的诉求不能丢：任务条目的增删仍在 QAS 侧
#      （media_library.delete_tasks / update_tasklist），OpenList 只负责
#      **文件侧**的真实情况。有了文件侧真相，才能做「任务对账」：
#      哪些任务在网盘上已经找不到对应内容（失效/空转）、哪些目录有内容却没有任务。
#
# 为什么不用 /api/fs/get 取下载直链来做「内容存在性」判断：
#   取直链会触发夸克侧签名与限流，代价高且可能被风控。目录浏览（fs/list）
#   已经能确认「这个目录下有什么」，对账只需要这个粒度。
#
# 设计约束（与 media_library 同口径）：
#   - 读接口走收窄超时（快速失败优于长时间转圈）；
#   - 删除是破坏性操作，保持完整等待预算，且不做静默降级；
#   - 未配置 OpenList 时所有函数均可用（返回 unconfigured），
#     媒体库自动回落到 QAS 直连，不会因为「没配 OpenList」而不可用。
#
# 鉴权优先级（issue #27 追问：「可以把 OpenList 的验证方式变成 OpenList 令牌验证吗」）：
#   1. **令牌优先**：配了 openlist_token 就把它原样放进 Authorization 直发，
#      完全不发登录请求（少一轮往返，且令牌不受「账号密码改没改」影响）；
#   2. 账号 + 密码：未配令牌时用 /api/auth/login 换 token（结果进进程内缓存）；
#   3. 游客模式：都没配就不带 Authorization，能否浏览交给 OpenList 侧权限裁决。
#
# 这里要说清一个容易误解的点：**令牌不会带来额外权限**。OpenList 的
# Authorization 就是登录换来的那个 JWT（/api/me 自省时同样是 Bearer），
# 它只回答「你是谁」。列表的 write 字段与删除是否放行，取决于该用户的
# 角色与目录权限 —— 也就是说：用 admin 的令牌 = admin 的权限，
# 与「用 admin 账号密码登录」完全等价；游客令牌仍只是游客。
# 令牌的真实收益是**少一次登录往返**，以及不受 OpenList 侧改密影响。
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config import ConfigManager

# 读接口超时：目录浏览是「点一下就该出结果」的交互，宁可快速失败让用户重试。
OPENLIST_READ_TIMEOUT = 8

# 写接口（删除）超时：破坏性操作需要拿到完整响应，不为了快而糊弄结果。
OPENLIST_WRITE_TIMEOUT = 15

# 单次删除的条目上限（与 media_library.MAX_DELETE_BATCH 一致，避免批量删库误操作）
MAX_REMOVE_BATCH = 50

# 对账时最多下钻的目录层数 / 目录数：防止网盘根目录下有几千个目录时
# 对账本身变成一次「全盘扫描」（那正是用户抱怨的「慢」的另一种形态）。
RECONCILE_MAX_DEPTH = 2
RECONCILE_MAX_DIRS = 40

# 登录 token 复用：OpenList 的 token 默认 48 小时有效（conf.TokenExpiresIn）。
# 这里只在进程内缓存，预留 10 分钟余量；登录失败不影响其它链路。
# 仅「账号密码」这条路径会用到它 —— 手填令牌是直发的，不经过登录。
_TOKEN_SKEW = 600
_token_lock = threading.Lock()
_token_cache = {"token": "", "expire_at": 0.0, "base": ""}

# 鉴权方式缓存：与凭据指纹绑定。
# 为什么不能只按 base 缓存：用户从「账号密码」改成「令牌」时 base 没变，
# 若还吃着旧的登录 token，改了配置却毫无效果（现象是「保存了，但没生效」）。
# 把 (令牌, 账号, 密码) 的哈希折进缓存键，任一改动即自然失效，无需重启。
_AUTH_CACHE_TTL = 120
_auth_lock = threading.Lock()
_auth_cache = {"fingerprint": "", "mode": "", "expire_at": 0.0, "base": "", "user": ""}

# 鉴权方式（用于把「谁在回答 + 用什么身份回答」如实告诉前端）
AUTH_TOKEN = "token"
AUTH_PASSWORD = "password"
AUTH_GUEST = "guest"
AUTH_MIXED = "mixed"


class OpenListError(Exception):
    """OpenList 调用失败（配置缺失 / 网络异常 / 业务错误统一收敛）。"""

    def __init__(self, message, unconfigured=False):
        super().__init__(message)
        self.unconfigured = unconfigured


def _cfg():
    """读取 OpenList 配置（地址 / 令牌 / 账号 / 密码）。

    返回 (base, token, user, password)；未配置地址时 base 为空字符串。

    密码与令牌都可能在进程内被 ConfigManager 解密后带出，这里不做任何
    加工（令牌里的 `.` / `-` / `_` 必须原样保留，任何 strip 都不能动中间字符）。
    """
    try:
        c = ConfigManager.get_instance().get_config()
    except Exception:
        c = {}
    base = (c.get("openlist") or "").strip()
    if base and not base.startswith(("http://", "https://")):
        base = "http://" + base
    token = (c.get("openlist_token") or "").strip()
    user = (c.get("openlist_user") or "").strip()
    password = c.get("openlist_pass") or ""
    return base.rstrip("/"), token, user, password


def _fingerprint(base, token, user, password):
    """凭据指纹：鉴权缓存只对「完全没变的凭据」生效。

    用哈希而不是明文：缓存会经 _auth_status() 暴露到 API 响应里，
    绝不能有任何路径把口令/令牌回显给前端。
    """
    import hashlib
    raw = "\u0000".join([base or "", token or "", user or "", password or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _credential_mode(token, user, password):
    """按「配置里有什么」推断鉴权方式（不代表服务端是否真的接受它）。"""
    has_user = bool(user)
    if token and has_user:
        return AUTH_MIXED
    if token:
        return AUTH_TOKEN
    if has_user or password:
        return AUTH_PASSWORD
    return AUTH_GUEST


_MODE_LABELS = {
    AUTH_TOKEN: "OpenList 令牌",
    AUTH_PASSWORD: "账号密码登录",
    AUTH_GUEST: "游客模式",
    AUTH_MIXED: "令牌优先（账号密码仅作兜底）",
}


def _root_prefix():
    """OpenList 侧的挂载前缀（对应「网盘根」的那个目录）。

    为什么必须有这个映射（issue #27 追问「为什么我配置了 OpenList，但还是回落」）：
      DBAuto 的媒体库路径来自 QAS 的 savepath（`/影视/电影`），而 OpenList 只认
      自己的挂载点（`/夸克/影视/电影`）。两者不一致时 `/api/fs/list` 会稳定返回
      500（storage not found / object not found），于是**每一次浏览都回落 QAS** ——
      用户看到的现象正是「配了 OpenList 却仍然回落」。

      这个报错还极容易被误判成「OpenList 挂了」，因为它和网络故障表现一样
      （都只是 `success:false`）。所以这里显式声明前缀并自动拼接。

    返回值形如 `""`（未配置，表示两边路径同构）或 `"/夸克"`（带前导斜杠、无尾斜杠）。
    """
    try:
        c = ConfigManager.get_instance().get_config()
    except Exception:
        c = {}
    raw = (c.get("openlist_root") or "").strip()
    if not raw or raw == "/":
        return ""
    if not raw.startswith("/"):
        raw = "/" + raw
    while len(raw) > 1 and raw.endswith("/"):
        raw = raw[:-1]
    if ".." in raw.split("/"):
        return ""
    return raw


def to_openlist_path(path):
    """把「网盘路径」（QAS savepath / 媒体库根）翻译成 OpenList 路径。

    双向里的「去程」：`/影视/电影` + 前缀 `/夸克` → `/夸克/影视/电影`。
    前缀为空时原样返回（两边同构）。
    """
    p = "/" + str(path or "/").strip().lstrip("/")
    if p != "/":
        p = p.rstrip("/") or "/"
    prefix = _root_prefix()
    if not prefix:
        return p
    if p == "/":
        return prefix
    return prefix + p


def from_openlist_path(path):
    """把 OpenList 路径翻译回「网盘路径」（反向，供提示与对账使用）。"""
    p = "/" + str(path or "/").strip().lstrip("/")
    prefix = _root_prefix()
    if prefix and (p == prefix or p.startswith(prefix + "/")):
        p = p[len(prefix):] or "/"
    if p != "/":
        p = p.rstrip("/") or "/"
    return p


def is_configured():
    """OpenList 是否已配置（地址非空即视为已配置）。

    凭据全部留空时按 OpenList 的「游客模式」处理：部分部署允许匿名列目录，
    这种情况下浏览可用、删除会返回 403（由 OpenList 自己裁决权限）。
    若设置了强制登录（conf.GuestLogin=false 覆盖含 WebDAV），游客模式
    会在自检里直接报 401，用户能在设置页看到「需填令牌或账号密码」。
    """
    base, _, _, _ = _cfg()
    return bool(base)


def _resp_code(payload):
    """取 OpenList 响应里的业务码。

    踩坑：OpenList 的**成功**响应也是 `{"code":200,...}`，所以不能用
    `payload.get("code")` 的真假来判错 —— 那会把每一次成功都当失败
    （本模块自测时正是这样把「列目录成功」全报成「错误 200」的）。
    """
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.isdigit():
        return int(code)
    return None


def _resp_message(payload, status):
    """从响应体里取可读原因，取不到再退回到 HTTP 状态码。

    为什么必须这么做：OpenList 的业务错误（密码错误 400、令牌无效 401、
    权限不足 403）**都带描述性 JSON**，直接抛 urllib 的
    「HTTP Error 400: Bad Request」会把真正的原因吃掉。
    """
    msg = (payload or {}).get("message") if isinstance(payload, dict) else None
    if msg:
        return str(msg)
    if payload is None:
        return "HTTP {}（OpenList 未返回 JSON，请确认地址指向 OpenList 而非其它服务）".format(status)
    return "HTTP {}".format(status)


def _request(url, data=None, method=None, timeout=None, token=""):
    """最小 HTTP 封装：返回 (status, json|None)。

    不抛超时/连接类异常之外的东西 —— 这里刻意把 HTTPError 也归一化成
    (status, payload)，因为 OpenList 的业务错误都带描述性 JSON
    （见 _resp_message）。JSON 解析失败时 payload 为 None，由调用方兜底。

    token 为空串时**不带** Authorization 头（游客模式）；非空则原样放入
    （OpenList 认的是裸 JWT，不加 `Bearer ` 前缀）。
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = token
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method=method or ("POST" if body else "GET"))
    status = 200
    try:
        resp = urllib.request.urlopen(req, timeout=timeout or OPENLIST_READ_TIMEOUT)
    except urllib.error.HTTPError as e:
        status, resp = e.code, e
    with resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def _resolve_auth(force=False):
    """决定本次请求用什么凭据。

    返回 (mode, credential, user)：
      - mode=token    → credential 是手填令牌，**直发**，不发登录请求；
      - mode=password → credential 是登录换来的 token（进程内复用）；
      - mode=guest    → credential 为空串，不带 Authorization。

    令牌优先是刻意的：它少一轮登录往返，且不受 OpenList 侧改密 / 重启影响。
    凭据留空时的顺序是「令牌 → 账号 → 密码」：只要有一项能用来鉴权，
    就不按游客模式发请求 —— 用户配了东西却被当游客，会得到莫名的 401。
    """
    base, token, user, password = _cfg()
    if not base:
        raise OpenListError("未配置 OpenList 地址", unconfigured=True)
    if token:
        return AUTH_TOKEN, token, ""

    now = time.time()
    if not user and not password:
        # 游客模式：不带 Authorization，是否放行交给 OpenList
        return AUTH_GUEST, "", ""

    fingerprint = _fingerprint(base, token, user, password)
    with _auth_lock:
        if (not force and _auth_cache["fingerprint"] == fingerprint
                and _auth_cache["base"] == base and now < _auth_cache["expire_at"]):
            return _auth_cache["mode"], _auth_cache["token"], _auth_cache["user"]

    login_token = _login(base, user, password)
    with _auth_lock:
        _auth_cache.update({"fingerprint": fingerprint, "mode": AUTH_PASSWORD,
                            "token": login_token, "base": base,
                            "expire_at": now + _AUTH_CACHE_TTL, "user": user})
    return AUTH_PASSWORD, login_token, user


def _login(base, user, password):
    """用账号密码登录 OpenList，返回 token 字符串（进程内缓存复用）。

    缓存键含 base 地址：用户改了地址后旧 token 不再被复用。
    """
    now = time.time()
    cache_key = "{}|{}|{}".format(base, user, password or "")
    with _token_lock:
        if (_token_cache["token"] and _token_cache["key"] == cache_key
                and now < _token_cache["expire_at"]):
            return _token_cache["token"]

    url = "{}/api/auth/login".format(base)
    try:
        # 注意：OpenList 的 /api/auth/login 收**明文**密码，服务端自己做 StaticHash；
        # 不要先做 sha256（那是 /api/auth/login/hash 的语义，两者不能混用）。
        status, payload = _request(url, {"username": user, "password": password},
                                   timeout=OPENLIST_READ_TIMEOUT)
    except Exception as e:
        raise OpenListError("OpenList 登录失败: {}".format(e))
    token = ((payload or {}).get("data") or {}).get("token") or ""
    if not token:
        raise OpenListError("OpenList 登录失败: {}".format(_resp_message(payload, status)))
    with _token_lock:
        _token_cache.update({"token": token, "key": cache_key, "base": base,
                             "expire_at": now + 3600})
    return token


def reset_token_cache():
    """清空登录 token / 鉴权方式缓存（改配置后调用；测试也用）。"""
    with _token_lock:
        _token_cache.update({"token": "", "key": "", "base": "", "expire_at": 0.0})
    with _auth_lock:
        _auth_cache.update({"fingerprint": "", "mode": "", "expire_at": 0.0,
                            "base": "", "user": ""})


def _api(path, data=None, method=None, timeout=None, retry_auth=True):
    """带一次鉴权重试的 OpenList 调用。

    重试的边界（这里刻意与「账号密码」路径不同）：
      - 用**账号密码**时 401 通常是登录 token 到期/服务端重启 → 丢掉缓存重登一次，
        用户不该看到莫名的「读取失败」；
      - 用**手填令牌**时 401 只可能是令牌本身不对（过期 / 撤销 / 抄漏）→
        重发同一个令牌只是白等一轮，必须**如实报错**，并在消息里点明
        「令牌」二字，用户才知道要去哪儿改。
    """
    base, _, _, _ = _cfg()
    if not base:
        raise OpenListError("未配置 OpenList 地址", unconfigured=True)
    mode, credential, _user = _resolve_auth()
    url = base + path
    try:
        status, payload = _request(url, data, method=method,
                                   timeout=timeout or OPENLIST_READ_TIMEOUT,
                                   token=credential)
    except Exception as e:
        raise OpenListError("OpenList 请求失败: {}".format(e))
    if status == 401 and retry_auth and mode == AUTH_PASSWORD:
        reset_token_cache()
        mode, credential, _user = _resolve_auth(force=True)
        if credential:
            status, payload = _request(url, data, method=method,
                                       timeout=timeout or OPENLIST_READ_TIMEOUT,
                                       token=credential)
    if status == 401 and mode == AUTH_TOKEN:
        raise OpenListError(
            "OpenList 令牌无效或已过期（HTTP 401）：{}。请到 OpenList 后台重新获取令牌后"
            "在设置页更新".format(_resp_message(payload, status)))
    if payload is None:
        raise OpenListError("OpenList 返回了非 JSON 响应（HTTP {}）".format(status))
    code = _resp_code(payload)
    if status >= 400 or (code is not None and code != 200):
        raise OpenListError("OpenList 错误 {}: {}".format(code or status,
                                                          _resp_message(payload, status)))
    return payload.get("data")


def ping():
    """探活 + 配置自检：返回 {"success", "message", "user", "mode"}，不抛异常。

    /api/me 走的是与目录浏览同一套鉴权解析，因此这里验证的就是
    「媒体库目录会以什么身份去读」——自检失败 = 媒体库会回落 QAS，
    不应等到用户翻媒体库才发现。
    """
    if not is_configured():
        return {"success": False, "message": "未配置 OpenList 地址", "user": "",
                "mode": AUTH_GUEST, "mode_label": _MODE_LABELS[AUTH_GUEST]}
    try:
        mode, _cred, _u = _resolve_auth()
    except OpenListError as e:
        return {"success": False, "message": str(e), "user": "",
                "mode": AUTH_GUEST, "mode_label": _MODE_LABELS[AUTH_GUEST]}
    try:
        data = _api("/api/me", timeout=OPENLIST_READ_TIMEOUT)
    except OpenListError as e:
        return {"success": False, "message": str(e), "user": "",
                "mode": mode, "mode_label": _MODE_LABELS.get(mode, "")}
    user = ((data or {}).get("username") or "").strip()
    if not user:
        # 游客模式下 /api/me 可能返回空用户名：如实说明「以游客身份访问」，
        # 而不是显示成「登录成功但用户为空」（后者会被误读成配错了）。
        label = "游客（未提供凭据）" if mode == AUTH_GUEST else ""
        if mode == AUTH_GUEST:
            return {"success": True, "message": "", "user": "", "mode": mode,
                    "mode_label": label}
    return {"success": True, "message": "", "user": user,
            "mode": mode, "mode_label": _MODE_LABELS.get(mode, "")}


def auth_status():
    """鉴权自检（不联网）：返回配置里用的是哪种鉴权、凭据是否齐备。

    与 ping 的区别：ping 证明「服务端真的接受了这套凭据」，auth_status 只说明
    「配置里有什么」。两者都要有 —— 用户问「我到底在用令牌还是账号密码」
    时不该依赖网络可达。
    """
    base, token, user, password = _cfg()
    mode = _credential_mode(token, user, password)
    return {
        "mode": mode,
        "mode_label": _MODE_LABELS[mode],
        "token_set": bool(token),
        "user_set": bool(user),
        "password_set": bool(password),
    }


def self_check(browse_path="/"):
    """完整自检：登录 + **真的列一次目录**。

    为什么必须列目录（issue #27 追问「为什么我配置了 OpenList，但还是回落」）：
      只 ping `/api/me` 时，设置页会显示「OpenList 可用，目录将由其回答」，
      但只要 OpenList 的挂载点与网盘路径不一致（或该目录未挂载），
      **每一次浏览都会回落 QAS**。用户因此在「自检说正常」与
      「实际用着 QAS」之间彻底失去线索 —— 这是本次问题的关键一环。

    返回 {
      "success": 登录+列目录都成功,
      "message": 不可用时的具体原因（可直接展示给用户）,
      "user", "browse_path": 实际尝试的网盘路径,
      "openlist_path": 实际请求的 OpenList 路径（体现前缀映射）,
      "count": 列到的条目数, "stage": 失败发生在哪一步,
    }
    """
    info = {"success": False, "message": "", "user": "", "count": 0,
            "browse_path": str(browse_path or "/"), "openlist_path": "",
            "stage": "config"}
    if not is_configured():
        info["message"] = "未配置 OpenList 地址"
        return info
    base = ping()
    info["user"] = base.get("user", "")
    if not base.get("success"):
        info["stage"] = "login"
        info["message"] = base.get("message", "")
        return info
    # 登录通过 → 再真的列一次目录，这才是「媒体库能不能走 OpenList」的判据
    info["stage"] = "list"
    target = info["browse_path"] or "/"
    info["openlist_path"] = to_openlist_path(target)
    res = list_dir(target)
    if not res.get("success"):
        info["message"] = res.get("message") or "OpenList 列目录失败"
        info["path_missing"] = bool(res.get("path_missing"))
        return info
    info["success"] = True
    info["count"] = len(res.get("entries") or [])
    info["stage"] = "ok"
    return info


# OpenList / AList 在「目录或存储不存在」时的典型报错文案。
# 用来把「配置错路径」和「OpenList 真的挂了」区分开 —— 两者都表现为
# success:false，混在一起用户永远查不出原因。
_MISSING_PATH_HINTS = (
    "storage not found", "object not found", "not found", "failed to list objs",
    "failed get objs", "directory not found", "no such file", "not exist",
)


def _looks_like_missing_path(message):
    """判断报错是不是「这个路径在 OpenList 上不存在」。"""
    low = str(message or "").lower()
    return any(h in low for h in _MISSING_PATH_HINTS)


def _norm_path(path):
    """规范化 OpenList 路径：必须绝对、去掉结尾斜杠、禁止上跳。"""
    p = str(path or "/").strip() or "/"
    if not p.startswith("/"):
        p = "/" + p
    if ".." in p.split("/"):
        raise OpenListError("路径不合法: {}".format(path))
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return p


def list_dir(path="/", refresh=False):
    """列出目录（对应 GET /api/fs/list）。

    返回 {"success", "path", "entries":[{name,dir,size,updated_at}], "message"}。
    entries 字段口径与 media_library.list_library 一致（name/dir/size/updated_at），
    这样前端不需要区分数据源 —— 换源只换「谁在回答」，不换数据形状。

    refresh=True 时向 OpenList 索取最新列表（绕过它的目录缓存）；
    默认 False 走缓存，这正是「访问更快」的来源。
    """
    try:
        p = _norm_path(path)
    except OpenListError as e:
        return {"success": False, "path": str(path or "/"), "entries": [],
                "message": str(e), "unconfigured": False}
    if not is_configured():
        return {"success": False, "path": p, "entries": [],
                "message": "未配置 OpenList 地址", "unconfigured": True}
    # 网盘路径 → OpenList 路径（挂载前缀映射，见 _root_prefix 的注释）。
    # 不做这一步时，只要 OpenList 的挂载点不是「网盘根」，每一次浏览都会
    # 因「目录不存在」而回落 QAS —— 这正是「配了 OpenList 却仍回落」的主因。
    api_path = to_openlist_path(p)
    try:
        data = _api("/api/fs/list", {"path": api_path, "refresh": bool(refresh),
                                     "page": 1, "per_page": 0},
                    timeout=OPENLIST_READ_TIMEOUT)
    except OpenListError as e:
        msg = str(e)
        if e.unconfigured or not _looks_like_missing_path(msg):
            return {"success": False, "path": p, "entries": [],
                    "message": msg, "unconfigured": e.unconfigured}
        # 路径不存在是**配置问题**，不是「OpenList 挂了」。把它说清楚，
        # 否则用户只会看到「回落 QAS」，永远不知道该去配挂载前缀。
        msg = ("OpenList 上找不到目录 {}{}（原始报错：{}）。"
               "若 OpenList 的挂载点不是网盘根，请在「设置 → OpenList 路径前缀」"
               "填写 OpenList 上对应网盘根的目录（例如 /夸克）。".format(
                   api_path,
                   "" if _root_prefix() else "（当前未配置路径前缀）",
                   msg))
        return {"success": False, "path": p, "entries": [],
                "message": msg, "unconfigured": False, "path_missing": True,
                "fallback_hint": "path_prefix" if _looks_like_missing_path(str(e)) else ""}
    content = (data or {}).get("content") or []
    entries = []
    for it in content:
        if not isinstance(it, dict):
            continue
        entries.append({
            "name": it.get("name", ""),
            "dir": bool(it.get("is_dir")),
            "size": it.get("size", 0),
            "updated_at": _to_epoch(it.get("modified")),
        })
    return {"success": True, "path": p, "entries": entries, "message": "",
            "write": bool((data or {}).get("write")), "unconfigured": False}


def _to_epoch(value):
    """把 OpenList 的 RFC3339 时间串转成秒级时间戳（前端 fmtTs 口径）。

    解析失败返回 0（前端显示「—」），绝不抛异常 —— 时间只影响展示。
    """
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip()
    if not s:
        return 0
    try:
        # 形如 2025-09-17T20:04:11+08:00 / 2025-09-17T12:04:11Z
        cleaned = s.replace("Z", "+0000")
        if cleaned[-5] in "+-" and cleaned[-3] != ":":
            cleaned = cleaned[:-2] + ":" + cleaned[-2:]
        from datetime import datetime
        return int(datetime.fromisoformat(cleaned).timestamp())
    except Exception:
        try:
            from datetime import datetime
            return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").timestamp())
        except Exception:
            return 0


def remove(names, dir_path="/"):
    """删除目录下的若干条目（对应 POST /api/fs/remove）。

    names 为**条目名列表**（不含父目录），与实际 OpenList WebUI 的调用一致。
    返回 {"success", "deleted", "failed", "results", "message"}。

    语义与 media_library.delete_entries 对齐：只有「全部成功」才算 success，
    部分成功如实回报 deleted/failed —— 不允许把「删了一半」说成成功。
    """
    if not is_configured():
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "未配置 OpenList 地址"}
    try:
        base_dir = _norm_path(dir_path)
    except OpenListError as e:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": str(e)}
    clean = [str(n) for n in (names or []) if str(n or "").strip()]
    if not clean:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "未指定要删除的条目"}
    if len(clean) > MAX_REMOVE_BATCH:
        return {"success": False, "deleted": 0, "failed": 0, "results": [],
                "message": "单次最多删除 {} 条".format(MAX_REMOVE_BATCH)}
    for n in clean:
        if "/" in n or "\\" in n or n in (".", ".."):
            return {"success": False, "deleted": 0, "failed": 0, "results": [],
                    "message": "条目名不合法: {}".format(n)}
    try:
        # 与浏览同源：目录同样要按挂载前缀翻译，否则「能看到却删不掉」。
        _api("/api/fs/remove", {"dir": to_openlist_path(base_dir), "names": clean},
             timeout=OPENLIST_WRITE_TIMEOUT)
    except OpenListError as e:
        # 整批失败：OpenList 的 /fs/remove 遇到第一条失败即中止，
        # 无法区分「删了几条」，因此拒绝宣称任何成功（与 QAS 删除同口径）。
        return {"success": False, "deleted": 0, "failed": len(clean), "results": [],
                "message": str(e)}
    results = [{"name": n, "ok": True, "message": "已删除（移入回收站）"} for n in clean]
    return {"success": True, "deleted": len(clean), "failed": 0,
            "results": results, "message": ""}


def _entry_names(entries):
    """从目录条目里取出条目名（供对账用）。"""
    return {str(e.get("name") or "").strip() for e in (entries or []) if e.get("name")}


def _match_task_dir(task, present):
    """判断某个 QAS 任务的内容是否出现在给定目录的条目里。

    返回 (matched_bool, matched_name)。匹配规则刻意保守：

      1. 任务名**精确命中**目录/文件名 → 命中；
      2. 任务名与条目名的**包含关系**（≥2 字）→ 命中。QAS 的 taskname 常见
         形态是「[已完结]作品名」「作品名 全12集」，而网盘目录名是作品名，
         所以必须允许包含匹配，否则真实命中会被判成「失效」；
      3. 其余一律判为「未命中」。

    ⚠️ 这里**刻意不拿 savepath 的最后一段去匹配**（初始版本犯过这个错，实测抓出）：
    savepath 末尾是「容器目录名」（如 `/影视/电影` 的 `电影`），拿它去比父目录
    `/影视` 的条目，会把 `电影` 这个目录本身当成命中 —— 于是**任何** savepath
    以 `/影视/电影` 结尾的任务都恒被判为「有效」，失效任务一条都报不出来。
    自测用例「任务名在网盘不存在(失效)」当时返回 checked=1 / missing=[] 即是此因。
    """
    name = str(task.get("taskname") or "").strip()
    if not name:
        return False, ""
    if name in present:
        return True, name
    for p in present:
        if not p:
            continue
        if len(name) >= 2 and len(p) >= 2 and (name in p or p in name):
            return True, p
    return False, ""


def _task_savepath_dir(task):
    """提取任务的目标父目录（用于去 OpenList 上核对）。

    QAS 的 savepath 有两种常见形态：
      - 目录型：`/影视/电影`（转存进去后是 `/影视/电影/<作品名>`）
      - 作品目录型：`/影视/电影/作品名`
    两种都要能核对，所以这里返回「父目录」与「自身」两个候选。
    """
    sp = str(task.get("savepath") or "").strip()
    if not sp.startswith("/"):
        sp = "/" + sp
    sp = sp.rstrip("/") or "/"
    parent = sp.rsplit("/", 1)[0] or "/"
    return sp, parent


def reconcile_tasks(tasks, root="/", max_depth=RECONCILE_MAX_DEPTH,
                    max_dirs=RECONCILE_MAX_DIRS):
    """QAS 任务 ↔ 网盘实际内容 对账（issue #27 追问的「同时管理任务条目」）。

    只读操作，不修改任何东西：结论用于在媒体库页给用户提示
    「这条任务在网盘上已经找不到对应内容了」，避免任务每天空转。

    返回 {
      "success", "checked", "missing": [{taskname, savepath, reason}],
      "unverified": [{taskname, savepath}],
      "orphans": [{dir, name}], "scanned_dirs": n, "truncated": bool, "message"
    }
      - missing：**已核对且确实对不上**的任务（目录不存在 / 目录里没同名条目）；
      - unverified：savepath 不在本次扫描范围内的任务 —— 单独列出，不混进
        missing。把「没扫到」当「不存在」是这类对账功能最常见的误报来源；
      - orphans：网盘上有目录但没有任何任务指向它（这类只提示，不删）。

    扫描范围刻意受限（max_depth / max_dirs）：网盘根下可能有上千个目录，
    「对账」不该变成一次全盘扫描 —— 那正是用户抱怨的「慢」。
    """
    if not is_configured():
        return {"success": False, "checked": 0, "missing": [], "unverified": [],
                "orphans": [], "scanned_dirs": 0, "truncated": False,
                "message": "未配置 OpenList 地址"}
    try:
        base = _norm_path(root)
    except OpenListError as e:
        return {"success": False, "checked": 0, "missing": [], "unverified": [],
                "orphans": [], "scanned_dirs": 0, "truncated": False,
                "message": str(e)}

    # 1. 收集扫描范围内的目录列表（广度优先，受层数与目录数双重约束）
    dirs = [(base, _entry_names(list_dir(base).get("entries")))]
    frontier = [base]
    depth = 0
    truncated = False
    while frontier and depth < max(0, int(max_depth or 1)):
        depth += 1
        nxt = []
        for p in frontier:
            if len(dirs) >= max_dirs:
                truncated = True
                break
            res = list_dir(p)
            if not res.get("success"):
                continue
            sub = [("{}/{}".format(p.rstrip("/"), e["name"])) for e in res.get("entries", [])
                   if e.get("dir")]
            for s in sub:
                if len(dirs) >= max_dirs:
                    truncated = True
                    break
                nxt.append(s)
                dirs.append((s, _entry_names(list_dir(s).get("entries"))))
        frontier = nxt

    # 2. 逐条任务核对
    #
    # 语义（初始版本在这里错了两次，都是实测抓出来的）：
    #   要核对的是「任务的内容出现在哪里」，因此：
    #     - savepath 是**内容所在目录**（`/影视/电影`）→ 看它**自己的条目**里
    #       有没有任务对应的作品。看它的父目录等于没核对：父目录里的 `电影`
    #       这个目录名一定会和 savepath 末段撞上，于是任何任务都恒判为有效
    #       （自测用例「任务名在网盘不存在」当时返回 checked=1 / missing=[]）。
    #     - savepath 是**作品目录**（`/影视/电影/作品名`）→ 看它的父目录里
    #       有没有同名条目。
    #     - savepath 已经不存在了（父目录有条目、其中没有这个名字）→
    #       「保存目录已被删除或改名」，这是最典型的任务失效，必须报出来。
    #     - savepath 的父目录都没扫到 → **未核对**，绝不混进 missing：
    #       把「没扫到」当「不存在」是这类对账功能最大的误报来源。
    present_by_dir = {d: names for d, names in dirs}
    checked = 0
    missing = []
    unverified = []
    claimed = set()
    for t in (tasks or []):
        if not isinstance(t, dict):
            continue
        sp, parent = _task_savepath_dir(t)
        tail = sp.rstrip("/").split("/")[-1]
        if sp in present_by_dir:
            # 目录型 savepath：核对它自己的条目
            checked += 1
            hit_dir, names = sp, present_by_dir[sp]
            if _match_task_dir(t, names)[0]:
                claimed.add(hit_dir)
                continue
            missing.append({"taskname": t.get("taskname", ""),
                            "savepath": t.get("savepath", ""),
                            "reason": "网盘对应目录中未找到同名条目"
                                      "（任务可能已失效或内容被手工删除）"})
            continue
        if parent in present_by_dir:
            # 作品目录型 savepath，或 savepath 已经不存在了
            checked += 1
            pnames = present_by_dir[parent]
            if tail and tail in pnames and _match_task_dir(t, pnames)[0]:
                claimed.add(parent)
                continue
            if tail and tail not in pnames and not _match_task_dir(t, pnames)[0]:
                missing.append({"taskname": t.get("taskname", ""),
                                "savepath": t.get("savepath", ""),
                                "reason": "网盘上找不到该任务的保存目录（目录已被删除或改名）"})
                continue
            missing.append({"taskname": t.get("taskname", ""),
                            "savepath": t.get("savepath", ""),
                            "reason": "网盘对应目录中未找到同名条目"
                                      "（任务可能已失效或内容被手工删除）"})
            continue
        unverified.append({"taskname": t.get("taskname", ""),
                           "savepath": t.get("savepath", "")})

    # 3. 孤立目录：有内容却没有任何任务指向它（只提示，不自动删）
    orphans = []
    for d, names in dirs:
        if d == base or d in claimed or not names:
            continue
        if any(str(t.get("savepath") or "").rstrip("/") == d for t in (tasks or [])
               if isinstance(t, dict)):
            continue
        orphans.append({"dir": d, "name": d.rstrip("/").split("/")[-1]})

    return {"success": True, "checked": checked, "missing": missing,
            "unverified": unverified, "orphans": orphans[:max_dirs],
            "scanned_dirs": len(dirs), "truncated": truncated, "message": ""}
