# routes_tmdb.py — TMDB 相关路由 Mixin
from tmdb import (
    get_tmdb_list, get_tmdb_genres, refresh_tmdb_cache,
    REGION_OPTIONS, SORT_OPTIONS, MOVIE_LIST_TYPES, TV_LIST_TYPES,
)
from utils import log
from validator import validate_string


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
            region = params.get("region", "")
            sort_by = params.get("sort_by", "popularity.desc")
            language = params.get("language", "")

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
                    region=region, sort_by=sort_by, language=language)
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
                "regions": REGION_OPTIONS,
                "sorts": SORT_OPTIONS,
                "movie_list_types": MOVIE_LIST_TYPES,
                "tv_list_types": TV_LIST_TYPES,
            })
            return True

        if route == "/api/tmdb/refresh":
            refresh_tmdb_cache()
            self._send_json({"success": True, "message": "TMDB 缓存已刷新"})
            return True

        return False

    def _handle_tmdb_post(self, route, body):
        return False
