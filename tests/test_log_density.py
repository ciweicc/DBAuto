"""issue #68：执行日志面板的信息密度。

面板日志的问题不是"行太长"，是"行太多且互相重复"：
一部电影从搜索到转存会产出 4 行在说同一件事。这里把"每个标题只剩一条终态行"
钉成断言，防止以后有人再往 log() 里塞实现细节噪声。

被过滤的行**仍然写 data/app.log**（读日志排查问题的人需要全程留痕），
只是不再投递到前端面板 —— 两个断言必须同时成立，否则就是把日志删了而不是去重。
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app_modules"))

import utils
from utils import log, log_progress, clear_progress


def _panel_lines():
    return list(log_progress)


def test_plain_log_reaches_panel():
    clear_progress()
    log("列表获取完成: 20 条")
    assert _panel_lines() == [x for x in _panel_lines() if "列表获取完成" in x]
    assert len(_panel_lines()) == 1


def test_tag_kept_for_source_attribution():
    clear_progress()
    log("转存完成: 3 条", tag="TRANSFER#ab12cd")
    line = _panel_lines()[0]
    assert "[TRANSFER#AB12CD]" in line
    assert line.endswith("转存完成: 3 条")


def test_panel_false_still_writes_file_log():
    """panel=False 只影响面板，不影响 data/app.log。"""
    clear_progress()
    written = []
    orig_info = utils.logger.info
    utils.logger.info = lambda m: written.append(m)
    try:
        log("待搜索任务: 20 条，并发数: 4", panel=False)
    finally:
        utils.logger.info = orig_info
    assert written == ["待搜索任务: 20 条，并发数: 4"]
    assert _panel_lines() == []


def test_known_noise_patterns_are_dropped_from_panel():
    """即便调用方忘了加 panel=False，已知的实现细节噪声也不该上面板。"""
    clear_progress()
    log("待搜索任务: 20 条，并发数: 4")
    assert _panel_lines() == []


def test_one_terminal_line_per_title():
    """一个标题在面板上只留一条终态结论行，且必须带标题。"""
    clear_progress()
    # 面板上不该出现的中间态
    log("搜索: 流浪地球", panel=False)
    log("找到: [电影] 流浪地球", panel=False)
    # 唯一保留的终态行
    log("流浪地球：转存成功", tag="TRANSFER#ab12cd")
    lines = [x for x in _panel_lines() if "流浪地球" in x]
    assert len(lines) == 1
    assert "转存成功" in lines[0]


# ---------------------------------------------------------------- 折叠详情（issue #68 追问）
# 第一轮把「行数」压下去了，但**行本身**仍把整段 requests 异常糊在面板上，
# 视觉上折成 5–6 行。这里把「异常原文必须从主行里剥出来」钉成断言。
_EXC = "HTTPConnectionPool(host='192.168.5.55', port=8080): Read timed out. (read timeout=20)"


def test_exception_text_is_split_off_the_main_line():
    """`主行: 异常原文` 必须拆成「主行 + 详情」，主行不带异常原文。"""
    clear_progress()
    log("PanSou 重试: {}".format(_EXC))
    lines = _panel_lines()
    assert len(lines) == 2, "应为主行 + 详情行两行，实际 {}".format(lines)
    main, detail = lines
    assert _EXC not in main, "主行里仍带着整段异常：{}".format(main)
    assert _EXC in detail, "详情里没拿到完整异常原文：{}".format(detail)


def test_bare_exception_line_gets_a_readable_main_line():
    """整条就是一段异常（旧调用没给主行）时，也要有一句能读的主行。"""
    clear_progress()
    log(_EXC)
    main = _panel_lines()[0]
    assert "HTTPConnectionPool" not in main
    assert "请求失败" in main, main


def test_explicit_detail_is_supported():
    """显式 `detail=` 时主行保持干净，detail 进第二行。"""
    clear_progress()
    log("HTTP POST 超时放弃（已重试 3 次）", detail=_EXC)
    lines = _panel_lines()
    assert len(lines) == 2
    assert lines[0].endswith("HTTP POST 超时放弃（已重试 3 次）")
    assert _EXC in lines[1]


def test_plain_message_with_colon_is_not_treated_as_detail():
    """普通句子里的冒号不能被误当成「主行: 详情」。"""
    clear_progress()
    for msg in ("TMDB: 未配置 api_key", "转存完成: 3 条", "共获取 20 条"):
        clear_progress()
        log(msg)
        lines = _panel_lines()
        assert len(lines) == 1, "{} 被误拆成 {}".format(msg, lines)
        assert msg in lines[0]


def test_plain_long_message_is_not_hidden_into_detail():
    """没有结构化异常的长句只是被截短显示，**不能**整句塞进详情里藏起来。"""
    clear_progress()
    log("一个不带任何异常的普通长句" * 20)
    lines = _panel_lines()
    assert len(lines) == 1, "普通长句不该产生详情行：{}".format(len(lines))
    assert "…" in lines[0], "长句应被截短（前端有 title 展示全文）"


def test_exception_detail_still_written_to_file_log():
    """折叠只影响面板，`data/app.log` 里异常原文必须完整保留。"""
    clear_progress()
    written = []
    orig_info = utils.logger.info
    utils.logger.info = lambda m: written.append(m)
    try:
        log("PanSou 重试: {}".format(_EXC))
    finally:
        utils.logger.info = orig_info
    assert any(_EXC in w for w in written), written


def test_progress_pairs_main_with_detail_for_backfill():
    """progress 必须自足（SSE 断连时靠它回填）：详情行紧跟主行，可成对认领。"""
    clear_progress()
    log("A 事件")
    log("B 事件", detail=_EXC)
    log("C 事件")
    lines = _panel_lines()
    assert len(lines) == 4, lines
    # 前端按「下一行是否以 `本行`: 开头」认领详情（见 scripts/check_log_density.py
    # 的 _split_pairs 与前端 log.js 的 detailFor）。
    assert not lines[1].startswith(lines[0] + ": "), "A 事件不该被认成带详情"
    assert lines[2].startswith(lines[1] + ": "), "B 的详情必须紧跟 B 的主行"
    assert _EXC in lines[2]
    assert not lines[3].startswith(lines[2] + ": "), "C 事件不该被认成带详情"


def test_blocking_panel_drop_patterns():
    """第二轮新增的面板黑名单：QAS 缓存行、失效检测目录范围。"""
    clear_progress()
    log("QAS: 12 个任务已缓存")
    log("失效检测目录范围: ['/影视']")
    log("搜索: 流浪地球")
    assert _panel_lines() == []
