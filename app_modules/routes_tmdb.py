# routes_tmdb.py — TMDB 相关路由 Mixin
from tmdb import (
    get_tmdb_list, get_tmdb_genres, refresh_tmdb_cache, get_tmdb_watch_providers,
    match_tmdb_by_name,
    COUNTRY_OPTIONS, LANGUAGE_OPTIONS, SORT_OPTIONS, MOVIE_LIST_TYPES, TV_LIST_TYPES,
)
from utils import log
from validator import validate_string


# 单次批量匹配的文件名上限：一个网盘目录一般 20~60 个条目，
# 取 60 既覆盖常见目录，又避免匿名/低配额把 TMDB 并发打满。
MAX_MATCH_NAMES = 60


class TmdbRouteMixin:
    """TMDB 数据源相关路由"""

    def _handle_tmdb_get(self, route):
        if route == "/api/tmdb/list":
            params = self._get_query_params()
            media_type = params.get("media_type", "movie")
            list_type = params.get("list_type", "trending")
            page = int(params.get("page", "1")) or 1
            genre_id = int(params.get("genre_id", "0")) or 0
            year = int(params.get("year", "0")) or 0
            min_rating = float(params.get("min_rating", "0")) or 0
            country = params.get("country", "")
            sort_by = params.get("sort_by", "popularity.desc")
            language = params.get("language", "")
            watch_providers = params.get("watch_providers", "")

            ok, msg = validate_string(media_type, min_len=1, max_len=10)
            if not ok:
                self._send_json({"error": "media_type: {}".format(msg)}, 400)
                return True

            ok, msg = validate_string(list_type, min_len=1, max_len=20)
            if not ok:
                self._send_json({"error": "list_type: {}".format(msg)}, 400)
                return True

            try:
                result = get_tmdb_list(
                    media_type=media_type, list_type=list_type, page=page,
                    genre_id=genre_id, year=year, min_rating=min_rating,
                    country=country, sort_by=sort_by, language=language, watch_providers=watch_providers)
                self._send_json(result)
            except Exception as e:
                log("TMDB list 错误: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        if route == "/api/tmdb/genres":
            params = self._get_query_params()
            media_type = params.get("media_type", "movie")

            ok, msg = validate_string(media_type, min_len=1, max_len=10)
            if not ok:
                self._send_json({"error": "media_type: {}".format(msg)}, 400)
                return True

            try:
                genres = get_tmdb_genres(media_type)
                self._send_json({"genres": genres})
            except Exception as e:
                log("TMDB genres 错误: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        if route == "/api/tmdb/options":
            self._send_json({
                "countries": COUNTRY_OPTIONS,
                "languages": LANGUAGE_OPTIONS,
                "sorts": SORT_OPTIONS,
                "movie_list_types": MOVIE_LIST_TYPES,
                "tv_list_types": TV_LIST_TYPES,
            })
            return True

        if route == "/api/tmdb/match":
            # 媒体库图文卡片（issue #27）：按网盘文件名批量匹配 TMDB 作品。
            # 目录里文件可能上百个，这里限制单次批量上限并对超限部分如实标记，
            # 避免一个页面把 TMDB 配额和线程池吃光。
            params = self._get_query_params()
            names = params.get("names") or []
            if isinstance(names, str):
                names = [names]
            cleaned = []
            for n in names[:MAX_MATCH_NAMES]:
                ok, msg = validate_string(n, min_len=1, max_len=200)
                if not ok:
                    continue
                cleaned.append(n)
            media_type = params.get("media_type", "")
            if media_type not in ("movie", "tv"):
                media_type = ""
            try:
                matches = {}
                for name in cleaned:
                    matches[name] = match_tmdb_by_name(name, media_type)
                self._send_json({
                    "success": True,
                    "matches": matches,
                    "truncated": max(0, len(names) - len(cleaned)),
                })
            except Exception as e:
                log("TMDB match 错误: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        if route == "/api/tmdb/refresh":
            refresh_tmdb_cache()
            self._send_json({"success": True, "message": "TMDB 缓存已刷新"})
            return True

        if route == "/api/tmdb/providers":
            params = self._get_query_params()
            media_type = params.get("media_type", "movie")
            region = params.get("region", "US")

            ok, msg = validate_string(media_type, min_len=1, max_len=10)
            if not ok:
                self._send_json({"error": "media_type: {}".format(msg)}, 400)
                return True

            try:
                providers = get_tmdb_watch_providers(media_type, region)
                self._send_json({"providers": providers})
            except Exception as e:
                log("TMDB providers 错误: {}".format(e))
                self._send_json({"error": str(e)}, 500)
            return True

        return False

    def _handle_tmdb_post(self, route, body):
        return False
