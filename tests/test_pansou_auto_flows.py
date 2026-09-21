"""自动转存 / TMDB 转存 与「盘搜（PanSou）搜索页」的能力差审计（issue #88 追问）。

issue #88 的主线修的是**搜索页**：结果行不但能展开文件清单，还能把
「共 N 集 / 缺几集」直接写在行上。用户随即追问：

    「自动转存和 TMDB转存也有类似的pansou问题吗」

这里的「pansou 问题」指的不是「搜索页修好了、别处没修」这种表面差异，而是
那条**数据通道**在别处是否也被丢掉了。审计结论（本文件逐条钉住）：

  1. 三条链路**共用** `transfer.search_pansou()`，候选来源与适配完全一致
     —— 自动转存、TMDB 转存不会因为「不是搜索页」而拿到不同的候选集；
  2. 差别在**消费方式**：搜索页把 QAS `get_share_detail` 的 `data.list`
     归一化成「条目数 / 视频数 / 文件清单」（`share_preview`），
     而自动转存 / 失效修复只用 `validate_share_link()` 取一个 `success` 布尔，
     清单被丢掉 —— 于是「集数全不全」在自动链路上依然不可见；
  3. 两条自动链路都只取**第一条**候选（`valid_sr[0]` / `sr[0]`），拿不到
     「视频文件数」这种廉价的择优依据，等于把「是不是全集」交给 PanSou
     的返回顺序决定；
  4. TMDB 一侧额外多一层：`_resolve_tmdb_id()` 解析出的 id 只参与**去重**，
     不参与选候选；搜索关键词仍是纯标题（不带年份/季数），
     电影/剧集同名的错配只能靠运气。

本文件不做行为改动，只把「已经丢掉的能力」变成可复跑的证据，
避免「以为自动链路也修了」。
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import share_preview
import transfer


# ---------------------------------------------------------------- 共通层

def test_all_three_flows_share_one_pansou_search():
    """三条链路共用同一个 search_pansou：候选来源不会因为入口不同而不同。

    若未来有人给自动转存另起一套搜索（比如换了 cloud_types），
    这条会立刻红 —— 那才是「自动转存有类似问题」的真正形态。
    """
    src = inspect.getsource(transfer)
    assert "def search_pansou(" in src
    # 搜索页路由 + 自动转存（_search_single_task）+ 失效修复，三处都调它
    assert "search_pansou(keyword, category)" in inspect.getsource(
        __import__("routes_transfer"))
    assert "search_pansou(title)" in inspect.getsource(transfer._search_single_task)
    assert "search_pansou(taskname)" in inspect.getsource(transfer.fix_expired_tasks)


# ------------------------------------------------- 自动转存：清单被丢掉

def test_auto_transfer_only_reads_success_flag_not_file_list():
    """自动转存的链接校验只取 success，QAS 返回的文件清单没有被利用。"""
    src = inspect.getsource(transfer.validate_share_link)
    assert "success" in src
    assert "list" not in src, "validate_share_link 不应消费文件清单（若消费了，本审计需重写）"
    assert "normalize_share_detail" not in src

    # 归一化早已能给出「有多少个视频文件」，能力是现成的
    # （集号推断已在 issue #88 第五轮整体删除：误报比不报更贵）
    raw = {"success": True, "data": {"list": [
        {"file_name": "Show.S01E01.mkv", "size": 1},
        {"file_name": "Show.S01E03.mkv", "size": 1},
    ]}}
    out = share_preview.normalize_share_detail(raw)
    assert out["video_count"] == 2
    assert "episodes" not in out, "集号推断不得回来（issue #88 第五轮）"


def test_auto_transfer_picks_first_candidate_without_completeness():
    """自动转存只取第一条通过校验的候选，不看候选完整度。"""
    src = inspect.getsource(transfer.run_transfer)
    assert "chosen = valid_sr[0]" in src
    assert "video_count" not in src, "run_transfer 未按视频文件数择优"
    assert "episodes" not in src, "run_transfer 未按集数缺口择优（集号推断已删除）"


def test_fix_expired_picks_first_candidate_even_unchecked():
    """失效修复更粗：连校验都没有，直接 sr[0]。"""
    src = inspect.getsource(transfer.fix_expired_tasks)
    assert "chosen = sr[0]" in src
    # 它确实会校验新链接，但校验发生在挑完之后（无效就把整条判失败）
    assert src.index("chosen = sr[0]") < src.index("validate_share_link(new_url)")


def test_auto_transfer_has_no_episode_visibility():
    """自动转存的日志/结果里没有任何「候选有几集」的信息。"""
    src = inspect.getsource(transfer)
    assert not __import__("re").search(r"候选.{0,20}集", src)
    assert "share_preview" not in src, "转存链路未接入分享清单模块"


# ------------------------------------------------- TMDB 转存：多一层丢

def test_tmdb_flow_reuses_auto_transfer_pipeline():
    """TMDB 转存没有自己的搜索/转存实现，完全复用自动转存那条管线。

    所以「自动转存有的问题，TMDB 转存一样有」，不是两处独立缺陷。
    """
    tmdb_src = inspect.getsource(transfer.run_transfer)  # 同一条管线
    assert "_search_single_task" in inspect.getsource(transfer.run_transfer)
    assert "search_pansou" not in tmdb_src, "候选搜索在 _search_single_task 里，管线本身不含搜索"


def test_tmdb_id_only_dedups_never_ranks_candidates():
    """TMDB 解析出的 id 只进历史去重，不参与候选挑选。"""
    src = inspect.getsource(transfer.run_transfer)
    dedup_at = src.index("_find_in_history")
    pick_at = src.index("valid_sr[0]")
    assert dedup_at < pick_at
    # 挑选前后不存在把 tmdb_id 传给搜索/校验的调用
    assert "search_pansou(title" in inspect.getsource(transfer._search_single_task)
    assert "search_tmdb_id" not in src, "run_transfer 不直接用 TMDB 搜索"


def test_search_keyword_has_no_year_or_season():
    """搜索关键词是纯标题：同名电影/剧集、多季作品只能靠 PanSou 排序。"""
    src = inspect.getsource(transfer._search_single_task)
    assert "search_pansou(title)" in src
    assert "year" not in src and "season" not in src
