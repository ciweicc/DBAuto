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
