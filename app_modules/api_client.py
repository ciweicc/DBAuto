import urllib.parse
from utils import http_get, http_post, http_post_stream, log


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


class PanSouClient(APIClient):
    def search(self, keyword):
        return self.post("/api/search", {"kw": keyword, "cloud_types": ["quark"], "res": "merge"})

    def check_links(self, urls):
        items = [{"disk_type": "quark", "url": u} for u in urls if u]
        if not items:
            return []
        return self.post("/api/check/links", {"items": items})


class QASClient(APIClient):
    def _add_token(self, params=None):
        """将 token 添加到查询参数中"""
        if self.token:
            p = dict(params) if params else {}
            p["token"] = self.token
            return p
        return params

    def get_data(self):
        return self.get("/data", self._add_token())

    def get_share_detail(self, shareurl):
        return self.post("/get_share_detail?token={}".format(self.token), {"shareurl": shareurl})

    def add_task(self, taskname, shareurl, savepath, pattern="", replace=""):
        payload = {"taskname": taskname, "shareurl": shareurl, "savepath": savepath}
        if pattern:
            payload["pattern"] = pattern
        if replace:
            payload["replace"] = replace
        return self.post("/api/add_task?token={}".format(self.token), payload)

    def run_script_now(self, tasklist):
        return self.post("/run_script_now?token={}".format(self.token), {"tasklist": tasklist})

    def run_script_now_stream(self, tasklist):
        return self.post_stream("/run_script_now?token={}".format(self.token), {"tasklist": tasklist})

    def update(self, data):
        return self.post("/update?token={}".format(self.token), data)


class OpenListClient(APIClient):
    def list_files(self, path, page=1, per_page=5000):
        return self.post("/api/fs/list", {
            "path": path,
            "password": "",
            "page": page,
            "per_page": per_page,
            "refresh": False,
            "token": self.token
        })

    def remove_files(self, names, dir_path):
        return self.post("/api/fs/remove", {
            "names": names,
            "dir": dir_path,
            "token": self.token
        })
