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
#      并且用配置里的访问令牌直发鉴权，省掉每请求一轮会话成本。
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
# 鉴权方式（issue #47 追问「把 OpenList 用户名密码验证的功能去掉，只保留令牌验证」）：
#   **只有一种**：把 openlist_token 原样放进 Authorization 直发。
#   令牌留空 → 不带 Authorization（游客模式），能否浏览交给 OpenList 侧权限裁决。
#
#   历史上还支持「账号 + 密码换 token」，已按用户要求移除。移除的理由不只是简化：
#     1. 两种凭据并存时，用户必须自己判断「我到底在用哪一种」，
#        而两者的行为差异（多一轮登录往返、改密后失效）在界面上看不见；
#     2. 账号密码必须先换取一次会话凭据，多一条凭据链路就多一份落盘密文
#        与一次明文传输；令牌没有这个问题（它本来就是可撤销的授权凭据）。
#
# 这里要说清一个容易误解的点：**令牌不会带来额外权限**。OpenList 的
# Authorization 就是登录换来的那个 JWT（/api/me 自省时同样是 Bearer），
# 它只回答「你是谁」。列表的 write 字段与删除是否放行，取决于该用户的
# 角色与目录权限 —— 也就是说：用 admin 的令牌 = admin 的权限。
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

# 诊断/自检超时（issue #47）：
# 自检与浏览是**两件事**，不能用同一个预算。
#   - 浏览是交互：慢了就该快速失败让用户重试（否则页面干等）；
#   - 自检是一次性诊断：它的职责是回答「配置到底对不对」。
# 用 8 秒去问一个刚挂载夸克网盘、列表冷启动的 OpenList，得到的
# 「timed out」会被当成「OpenList 不可用 / 配错了」——用户明明填对了
# 每一项（issue #47：「在正确填写账号、用户名、密码。根文件路径还有令牌后
# 依旧存在这个问题」），却永远查不出原因。
# 所以自检单独给足预算：慢 ≠ 错。
OPENLIST_PROBE_TIMEOUT = 30

# 写接口（删除）超时：破坏性操作需要拿到完整响应，不为了快而糊弄结果。
OPENLIST_WRITE_TIMEOUT = 15

# 目录浏览的**短命缓存 / 失败退避**（issue #47：「OpenList 经常回落」）。
#
# 症状：同一台 OpenList、配置全对，浏览媒体库却**经常**落到 QAS（徽标
# 「QAS（OpenList 回落）」），不是每次都回落、但频率很高。
#
# 根因不是配置，而是「浏览路径缺了两块常见机制」：
#   1. **成功结果不缓存**：QAS 侧的目录有 30s 短命缓存，OpenList 侧却每次
#      浏览都实打实打一次网络。夸克驱动的目录列表在**冷启动 / 命中不到
#      OpenList 自身缓存**时会超过交互预算（8s），于是一次超时 = 一次回落；
#      而回落结果又被 QAS 缓存 30s —— 用户看到的就是「明明配好了却经常回落」。
#   2. **失败不退避**：上一次刚超时，下一次请求立刻又以同样预算捅同一个
#      慢接口，几乎必然再次超时 → 回落变成「连续、粘住」的状态。
#
# 所以这里补两块，且**只补浏览路径**（自检仍走 OPENLIST_PROBE_TIMEOUT）：
#   - 成功 → 进 30s 缓存，重复打开 / 切标签 / 返回父目录不再打网络；
#   - 失败 → 进一个短退避窗口，窗口内直接走回落缓存，不再反复干等 8 秒。
# 退避不是「放弃」：窗口很短，过期后自动重新尝试 OpenList（网盘侧恢复即好）。
OPENLIST_CACHE_TTL = 30.0
OPENLIST_CACHE_MAX = 64
OPENLIST_FAIL_BACKOFF = 20.0

# 失败归类（issue #47）：把「为什么失败」从一句含糊的「不可用」里拆出来。
# 不同 KIND 对应**完全不同的用户动作**，混在一起就是让用户乱试：
#   TIMEOUT      → 网络/网盘慢，等一会重试，别去改配置；
#   NETWORK      → 地址/端口不通（或不是 OpenList），去核对地址；
#   AUTH         → 令牌不对（没填或失效），去 OpenList 后台重取；
#   PATH_MISSING → 路径前缀不对（配置问题），去改「路径前缀」；
#   HTTP         → OpenList 自己报错（服务端问题）；
#   UNCONFIGURED → 根本没配地址。
KIND_TIMEOUT = "timeout"
KIND_NETWORK = "network"
KIND_AUTH = "auth"
KIND_PATH_MISSING = "path_missing"
KIND_HTTP = "http"
KIND_UNCONFIGURED = "unconfigured"

# 单次删除的条目上限（与 media_library.MAX_DELETE_BATCH 一致，避免批量删库误操作）
MAX_REMOVE_BATCH = 50

# 对账时最多下钻的目录层数 / 目录数：防止网盘根目录下有几千个目录时
# 对账本身变成一次「全盘扫描」（那正是用户抱怨的「慢」的另一种形态）。
RECONCILE_MAX_DEPTH = 2
RECONCILE_MAX_DIRS = 40

# 鉴权方式（用于把「谁在回答 + 用什么身份回答」如实告诉前端）。
#
# 令牌只剩两种可能，因此不再需要任何进程内凭据缓存：
#   - 令牌是**配置里的常量**，每个请求原样直发，不存在「缓存与新配置不一致」
#     这种最难排查的失效（历史上账号密码路径正是为此引入指纹缓存的）；
#   - 旧的 401 自动重登也随之消失：手填令牌没法「重新登录」，重发同一个令牌
#     只是白等一轮，所以 401 一律如实报「令牌无效或已过期」。
#
# 注意：下面的**目录浏览缓存 / 失败退避与鉴权无关**，因此不受本次收敛影响 ——
# 它按路径缓存「目录内容」，换凭据不代表目录内容变了（改前缀会自然换键）。

# 目录浏览缓存 / 失败退避（issue #47）。
# 键 = OpenList 侧的真实路径（含前缀映射后的结果），与鉴权指纹无关 ——
# 换凭据不代表目录内容变了；但**改路径前缀**会自然换键，不会串数据。
# 存「成功的结果字典」与「最近一次失败的 (kind, message, ts)」两类信息。
_dir_cache_lock = threading.Lock()
_dir_cache = {}          # api_path -> {"ts": float, "data": dict}
_dir_fail = {}           # api_path -> {"ts": float, "kind": str, "message": str, "hint": str}

# 鉴权方式（用于把「谁在回答 + 用什么身份回答」如实告诉前端）
AUTH_TOKEN = "token"
AUTH_GUEST = "guest"


def _is_timeout(exc):
    """判断异常是不是超时。

    urllib 的超时异常是 `socket.timeout`（Python 3.10+ 起等于 `TimeoutError`），
    也常被包成 `URLError(reason=...)`；这里按文案兜底，避免不同 CPython 版本 /
    SSL 实现的差异把「超时」判成「网络不通」——那会把用户从「等一会重试」
    引到「去改地址」，方向完全错了。
    """
    import socket
    seen = []
    cur = exc
    for _ in range(4):
        if cur is None:
            break
        seen.append(cur)
        cur = getattr(cur, "reason", None) or getattr(cur, "__cause__", None)
    for e in seen:
        if isinstance(e, (socket.timeout, TimeoutError)):
            return True
        low = str(e).lower()
        if "timed out" in low or "timeout" in low:
            return True
    return False


class OpenListError(Exception):
    """OpenList 调用失败（配置缺失 / 网络异常 / 业务错误统一收敛）。

    kind（issue #47）：见上面 KIND_* —— 调用方据此给出**可执行**的提示。
    """

    def __init__(self, message, unconfigured=False, kind=""):
        super().__init__(message)
        self.unconfigured = unconfigured
        self.kind = kind or (KIND_UNCONFIGURED if unconfigured else KIND_HTTP)


def _cfg():
    """读取 OpenList 配置（地址 / 令牌）。

    返回 (base, token)；未配置地址时 base 为空字符串。

    令牌可能在进程内被 ConfigManager 解密后带出，这里不做任何加工
    （令牌里的 `.` / `-` / `_` 必须原样保留，任何 strip 都不能动中间字符）。
    """
    try:
        c = ConfigManager.get_instance().get_config()
    except Exception:
        c = {}
    base = (c.get("openlist") or "").strip()
    if base and not base.startswith(("http://", "https://")):
        base = "http://" + base
    token = (c.get("openlist_token") or "").strip()
    return base.rstrip("/"), token


def _conn_kind(exc):
    """把传输层异常归类成 KIND_TIMEOUT / KIND_NETWORK。

    这个区分不是「为了更细」，而是**用户动作完全不同**：
      - 超时：OpenList 还活着，只是这次慢（冷启动 / 网盘侧限流）→ 等一会重试；
      - 不通：地址或端口不对（或根本不是 OpenList）→ 去改地址。
    把超时报成「不可用」，用户会把配置翻个底朝天也找不到问题（issue #47）。
    """
    return KIND_TIMEOUT if _is_timeout(exc) else KIND_NETWORK


def _conn_message(prefix, exc):
    """传输层失败的可读文案（超时要显式点名「超时」，不要只说「失败」）。"""
    if _is_timeout(exc):
        return ("{}: 请求超时（{}）。OpenList 有响应但太慢 —— 这通常不是配置错误，"
                "多半是它正在等网盘（如夸克）返回列表。请稍后重试；"
                "若每次浏览都超时，说明该目录在网盘侧的确很慢。".format(prefix, exc))
    return "{}: {}".format(prefix, exc)


def _credential_mode(token):
    """按「配置里有什么」推断鉴权方式（不代表服务端是否真的接受它）。

    只剩两档：有令牌 = 令牌鉴权；没有 = 游客。刻意不留「凭据缺失」这种
    中间态 —— 用户填没填令牌是**可见**的，含糊的第三档只会制造新困惑。
    """
    return AUTH_TOKEN if token else AUTH_GUEST


_MODE_LABELS = {
    AUTH_TOKEN: "OpenList 令牌",
    AUTH_GUEST: "游客模式（未填令牌）",
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

    令牌留空时按 OpenList 的「游客模式」处理：部分部署允许匿名列目录，
    这种情况下浏览可用、删除会返回 403（由 OpenList 自己裁决权限）。
    若设置了强制登录（conf.GuestLogin=false 覆盖含 WebDAV），游客模式
    会在自检里直接报 401，用户能在设置页看到「请填访问令牌」。
    """
    base, _token = _cfg()
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


def _resolve_auth():
    """决定本次请求用什么凭据（唯一一条鉴权路径：令牌直发）。

    返回 (mode, credential)：
      - mode=token → credential 是配置里的令牌，**直发**，不发任何登录请求；
      - mode=guest → credential 为空串，不带 Authorization。

    这里刻意没有任何缓存：令牌是配置常量，直发即最新 —— 用户「保存了却没生效」
    这种最难排查的失效在这条链路上根本不存在（账号密码路径正因如此才需要指纹缓存）。
    """
    base, token = _cfg()
    if not base:
        raise OpenListError("未配置 OpenList 地址", unconfigured=True)
    if token:
        return AUTH_TOKEN, token
    # 游客模式：不带 Authorization，是否放行交给 OpenList
    return AUTH_GUEST, ""


def reset_token_cache():
    """历史接口，保留为无操作。

    令牌鉴权不需要任何进程内缓存（见 _resolve_auth）。这个函数存在的唯一原因
    是**防止外部/脚本里残留的调用直接 NameError** —— 它曾经用于「改配置后
    清空登录 token」，现在没有可清的东西。
    """
    return None


def _api(path, data=None, method=None, timeout=None):
    """OpenList 调用（带凭据直发）。

    这里**没有**「401 自动重试」这条分支了：能自动重试的前提是「凭据可重新获取」，
    而现在是手填令牌 —— 重发同一个令牌只是白等一轮。401 一律如实报错，
    并在消息里点明「令牌」二字，用户才知道要去哪儿改。

    也不再保留「是否重试」这类开关形参：留着它只会让调用方误以为
    「凭据还能重新获取、401 会自动恢复」。
    """
    base, _token = _cfg()
    if not base:
        raise OpenListError("未配置 OpenList 地址", unconfigured=True)
    mode, credential = _resolve_auth()
    url = base + path
    try:
        status, payload = _request(url, data, method=method,
                                   timeout=timeout or OPENLIST_READ_TIMEOUT,
                                   token=credential)
    except Exception as e:
        raise OpenListError(_conn_message("OpenList 请求失败", e), kind=_conn_kind(e))
    if status == 401 and mode == AUTH_TOKEN:
        raise OpenListError(
            "OpenList 令牌无效或已过期（HTTP 401）：{}。请到 OpenList 后台重新获取令牌后"
            "在设置页更新".format(_resp_message(payload, status)), kind=KIND_AUTH)
    if payload is None:
        raise OpenListError("OpenList 返回了非 JSON 响应（HTTP {}）".format(status),
                            kind=KIND_NETWORK)
    if status in (401, 403):
        # 游客被拒 / 权限不足：这是**凭据或权限**问题，不是路径问题。
        raise OpenListError("OpenList 拒绝访问（HTTP {}）：{}".format(
            status, _resp_message(payload, status)), kind=KIND_AUTH)
    code = _resp_code(payload)
    if status >= 400 or (code is not None and code != 200):
        msg = "OpenList 错误 {}: {}".format(code or status, _resp_message(payload, status))
        kind = KIND_PATH_MISSING if _looks_like_missing_path(msg) else KIND_HTTP
        raise OpenListError(msg, kind=kind)
    return payload.get("data")


def ping():
    """探活 + 配置自检：返回 {"success", "message", "user", "mode"}，不抛异常。

    /api/me 走的是与目录浏览同一套鉴权解析，因此这里验证的就是
    「媒体库目录会以什么身份去读」——自检失败 = 媒体库会回落 QAS，
    不应等到用户翻媒体库才发现。
    """
    if not is_configured():
        return {"success": False, "message": "未配置 OpenList 地址", "user": "",
                "kind": KIND_UNCONFIGURED,
                "mode": AUTH_GUEST, "mode_label": _MODE_LABELS[AUTH_GUEST]}
    try:
        mode, _cred = _resolve_auth()
    except OpenListError as e:
        return {"success": False, "message": str(e), "user": "", "kind": e.kind,
                "mode": AUTH_GUEST, "mode_label": _MODE_LABELS[AUTH_GUEST]}
    try:
        # 自检用**诊断预算**：慢 ≠ 错（见 OPENLIST_PROBE_TIMEOUT 的注释）。
        data = _api("/api/me", timeout=OPENLIST_PROBE_TIMEOUT)
    except OpenListError as e:
        return {"success": False, "message": str(e), "user": "", "kind": e.kind,
                "mode": mode, "mode_label": _MODE_LABELS.get(mode, "")}
    user = ((data or {}).get("username") or "").strip()
    if not user:
        # 游客模式下 /api/me 可能返回空用户名：如实说明「以游客身份访问」，
        # 而不是显示成「鉴权通过但用户为空」（后者会被误读成配错了）。
        if mode == AUTH_GUEST:
            return {"success": True, "message": "", "user": "", "mode": mode,
                    "kind": "", "mode_label": "游客（未填令牌）"}
    return {"success": True, "message": "", "user": user,
            "mode": mode, "kind": "", "mode_label": _MODE_LABELS.get(mode, "")}


def auth_status():
    """鉴权自检（不联网）：返回配置里用的是哪种鉴权、令牌是否已填。

    与 ping 的区别：ping 证明「服务端真的接受了这个令牌」，auth_status 只说明
    「配置里有什么」。两者都要有 —— 用户问「我到底在用令牌还是游客」
    时不该依赖网络可达。

    user_set / password_set 恒为 False，保留字段只为兼容既有前端与走查脚本：
    它们曾经表示「账号 / 密码已配置」，现在这两项已不受支持（issue #47 要求
    只保留令牌验证），字段比「悄悄消失导致前端读 undefined」更安全。
    """
    _base, token = _cfg()
    mode = _credential_mode(token)
    return {
        "mode": mode,
        "mode_label": _MODE_LABELS[mode],
        "token_set": bool(token),
        "user_set": False,
        "password_set": False,
    }


def self_check(browse_path="/"):
    """完整自检：鉴权 + **真的列一次目录**。

    为什么必须列目录（issue #27 追问「为什么我配置了 OpenList，但还是回落」）：
      只 ping `/api/me` 时，设置页会显示「OpenList 可用，目录将由其回答」，
      但只要 OpenList 的挂载点与网盘路径不一致（或该目录未挂载），
      **每一次浏览都会回落 QAS**。用户因此在「自检说正常」与
      「实际用着 QAS」之间彻底失去线索 —— 这是本次问题的关键一环。

    返回 {
      "success": 鉴权+列目录都成功,
      "message": 不可用时的具体原因（可直接展示给用户）,
      "user", "browse_path": 实际尝试的网盘路径,
      "openlist_path": 实际请求的 OpenList 路径（体现前缀映射）,
      "count": 列到的条目数, "stage": 失败发生在哪一步,
      "kind": 失败归类（见 KIND_*）—— 前端据此给**可执行**的提示,
      "timeout_seconds": 本次自检实际使用的超时预算,
    }

    stage 取值：config（没配） / login（令牌或连通性，历史档位名，前端文案已收敛）
    / list（列目录） / timeout（列目录超时：慢，不等于配错） / ok。
    """

    info = {"success": False, "message": "", "user": "", "count": 0,
            "browse_path": str(browse_path or "/"), "openlist_path": "",
            "stage": "config", "kind": ""}
    info["timeout_seconds"] = OPENLIST_PROBE_TIMEOUT
    if not is_configured():
        info["message"] = "未配置 OpenList 地址"
        info["kind"] = KIND_UNCONFIGURED
        info["stage"] = "config"
        return info
    base = ping()
    info["user"] = base.get("user", "")
    if not base.get("success"):
        info["stage"] = "login"
        info["message"] = base.get("message", "")
        info["kind"] = base.get("kind") or KIND_HTTP
        return info
    # 鉴权通过 → 再真的列一次目录，这才是「媒体库能不能走 OpenList」的判据
    info["stage"] = "list"
    target = info["browse_path"] or "/"
    info["openlist_path"] = to_openlist_path(target)
    # 自检用诊断预算：用交互预算（8s）会把「网盘目录拉取慢」误判成
    # 「OpenList 不可用」，进而把用户引去改「路径前缀」——方向完全错（issue #47）。
    res = list_dir(target, timeout=OPENLIST_PROBE_TIMEOUT)
    if not res.get("success"):
        info["message"] = res.get("message") or "OpenList 列目录失败"
        info["kind"] = res.get("kind") or KIND_HTTP
        info["path_missing"] = bool(res.get("path_missing"))
        # 超时是**唯一**一类「配置可能是对的、只是这次慢」的失败：
        # 单独标一个 stage，前端才不会提示「多半是路径前缀没填对」。
        if info["kind"] == KIND_TIMEOUT:
            info["stage"] = "timeout"
        return info
    info["success"] = True
    info["kind"] = ""
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


def invalidate_dir_cache(path=None):
    """作废 OpenList 目录缓存 / 失败退避（改配置、删除后调用）。

    path 为 None 时全清 —— 删除会同时影响当前目录与其父目录（条目数变了），
    等回落发生再纠正会让用户先看到一屏旧数据。
    """
    with _dir_cache_lock:
        if path is None:
            _dir_cache.clear()
            _dir_fail.clear()
            return
        api = to_openlist_path(_norm_path(path))
        _dir_cache.pop(api, None)
        _dir_fail.pop(api, None)


def _dir_cache_get(api_path):
    """命中返回数据字典的浅拷贝；过期即清。只缓存**成功**结果。"""
    now = time.time()
    with _dir_cache_lock:
        item = _dir_cache.get(api_path)
        if not item:
            return None
        if now - item["ts"] >= OPENLIST_CACHE_TTL:
            _dir_cache.pop(api_path, None)
            return None
        return dict(item["data"])


def _dir_cache_put(api_path, data):
    with _dir_cache_lock:
        if len(_dir_cache) >= OPENLIST_CACHE_MAX:
            oldest = min(_dir_cache.items(), key=lambda kv: kv[1]["ts"])[0]
            _dir_cache.pop(oldest, None)
        _dir_cache[api_path] = {"ts": time.time(), "data": dict(data)}


def _dir_fail_get(api_path):
    """退避窗口内返回上次失败信息（含 kind），窗口外返回 None。

    看点：**只有失败才退避，且窗口很短**。这不是「记住失败」，而是
    「刚超时的接口别在同一秒里被再捅一次」—— 否则连续三次浏览就是 24 秒
    的干等，用户感知就是「OpenList 老是回落」。
    """
    now = time.time()
    with _dir_cache_lock:
        item = _dir_fail.get(api_path)
        if not item:
            return None
        if now - item["ts"] >= OPENLIST_FAIL_BACKOFF:
            _dir_fail.pop(api_path, None)
            return None
        return dict(item)


def _dir_fail_put(api_path, payload):
    """记录一次失败用于短期退避。路径不存在是**配置问题**，不退避 ——
    用户可能马上就去改前缀，退避反而会让他改完还看不到效果。"""
    if payload.get("kind") == KIND_PATH_MISSING or payload.get("unconfigured"):
        return
    with _dir_cache_lock:
        _dir_fail[api_path] = {"ts": time.time(), "kind": payload.get("kind", ""),
                               "message": payload.get("message", ""),
                               "hint": payload.get("fallback_hint", "")}


def list_dir(path="/", refresh=False, timeout=None):
    """列出目录（对应 GET /api/fs/list）。

    返回 {"success", "path", "entries":[{name,dir,size,updated_at}], "message"}。
    entries 字段口径与 media_library.list_library 一致（name/dir/size/updated_at），
    这样前端不需要区分数据源 —— 换源只换「谁在回答」，不换数据形状。

    refresh=True 时向 OpenList 索取最新列表（绕过它的目录缓存与本地缓存）；
    默认 False 走缓存，这正是「访问更快」的来源，也是「不再经常回落」的
    关键（issue #47：成功结果不缓存时，每次打开都要重新面对一次慢查询）。

    timeout：不传用交互预算 OPENLIST_READ_TIMEOUT；自检会传更长的
    OPENLIST_PROBE_TIMEOUT（见该常量的注释）。

    缓存/退避只对**交互浏览**生效：传了显式 timeout（自检）或 refresh=True
    时一律穿透 —— 自检的职责是如实回答「现在到底通不通」，不能吃缓存。
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
    # 是否走本地缓存 / 退避：仅「交互浏览」启用（默认预算 + 不带 refresh）。
    # 显式 timeout（自检）或 refresh=True 时穿透，保证「验证」永远是实时的。
    cacheable = refresh is not True and timeout is None
    # 退避的**记录**与**使用**是两个条件：
    #   - 使用（读退避窗）：只有交互浏览才吃；refresh / 自检必须现摘现报；
    #   - 记录（写退避窗）：交互浏览**和刷新**都要记。刷新刚超时，紧接着的
    #     普通打开若无退避就会再干等一轮 —— 那正是「老是回落」的体感来源。
    # 自检（显式 timeout）不记：它是一次诊断，不该影响用户后续的浏览。
    remember = timeout is None
    if cacheable:
        hit = _dir_cache_get(api_path)
        if hit is not None:
            # 命中缓存 = 这次**没有**回落。source 由 media_library 侧照常标 openlist。
            hit["cached"] = True
            return hit
        # 刚失败过：短窗口内直接如实返回上次的失败原因，不再干等一轮。
        # 这时 media_library 会照常回落 QAS —— 但原因（kind）是真实的，
        # 且避免了「连续几次浏览 = 连续几次 8 秒等待」的放大。
        backoff = _dir_fail_get(api_path)
        if backoff is not None:
            out = {"success": False, "path": p, "entries": [],
                   "message": backoff.get("message") or "OpenList 读取失败",
                   "unconfigured": False, "kind": backoff.get("kind", ""),
                   "backoff": True}
            if backoff.get("hint"):
                out["fallback_hint"] = backoff["hint"]
            return out
    try:
        data = _api("/api/fs/list", {"path": api_path, "refresh": bool(refresh),
                                     "page": 1, "per_page": 0},
                    timeout=timeout or OPENLIST_READ_TIMEOUT)
    except OpenListError as e:
        msg = str(e)
        # 归类优先看 kind，但**同时**保留文案兜底：kind 是新增信息，
        # 而「能不能认出这是路径问题」关系到用户能不能找到修改入口，
        # 不能因为某条错误路径没带上 kind 就丢掉（旧调用方/自造异常都可能没有）。
        missing = e.kind == KIND_PATH_MISSING or _looks_like_missing_path(msg)
        if e.unconfigured or not missing:
            out = {"success": False, "path": p, "entries": [],
                   "message": msg, "unconfigured": e.unconfigured, "kind": e.kind}
            # 失败 → 记一个短退避，避免「下一次打开又干等 8 秒」
            # （连续几次浏览 = 连续几次超时，用户感知就是「经常回落」）。
            if remember:
                _dir_fail_put(api_path, out)
            return out
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
                "kind": KIND_PATH_MISSING, "fallback_hint": "path_prefix"}
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
    out = {"success": True, "path": p, "entries": entries, "message": "",
           "write": bool((data or {}).get("write")), "unconfigured": False,
           "kind": ""}
    if cacheable:
        # 成功 → 进短命缓存，并清掉该路径的失败记录（已经好了）。
        _dir_cache_put(api_path, out)
        with _dir_cache_lock:
            _dir_fail.pop(api_path, None)
    return out


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
    # 目录内容已变：作废缓存，否则用户删完再进同一目录（30s 内）还会看到
    # 已删条目 —— 与 QAS 侧「删除后 invalidate_library_cache」同口径。
    invalidate_dir_cache()
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
