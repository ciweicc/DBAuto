"""构建产物守护（UI 路线图 5.2）。

`static/index_new.html` 是由 `static/src/build.sh` 拼接生成的单文件产物，
手工编辑产物而不改源文件会造成「源码与产物漂移」。本用例把此前人工执行的
校验固化为 CI 门禁：

1. CSS 括号配平、JS 语法可解析（node --check 等价的静态校验）；
2. 产物确实由 src 源文件生成（关键模块标记齐全，且新增源文件已进入构建清单）；
3. 已下线功能的样式/代码不残留（如「想看 / wish」功能移除后不得回归）。

注意：本用例不依赖 node，避免 CI 环境差异；语法层面只做结构性校验。
"""
import os
import re
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "static", "src")
_DIST = os.path.join(_ROOT, "static", "index_new.html")
_BUILD_SH = os.path.join(_SRC, "build.sh")
_JS_DIR = os.path.join(_SRC, "scripts")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def dist():
    if not os.path.isfile(_DIST):
        pytest.skip("构建产物 static/index_new.html 不存在（可能尚未构建）")
    return _read(_DIST)


def _extract(dist_html):
    """拆出 <style> 与末尾业务 <script> 段落。"""
    css = "\n".join(re.findall(r"<style>(.*?)</style>", dist_html, re.S))
    scripts = re.findall(r"<script>(.*?)</script>", dist_html, re.S)
    # 第一个 script 是 FOUC 主题脚本，业务 bundle 为最后一段
    return css, (scripts[-1] if scripts else "")


def test_dist_exists_and_non_trivial(dist):
    assert len(dist) > 50_000, "产物体积异常，疑似构建不完整"
    assert "DBAuto frontend bundle" in dist, "缺少构建标记注释"


def test_css_braces_balanced(dist):
    css, _ = _extract(dist)
    assert css, "产物中未找到 <style> 内容"
    assert css.count("{") == css.count("}"), "CSS 括号不配平，构建拼接可能出错"


def test_js_syntax(dist):
    """优先用 node --check 做真实语法校验；无 node 时降级为结构性校验。"""
    _, js = _extract(dist)
    assert js.strip(), "产物中未找到业务 script"

    if shutil.which("node"):
        tmp = os.path.join(_ROOT, ".build-check.js")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(js)
            proc = subprocess.run(
                ["node", "--check", tmp],
                capture_output=True, text=True, timeout=60,
            )
            assert proc.returncode == 0, "打包 JS 语法错误：\n{}".format(proc.stderr)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    else:
        # 降级：括号配平 + 不得出现拼接残留的模块标记错位
        assert js.count("{") == js.count("}"), "JS 花括号不配平"
        assert js.count("(") == js.count(")"), "JS 圆括号不配平"


def _js_files_from_build_sh():
    build_sh = _read(_BUILD_SH)
    m = re.search(r"JS_FILES=\((.*?)\)", build_sh, re.S)
    assert m, "build.sh 中未找到 JS_FILES 清单"
    return m.group(1).split()


def test_all_source_modules_bundled(dist):
    """build.sh 的 JS_FILES 清单必须覆盖 scripts/ 下全部模块（防止新增文件漏打包）。"""
    listed = _js_files_from_build_sh()

    on_disk = sorted(n[:-3] for n in os.listdir(_JS_DIR) if n.endswith(".js"))
    missing = [n for n in on_disk if n not in listed]
    assert not missing, "以下源模块未加入 build.sh 的 JS_FILES，产物会漏功能：{}".format(missing)

    # 产物可能被 esbuild minify（剥离注释），因此用「每个模块的顶层函数名」而非注释标记校验
    _, js = _extract(dist)
    for name in listed:
        declared = re.findall(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", _read(
            os.path.join(_JS_DIR, name + ".js")), re.M)
        if not declared:
            continue  # 纯变量声明模块（如 globals）无顶层函数，跳过
        assert any(d in js for d in declared), (
            "产物中未找到模块 {} 的任何顶层函数（{}），疑似漏打包".format(name, declared))


TOKENS_CSS = os.path.join(_SRC, "styles", "tokens.css")
MAIN_CSS = os.path.join(_SRC, "styles", "main.css")


def test_tokens_and_main_css_bundled(dist):
    css, _ = _extract(dist)
    tokens = _read(TOKENS_CSS)
    # 关键令牌名（值可能被 minify 重排，但 var 名与选择器保留）
    for token in ["--glass-bg-strong", "--surface-solid", "--z-toast", "--fs-base"]:
        assert token in tokens, "tokens.css 自身缺少 {}".format(token)
        assert token in css, "产物样式缺少令牌 {}".format(token)
    # 浅色主题覆盖块（含引号可能被 minify 改写，按无引号形式匹配）
    assert 'data-theme=light' in css.replace('"', '').replace("'", '') or \
        '[data-theme=light]' in css, "产物缺少浅色主题样式块"
    # main.css 的标志性选择器
    for sel in ["#toastContainer", ".toast-action"]:
        assert sel in css, "产物样式缺少 {}".format(sel)


def test_no_wish_remnant(dist):
    """「豆瓣想看」功能已下线，产物与源文件中不得残留实现代码。"""
    css, js = _extract(dist)
    forbidden = ["wish_acc", "wish-acc", "douban_wish", "loadWish", "renderWish"]
    for token in forbidden:
        assert token not in js, "JS 产物残留想看功能代码：{}".format(token)
        assert token not in css, "CSS 产物残留想看功能样式：{}".format(token)


def test_contrast_audit_entrypoint_present():
    """4.1 对比度审计脚本必须存在且可执行（CI 门禁依赖）。"""
    script = os.path.join(_ROOT, "scripts", "check_contrast.py")
    assert os.path.isfile(script), "缺少 scripts/check_contrast.py"


def test_undo_restore_endpoint_wired(dist):
    """3.3 撤销交互：前端必须调用 restore 动作，后端必须支持该动作。"""
    _, js = _extract(dist)
    assert "restoreExecHistory" in js, "前端缺少撤销执行历史实现"
    assert "action:'restore'" in js.replace('"', "'"), "前端未调用 restore 动作"

    routes = _read(os.path.join(_ROOT, "app_modules", "routes_history.py"))
    assert "restore_exec_history" in routes, "后端未接入 restore_exec_history"


def test_log_panel_has_expand_affordance(dist):
    """回归：日志面板折叠后必须有展开入口（曾只有 CSS 无 DOM，折叠即无法展开）。"""
    assert 'id="logToggleBtn"' in dist, "产物缺少日志面板展开按钮"
    assert "toggleLogPanel()" in dist, "展开按钮未绑定 toggleLogPanel"
    css, js = _extract(dist)
    assert ".log-toggle-btn" in css, "缺少展开按钮样式"
    assert "_syncLogPanelA11y" in js, "缺少折叠态 aria/入口同步逻辑"


def test_log_panel_not_auto_collapsed_on_desktop(dist):
    """回归：桌面态概览页不得无条件自动折叠日志栏（会压成 48px 且无入口）。

    对应 410498f / 58fcfd4 修复的同类 P0 缺陷：日志栏是桌面常驻功能区。
    """
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "isNarrowViewport" in tabs, "概览页自动折叠未做窄屏判定"
    assert "userSetLogPanelState" in tabs, "未尊重用户显式折叠选择"
    # 自动折叠必须带窄屏条件，禁止裸调用
    assert "tab === 'overview' && isNarrowViewport()" in tabs, \
        "桌面态仍会无条件折叠日志栏"


def test_build_fingerprint_is_content_based(dist):
    """回归：构建指纹必须是内容哈希，禁止写入易变的 git 提交号。

    背景：产物随仓库提交，CI「产物漂移检查」会在当前提交上重建并要求
    `git diff --quiet` 为空。若指纹写成 `git rev-parse HEAD`，则每次 commit
    （尤其是 merge commit）都会让重建产生一行 diff，门禁对任何 PR 恒失败。
    因此指纹必须只由构建输入决定，重建幂等。
    """
    m = re.search(r"^// (hash|sha):(\S*)$", dist, re.M)
    assert m, "产物缺少构建指纹注释"
    kind, value = m.group(1), m.group(2)
    assert kind == "hash", (
        "产物指纹应为内容哈希（hash:），当前为 {}:（提交号会导致漂移门禁恒失败）".format(kind)
    )
    assert re.fullmatch(r"[0-9a-f]{8,}", value), "指纹不是合法的十六进制内容哈希"

    # 构建脚本中不得再出现基于 HEAD 的指纹来源（仅检查可执行语句，注释中的
    # 「反面说明」不算违规）
    for name in ("build.sh", "build.py"):
        script = _read(os.path.join(_SRC, name))
        code_lines = []
        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue  # 注释：build.sh 在此说明了「为什么不能用 HEAD」
            code_lines.append(line)
        code = "\n".join(code_lines)
        assert "rev-parse" not in code, (
            "{} 仍从 git HEAD 派生指纹，会让 CI 产物漂移门禁恒为失败".format(name)
        )


def test_build_rebuild_is_idempotent(dist):
    """回归：同一份源文件重建两次，产物必须逐字节一致（漂移门禁的前提）。"""
    build_sh = _read(_BUILD_SH)
    if "hash:" not in build_sh:
        pytest.skip("build.sh 尚未使用内容指纹")
    if not shutil.which("bash"):
        pytest.skip("无 bash，跳过重建幂等校验")
    before = _read(_DIST)
    subprocess.run(["bash", _BUILD_SH], capture_output=True, text=True, timeout=120)
    after = _read(_DIST)
    assert before == after, "重建后产物发生变化，CI 产物漂移检查将恒失败"


def _read_agent_docs():
    """Agent 协作约束文件（AGENTS.md 等），缺失则视为空。

    这些文件是为 AI/NPC 协作者写的硬性约束，历史教训是「约束只在 prompt 里、
    没落库」时下一个 Agent 会重复踩坑（如批量截图触发上下文超限导致整个
    回合报废、把手工验证当 CI 门禁）。因此把关键条目固化为断言。
    """
    path = os.path.join(_ROOT, "AGENTS.md")
    return _read(path) if os.path.isfile(path) else ""


def test_agents_md_users_are_referenced_by_build_scripts():
    """构建脚本互相引用时，AGENTS.md 必须存在并指向 build.sh（唯一正确入口）。"""
    agents = _read_agent_docs()
    assert agents, "缺少 AGENTS.md：Agent 交付规范未落库"
    assert "static/src/build.sh" in agents, "AGENTS.md 未指明前端重建入口"


def test_agents_md_caps_screenshot_usage():
    """回归：Agent 约束必须包含读图配额。

    背景：本项目两次运行的失败原因都是「批量截图 → 上下文超限 →
    `500 Internal error: request entity too large`」，一次都没能产出 PR。
    只有把配额写进仓库内可读的约束文件，下一次运行才不会重犯。
    """
    agents = _read_agent_docs()
    assert "request entity too large" in agents, "未记录上下文超限的失败教训"
    assert "读图" in agents or "截图" in agents, "未约束截图/读图使用"
    assert re.search(r"(≤|<=|上限)\s*8\s*张", agents), "未给出读图数量硬上限"


def test_agents_md_requires_local_evidence_before_delivery():
    """回归：Agent 约束必须要求「先本地验证、再交付 PR」，而非依赖 CI 试错。"""
    agents = _read_agent_docs()
    assert "任务完成门禁" in agents, "缺少可比对的完成门禁章节"
    for token in ("本地", "提交 PR"):
        assert token in agents, "完成门禁缺少关键字：{}".format(token)


def test_local_verify_skill_landed():
    """回归：本地验证流程必须作为可复用的 skill 落库，而不是只活在会话里。"""
    skill = os.path.join(_ROOT, ".cnb", "skills", "dbauto-local-verify", "SKILL.md")
    assert os.path.isfile(skill), "缺少 .cnb/skills/dbauto-local-verify/SKILL.md"
    content = _read(skill)
    assert "api/sse" in content, "验证 skill 未禁止拉取 SSE 长连接"
    assert "nohup" in content, "验证 skill 未要求后台服务重定向输出"
    assert "static/src/build.sh" in content, "验证 skill 未指明产物重建入口"


# ============================================================
# 设置页视觉优化（issue #1 后续）回归断言
# ============================================================

def test_settings_page_has_jump_nav(dist):
    """设置页约 3 屏高，必须有分区快速定位入口（chips）且目标分组存在。"""
    assert 'class="set-jump"' in dist, "产物缺少设置页分区索引"
    assert 'aria-label="设置分区快速定位"' in dist, "分区索引缺少无障碍名称"
    for gid in ("setGroupSource", "setGroupTransfer", "setGroupPath", "setGroupAuth"):
        assert 'id="{}"'.format(gid) in dist, "缺少设置分组 {}".format(gid)
        assert 'data-target="{}"'.format(gid) in dist, "分区索引未覆盖 {}".format(gid)
    js = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "jumpToSettingsGroup" in js, "缺少分区跳转实现"
    assert "suspendSettingsJumpSpy" in js, (
        "滚动监听未做跳转期挂起，点击 chip 的高亮会被平滑滚动途中的中间分组覆盖"
    )


def test_settings_group_headers_have_description(dist):
    """分组头需含「名称 + 说明」，避免只有一行标题导致的信息量不足。"""
    css, _ = _extract(dist)
    for cls in (".set-group-ic", ".set-group-name", ".set-group-desc", ".set-group-badge"):
        assert cls in css, "缺少分组头样式 {}".format(cls)
    assert dist.count('class="set-group-desc"') >= 4, "分组说明缺失（应覆盖 4 个分组）"


def test_settings_savebar_replaces_empty_card(dist):
    """旧的「一张空卡 + 右下按钮」保存条应替换为带说明的保存条。"""
    assert 'class="settings-savebar"' in dist, "缺少设置页保存条"
    assert 'id="settingsDirtyHint"' in dist, "保存条缺少改动提示位"
    assert "旧称" not in dist
    # 旧的 save-card 空卡结构必须清除
    assert 'class="card save-card"' not in dist, "旧保存空卡仍残留"
    css, _ = _extract(dist)
    assert ".settings-savebar" in css
    # sticky 悬停方案会遮挡滚动中段内容，禁止回归
    savebar_block = re.search(r"\.settings-savebar\{(.*?)\}", css, re.S)
    assert savebar_block, "未找到 .settings-savebar 样式块"
    assert "position:sticky" not in savebar_block.group(1).replace(" ", ""), (
        "保存条不得使用 sticky 悬停（实测会盖住滚动中段的输入框与说明文字）"
    )


def test_settings_cards_equal_height(dist):
    """同一行卡片必须等高（此前 343/303/214 参差，底部留白不一致）。"""
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    assert "align-items:stretch" in compact, "设置网格未做等高拉伸"
    assert ".settings-group.settings-grid>.card{display:flex;flex-direction:column}" in compact, (
        "缺少设置卡片 flex 纵向布局（底部动作位依赖该布局）"
    )
    assert ".settings-group.settings-grid>.card>.set-card-actions{margin-top:auto}" in compact, (
        "卡片动作位未贴底，同行卡片底部留白不一致"
    )
    # 单卡分组需限制卡片宽度，避免巨型单卡
    assert "--set-card-max" in css, "单卡分组缺少宽度上限令牌"


def test_settings_field_hints_present(dist):
    """关键字段需有 .form-hint 说明（此前仅 TMDB 一处 inline 样式说明）。"""
    css, _ = _extract(dist)
    assert ".form-hint" in css, "缺少 .form-hint 样式"
    assert dist.count('class="form-hint"') >= 6, "字段说明覆盖不足"
    # 不应再残留 inline 的 12px 说明块写法
    assert 'style="font-size:12px;color:var(--text2)' not in dist, "仍有 inline 说明写法残留"


def test_settings_declares_missing_lock_icon(dist):
    """回归：设置页用到的图标必须在 sprite 中定义（此前 icon-lock 未定义，渲染为空白）。"""
    used = set(re.findall(r'href="#(icon-[a-z-]+)"', dist))
    defined = set(re.findall(r'id="(icon-[a-z-]+)"', dist))
    missing = sorted(used - defined)
    assert not missing, "产物引用了未定义的图标：{}".format(missing)


# ============================================================
# 概览页信息密度与优先级（issue #8）回归断言
# ============================================================

def _overview_js():
    return _read(os.path.join(_JS_DIR, "overview.js"))


def test_overview_first_screen_fits_in_one_viewport(dist):
    """概览页首屏工作区必须整体落在一屏内。

    现状问题（issue #8）：桌面 900px 视口下，KPI 条（4 张卡，高 93~132px 随窗口浮动）
    + 工作区 + 「热门推荐」竖排堆叠，内容高度 845~1135px，推荐面板要从 560/904 才开始，
    必然被推到折叠线以下；KPI 卡又在 1350px 处折成 2×2，导致模块高度在窗口变化时上下浮动。
    """
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    # 首屏两列栅格：左「概览+最近转存」，右「热门推荐」
    assert ".ov-top{display:grid" in compact.replace(" ", ""), "缺少概览首屏两列栅格"
    assert "--ov-rec-w" in css, "右侧推荐列宽度未令牌化，海报卡尺寸会随窗口漂移"
    assert ".ov-overview-body{display:grid" in compact.replace(" ", ""), "缺少「指标 | 状态」两列栅格"
    # 禁止回归到旧的「KPI 条 → 工作区 → 推荐」三段式竖排
    for dead in (".ov-kpi-bar", ".ov-kpi-card", ".ov-workspace", ".ov-main-col", ".ov-side-col"):
        assert dead not in css, "旧概览布局类 {} 仍残留，首屏会重新被推长".format(dead)
    assert "ov-panel-rec" in dist, "热门推荐面板缺失"


def test_overview_layout_uses_measured_width_not_viewport_media_queries(dist):
    """回归：概览页两列切换必须由「主列实测宽度」驱动，不得用 viewport 媒体查询。

    背景：Playwright 的 `viewport:{width:1440}` 会因滚动条让 `window.innerWidth=1425`，
    侧栏折叠、日志栏宽度同样会改变主列宽度；用 media query 判定会在 1280~1440
    这段「媒体查询命中但空间其实够用」的区间错误堆叠（本项目已实测复现）。
    """
    js = _overview_js()
    assert "initOverviewViewport" in js, "缺少概览自适应入口"
    assert "ResizeObserver" in js, "未使用 ResizeObserver 观测主列宽度"
    assert 'data-ov-narrow' in js, "未通过数据属性驱动堆叠布局"
    assert "OV_NARROW_PX" in js, "缺少窄布局阈值常量"
    # init 必须接线
    init_js = _read(os.path.join(_JS_DIR, "init.js"))
    assert "initOverviewViewport()" in init_js, "init() 未调用概览自适应入口"

    # 概览自己的断点不得再用 media query 控制两列结构（宽度类断点只允许调右列宽）
    main_css = _read(MAIN_CSS)
    overview_start = main_css.index("Overview Page（概览首页）")
    overview_css = main_css[overview_start:]
    overview_css = overview_css[:overview_css.index("日志面板折叠")]
    for m in re.finditer(r"@media\((max|min)-width:(\d+)px\)\{(.*?)\n\}", overview_css, re.S):
        body = m.group(3)
        assert ".ov-overview-body{grid-template-columns:minmax(0,1fr)}" not in body, (
            "概览两列结构仍由 viewport 媒体查询控制（{}px），中间宽度会误堆叠".format(m.group(2))
        )
        assert ".ov-stat-list" not in body, "指标列表的堆叠不应由 viewport 媒体查询控制"


def test_overview_marks_skipped_items_distinctly():
    """回归：跳过（幂等转存）不得再显示成绿色「已转存」。

    存储里 `status` 为 `exists`（已在库内跳过）或 `ok`，
    旧实现每行都渲染 `ov-badge-success`「已转存」，把跳过误报为成功。
    """
    js = _overview_js()
    assert "ovRecentState" in js, "缺少条目状态判定函数"
    for token in ("'exists'", "'ok'", "ov-badge-skip", "ov-badge-fail", "item.status"):
        assert token in js, "条目状态判定缺少分支：{}".format(token)
    assert "已存在跳过" in js, "跳过状态没有独立文案"
    # 旧的「无条件已转存」写法不得回归
    assert re.search(r"ov-badge ov-badge-success\">已转存<", js) is None, (
        "仍有无条件渲染「已转存」的写法"
    )


def test_overview_recent_list_has_no_triple_cap(dist):
    """回归：最近转存列表不得再「硬编码 8 条 + 查看全部 + 面板内滚动」三重截断。"""
    js = _overview_js()
    assert "OV_RECENT_LIMIT" in js, "渲染上限未抽成常量"
    m = re.search(r"OV_RECENT_LIMIT\s*=\s*(\d+)", js)
    assert m and int(m.group(1)) >= 10, "最近转存渲染上限过小（应为面板容量，而非 8 条）"
    assert "slice(0, 8)" not in js, "仍残留硬编码 8 条截断"
    assert "item.title.length > 30" not in js, "仍对标题做硬截断（应交给 CSS 省略）"
    # 面板内滚动应取消：列表交给「查看全部」出口
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    assert ".ov-panel-recent.ov-table-wrap{flex:1;min-height:0;overflow:auto}" in compact, (
        "最近转存面板未改为随内容等高（面板内滚动会让高度与左侧概览脱钩）"
    )
    # 旧的面板内滚动约束（.ov-table-wrap{overflow-x:auto;max-height:320px}）不得回归
    assert ".ov-table-wrap{overflow-x:auto;max-height:320px" not in compact, \
        "最近转存面板仍被限制在 320px 内滚动"


def test_overview_date_sorting_and_format():
    """回归：空日期不得插到列表最前，日期格式统一为 MM/DD HH:MM。"""
    js = _overview_js()
    assert "ovDateShort" in js, "缺少日期格式化函数"
    assert "if(da && !db) return -1" in js, "排序未对空日期做兜底（空串会排到最前）"
    assert "'—'" in js or "\\u2014" in js, "空日期缺少兜底显示"
    # 旧实现直接 date.slice(5,16)，且用 '-' 兜底（与真实日期不可区分）
    assert "item.date.slice(5, 16)" not in js, "仍残留无保护的日期切片"


def test_overview_recommendation_poster_uses_api_contract(dist):
    """回归：热门推荐必须按后端契约取名（items/poster/year/rating）。

    后端 `get_tmdb_list` 返回 `{items:[{poster,title,year,rating,...}]}`，
    旧实现读 `results/poster_path/release_date/vote_average`，
    结果每张卡都退化成 36px 占位图标。
    """
    js = _overview_js()
    assert "tmdb.items" in js, "未按契约读取 items 字段"
    assert "item.poster" in js and "item.year" in js and "item.rating" in js, \
        "未按契约读取 poster/year/rating 字段"
    # 卡片必须真有海报位（aspect-ratio 保证高度，不依赖图片加载成功）
    css, _ = _extract(dist)
    assert "aspect-ratio:2/3" in css.replace(" ", ""), "海报位缺少固定宽高比，图片缺失时会塌成一行"


def test_overview_no_panel_level_scrolling_content(dist):
    """回归：概览页不应出现「内容整体不足一屏但被容器裁掉」的度量。"""
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    # 概览内部的两列各自不滚动，滚动交给 .content
    assert ".ov-overview{display:flex;flex-direction:column" in compact, "概览容器未做纵向弹性布局"
    assert ".ov-side{display:flex;flex-direction:column" in compact, "概览右列未做纵向弹性布局"


def test_overview_skeleton_and_density(dist):
    """3.2 骨架屏必须覆盖概览首载（避免数字与行高跳变）。"""
    js = _overview_js()
    assert "renderOvSkeletons" in js, "缺少概览骨架屏"
    assert "ovState.hasData" in js, "缺少首载标记（每次切 Tab 都会重铺骨架）"
    css, _ = _extract(dist)
    assert ".ov-skel" in css, "缺少骨架屏样式"
    assert "prefers-reduced-motion" in css and "ovSkelPulse" in css, "骨架屏动画未做无障碍降级"
    # 概览的行式 KPI 结构
    assert ".ov-stat-row" in css, "缺少指标行样式"
    for dead in (".ov-kpi-label", ".ov-kpi-value-row", ".ov-kpi-trend", ".ov-kpi-schedule",
                 ".ov-status-dot", ".ov-st-ok", ".ov-rec-btn"):
        assert dead not in css, "概览/杂项死样式仍残留：{}".format(dead)


def test_overview_stacking_switch_is_deterministic(dist):
    """回归：概览两列切换只依赖 JS 阈值，不得散落视口断点。"""
    main_css = _read(MAIN_CSS)
    overview_start = main_css.index("Overview Page（概览首页）")
    overview_css = main_css[overview_start:]
    overview_css = overview_css[:overview_css.index("日志面板折叠")]
    assert '.app[data-ov-narrow="1"] .ov-overview-body{grid-template-columns:minmax(0,1fr)}' in overview_css, \
        "缺少窄主列的堆叠规则"
    assert "1350px" in overview_css  # 断点仍统一到全局阶梯（但仅用于调右列宽）


def test_overview_stat_rows_share_one_text_metric():
    """回归：概览指标行的文本度量必须统一，且其换行行数必须显式声明。

    背景：窄屏下调度行由「标签 + 时间 + 按钮」三段组成，三段总高超过基准行高
    （19.5px 行距时三行 = 58.5px），会与另外两行（54px）不一致；
    而 `min-height` 会掩盖高度差（不报错但内容溢出）。
    因此这里断言「统一行盒 + 已补偿的行数」，而不是断言整行高度相等。
    """
    main_css = _read(MAIN_CSS)
    start = main_css.index("Overview Page（概览首页）")
    block = main_css[start:main_css.index("日志面板折叠")]

    # 指标行内所有文本/按钮在窄屏断点下共用同一行盒（16px），换行行数因而可预期
    narrow = block[block.index("@media(max-width:640px)"):]
    narrow = narrow[:narrow.index("\n}")]
    narrow_compact = re.sub(r"\s+", "", narrow)
    for sel in (".ov-stat-value", ".ov-stat-sub,.ov-stat-sched", ".ov-kpi-btn"):
        assert sel + "{line-height:16px}" in narrow_compact or "line-height:16px}" in narrow_compact, \
            "窄屏下 {} 未统一 16px 行盒（换行后会与其他指标行不等高）".format(sel)

    # 调度行的换行行数补偿必须写明（3 段 = 3 行 × 16px + 2 × 9px 内边距 = 66px）
    assert "两段各自占一行时是 33px" in block, "未声明调度行的换行行数假设"
