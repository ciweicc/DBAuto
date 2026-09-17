import urllib.parse
from utils import http_get, http_post, http_post_stream


class APIClient:
    def __init__(self, base_url, token=None, timeout=15):
        self.base_url = base_url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout

    def _build_url(self, path):
        if path.startswith("/"):
            path = path[1:]
        if "?" in path:
            base, qs = path.split("?", 1)
            return "{}?{}".format(urllib.parse.urljoin(self.base_url, base), qs)
        return urllib.parse.urljoin(self.base_url, path)

    def get(self, path, params=None):
        url = self._build_url(path)
        if params:
            url = "{}?{}".format(url, urllib.parse.urlencode(params))
        return http_get(url, timeout=self.timeout)

    def post(self, path, data=None):
        url = self._build_url(path)
        return http_post(url, data or {}, timeout=self.timeout)

    def post_stream(self, path, data=None, timeout=120):
        url = self._build_url(path)
        return http_post_stream(url, data or {}, timeout=timeout)


# PanSou /api/search 支持的网盘类型（完整列表，参考 fish2018/pansou 文档）。
# 注：本项目转存仅使用夸克网盘，search() 只请求 quark 类型；
# 此列表与 infer_disk_type() 保留供 check_links() 使用（链接检测需按 URL 推断 disk_type）。
PANSOU_CLOUD_TYPES = [
    "baidu", "aliyun", "quark", "guangya", "tianyi", "uc", "mobile",
    "115", "pikpak", "xunlei", "123", "magnet", "ed2k",
]

# /api/check/links 可识别的 disk_type（用于按 URL 推断网盘类型时兜底）。
_PANSOU_DISK_TYPE_HINTS = [
    ("pan.quark.cn", "quark"),
    ("pan.baidu.com", "baidu"),
    ("aliyundrive.com", "aliyun"),
    ("alipan.com", "aliyun"),
    ("115.com", "115"),
    ("115cdn.com", "115"),
    ("115cdn.net", "115"),
    ("pan.xunlei.com", "xunlei"),
    ("cloud.189.cn", "tianyi"),
    ("pan.uc.cn", "uc"),
    ("caiyun.139.com", "mobile"),
    ("139.com", "mobile"),
    ("123pan.com", "123"),
    ("123pan.cn", "123"),
    ("pikpak.com", "pikpak"),
    ("pikpak.cn", "pikpak"),
]


def infer_disk_type(url):
    """根据分享链接 URL 推断 PanSou check/links 所需的 disk_type。

    PanSou 的 /api/check/links 要求每条链接带上 disk_type；但搜索结果通常只给出
    url 与来源频道（source），并不直接给出网盘类型。这里按域名做启发式推断，
    无法识别时兜底为 quark（盘搜结果中夸克占比最高）。
    """
    u = (url or "").lower()
    for domain, dtype in _PANSOU_DISK_TYPE_HINTS:
        if domain in u:
            return dtype
    return "quark"


class PanSouClient(APIClient):
    def search(self, keyword):
        # 本项目仅使用夸克网盘转存，只请求 quark 类型结果
        return self.post(
            "/api/search",
            {"kw": keyword, "cloud_types": ["quark"], "res": "merge"},
        )

    def check_links(self, urls):
        items = [{"disk_type": infer_disk_type(u), "url": u} for u in urls if u]
        if not items:
            return []
        return self.post("/api/check/links", {"items": items})


class QASClient(APIClient):
    def _add_token_to_url(self, path):
        if self.token:
            sep = "&" if "?" in path else "?"
            return "{}{}token={}".format(path, sep, self.token)
        return path

    def get_data(self):
        return self.get(self._add_token_to_url("/data"))

    def get_share_detail(self, shareurl):
        return self.post(self._add_token_to_url("/get_share_detail"), {"shareurl": shareurl})

    def add_task(self, taskname, shareurl, savepath, pattern="", replace=""):
        payload = {"taskname": taskname, "shareurl": shareurl, "savepath": savepath}
        if pattern:
            payload["pattern"] = pattern
        if replace:
            payload["replace"] = replace
        return self.post(self._add_token_to_url("/api/add_task"), payload)

    def run_script_now(self, tasklist):
        return self.post(self._add_token_to_url("/run_script_now"), {"tasklist": tasklist})

    def run_script_now_stream(self, tasklist):
        return self.post_stream(self._add_token_to_url("/run_script_now"), {"tasklist": tasklist})

    def update(self, data):
        return self.post(self._add_token_to_url("/update"), data)

    def update_tasklist(self, tasklist):
        """整体写回 QAS 的 tasklist（用于删除任务配置）。

        对应 QAS `/update` 的 `tasklist` 字段：QAS 侧是「整表替换」语义
        （见其 WebUI `removeTask` 的实现：splice 后提交整个 formData），
        因此删除任务必须提交**删减后的完整列表**——只发被删的那一条，
        会把其余任务全部抹掉。

        刻意不改动列表内任何字段：QAS 的 `/update` 会把传入 tasklist 直接落盘，
        `addition`（任务级插件配置，如 auto_unarchive）、`shareurl_ban`（失效标记）
        若被重写/丢弃，任务的解压、订阅等行为就会静默改变。
        """
        return self.update({"tasklist": tasklist})

    # ---------- 网盘文件管理（媒体库浏览 / 删除，用于双向同步） ----------

    def get_savepath_detail(self, path=None, fid=None):
        """列出某个网盘目录下的文件。

        对应 QAS `/get_savepath_detail`：接受 `path`（如 `/媒体库/电影`）
        或 `fid`，返回 {"success": bool, "data": {"fid", "list", "paths"}}。
        `list` 中每项包含 fid / file_name / dir / size / updated_at 等。
        """
        # 注意：token 必须先于其它 query 参数拼入；这里手工拼接 query，
        # 因为底层 http_get 不接受 params 参数（见 utils.http_get）。
        path_part = ""
        if fid is not None:
            path_part = "/get_savepath_detail?fid={}".format(urllib.parse.quote(str(fid)))
        elif path:
            path_part = "/get_savepath_detail?path={}".format(urllib.parse.quote(str(path)))
        else:
            path_part = "/get_savepath_detail?path=%2F"
        return self.get(self._add_token_to_url(path_part))

    def delete_file(self, path=None, fid=None):
        """删除网盘中的单个文件/目录（移入回收站）。

        对应 QAS `/delete_file`：fid 优先，缺失时由 QAS 依据 path 反查 fid。
        返回 {"success": bool, ...}。
        """
        payload = {}
        if fid is not None:
            payload["fid"] = fid
        elif path:
            payload["path"] = path
        else:
            return {"success": False, "message": "缺少 fid 或 path"}
        return self.post(self._add_token_to_url("/delete_file"), payload)
