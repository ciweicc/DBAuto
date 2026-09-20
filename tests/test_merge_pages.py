"""issue #68 追问：「转存」与「定时」两页合并为单页「转存」。

用户诉求原话：「考量这两个页面整合的可能性，另外加入定时任务管理的功能，
然后将这两个页面重复的内容进行整合」。

合并前后的重复是**逐字重复的 DOM**：
  - 分类 chips：catsMovie/catsTV/catsVariety  vs  schedCatsMovie/schedCatsTV/schedCatsVariety
  - 筛选控件：manualFilterRating/manualFilterSort/manualFilterYear{From,To}
              vs  filterRating/filterSort/filterYear{From,To}
  - 上限输入：limit  vs  schedLimit
即「同一件事要在两个页面各配一遍」。

合并后：分类与筛选**只有一份**，既是「立即转存」的输入也是「定时转存」的输入；
定时相关的东西（启用开关 / 时间 / 下次执行 / 立即执行 / 失效检测目录）
收敛进「定时任务管理」卡片，仍留在「转存」页。

本用例把这些判据钉成断言，防止重复 DOM / 第二个页面回归。
"""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "static", "src")
_JS = os.path.join(_SRC, "scripts")
_DIST = os.path.join(_ROOT, "static", "index_new.html")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _strip_comments(html):
    """去掉 HTML 注释：注释里叙述沿革时会提到已删除的 id，那不是残留。"""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


def test_no_duplicate_category_or_filter_dom():
    """分类 chips / 筛选控件 / 上限输入在整个源文件里只能出现一份。"""
    body = _strip_comments(_read(os.path.join(_SRC, "body.html")))

    # 1. 分类容器：只保留一套
    for keep in ("catsMovie", "catsTV", "catsVariety"):
        assert body.count('id="%s"' % keep) == 1, "%s 容器数量不为 1（分类 DOM 重复）" % keep
    for dead in ("schedCatsMovie", "schedCatsTV", "schedCatsVariety"):
        assert dead not in body, "残留旧的定时分类容器 %s（应与手动分类合并）" % dead

    # 2. 筛选控件：只保留一套（filter*），manualFilter* 是重复的那套
    for keep in ("filterRating", "filterSort", "filterYearFrom", "filterYearTo"):
        assert body.count('id="%s"' % keep) == 1, "筛选控件 #%s 数量不为 1" % keep
    for dead in ("manualFilterRating", "manualFilterSort",
                 "manualFilterYearFrom", "manualFilterYearTo"):
        assert dead not in body, "残留重复的筛选控件 %s（应与「转存设置」合并）" % dead

    # 3. 上限输入：limit 与 schedLimit 曾是两份，现在只留 #limit
    assert body.count('id="limit"') == 1, "#limit 数量不为 1"
    assert "schedLimit" not in body, "残留重复的上限输入 #schedLimit"


def test_schedule_tab_is_gone_and_nav_shrunk():
    """不再有独立的「定时」页面：PAGE_IDS / PAGES / BOTTOM_NAV / 侧栏 / 底栏一起去掉。"""
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="tabSchedule"' not in _strip_comments(body), "仍存在独立的 #tabSchedule 页面容器"
    assert 'data-tab="schedule"' not in body, "侧栏仍有「定时」导航项"

    tabs = _read(os.path.join(_JS, "tabs.js"))
    assert "schedule:" not in tabs.split("var PAGES")[0], "PAGE_IDS 仍映射 schedule 页面"
    m = re.search(r"var PAGES = \[(.*?)\]", tabs)
    pages = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    assert "schedule" not in pages, "PAGES 仍包含 schedule"
    m2 = re.search(r"var BOTTOM_NAV = \[(.*?)\]", tabs)
    navs = [x.strip().strip("'\"") for x in m2.group(1).split(",") if x.strip()]
    assert navs == ["overview", "manual", "library", "history", "settings"], \
        "底部导航项与预期不一致: {}".format(navs)
    # 切到「转存」页要同时拉取调度配置（定时任务管理卡片的数据来源）
    assert "if(tab === 'manual') loadSchedule();" in tabs, \
        "切到「转存」页未加载调度配置，定时卡片会空白"


def test_schedule_management_ui_present_in_transfer_page():
    """「定时任务管理」区块必须在「转存」页内，且带下一跳时间与立即执行。"""
    body = _read(os.path.join(_SRC, "body.html"))
    manual_start = body.index('id="tabManual"')
    manual_end = body.index('id="tabHistory"')
    manual = _strip_comments(body[manual_start:manual_end])

    for token in ('id="schedTOn"', 'id="schedEOn"', 'id="schedTHour"', 'id="schedEHour"',
                  'id="schedTNext"', 'id="schedENext"', 'id="schedLastTransfer"',
                  'id="expiredDirList"'):
        assert token in manual, "「转存」页缺少定时任务管理控件：{}".format(token)
    assert "定时任务管理" in manual, "缺少「定时任务管理」区块标题"
    # 两个立即执行按钮分别触发 transfer / expired_check（没有 section 会被后端 400）
    assert manual.count("runScheduleNow('transfer')") == 1
    assert manual.count("runScheduleNow('expired_check')") == 1


def test_run_schedule_now_sends_section():
    """runScheduleNow 必须带 section —— 后端 run_now 分支要求它。"""
    ov = _read(os.path.join(_JS, "overview.js"))
    assert "action:'run_now'" in ov or 'action: "run_now"' in ov or "action:'run_now'" in ov, \
        "概览页缺少 run_now 调用"
    m = re.search(r"apiPost\('/api/schedule', \{[^}]*\}\)", ov)
    assert m, "未能解析 runScheduleNow 的请求体"
    assert "section:section" in m.group(0).replace(" ", ""), \
        "run_now 请求体缺少 section（后端会返回 400，按钮点了没反应）"


def test_schedule_js_reads_merged_dom():
    """schedule.js 必须读写合并后的 DOM（#tabManual 的 chips / #limit），不得指向已删节点。"""
    js = _read(os.path.join(_JS, "schedule.js"))
    assert "#tabManual .chip" in js, "schedule.js 未从合并后的分类 chips 读取选择"
    assert "getSelectedTasks('tabManual')" in js, "保存调度时未取「转存」页的榜单选择"
    assert "document.getElementById('limit')" in js, "保存调度时未复用共享的上限输入 #limit"
    assert "manualFilter" not in js, "schedule.js 仍引用已删除的 manualFilter* 控件"
    assert "schedLimit" not in js, "schedule.js 仍引用已删除的 #schedLimit"
    # 下次执行时间必须被回填
    assert "_setNextRun('schedTNext'" in js and "_setNextRun('schedENext'" in js, \
        "schedule.js 未回填下次执行时间"


def test_categories_single_source_saves_both():
    """分类 chips 只有一份：点击即保存（同时作为手动转存与定时转存的输入）。"""
    cats = _read(os.path.join(_JS, "categories.js"))
    assert "parseSchedCats" not in cats, "仍存在 parseSchedCats（第二套 chips 的渲染入口）"
    assert "schedCats" not in cats, "仍引用已删除的定时分类容器"
    assert "syncToSchedule" not in cats, "「同步到定时任务」已无意义（选择本就是同一份）"

    # parseCategories 用同一份容器，且点击 chip 会持久化
    m = re.search(r"function parseCategories\(\)\{(.*?)\n\}", cats, re.S)
    assert m, "未能解析 parseCategories"
    assert "catsMovie" in m.group(1) and "toggleChip" in m.group(1), \
        "parseCategories 未渲染合并后的唯一分类容器"

    init = _read(os.path.join(_JS, "init.js"))
    assert "parseSchedCats" not in init, "init.js 仍在调用已删除的 parseSchedCats"


def test_dist_reflects_merge():
    """产物层面复核：合并后的 DOM 已进入构建产物。"""
    if not os.path.isfile(_DIST):
        return
    dist = _read(_DIST)
    assert 'id="tabManual"' in dist, "产物缺少「转存」页"
    assert 'id="tabSchedule"' not in dist, "产物仍含已删除的定时页容器"
    assert 'data-tab="schedule"' not in dist, "产物仍含「定时」导航项"
    assert "定时任务管理" in dist, "产物缺少「定时任务管理」区块"
    assert 'id="schedTNext"' in dist and 'id="schedENext"' in dist, "产物缺少下次执行时间节点"
    assert "schedCatsMovie" not in dist and "manualFilterRating" not in dist, \
        "产物仍含重复的分类 / 筛选 DOM"
