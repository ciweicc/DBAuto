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


def _iter_css_rules(css):
    """粗粒度 CSS 规则扫描：产出 (选择器, 声明块)。

    产物是单行压缩结果，正则逐个 `{...}` 配对即可满足本用例需要
    （@media/@supports 的外层块会先被 yield，再被内层规则覆盖，不影响判断）。
    """
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


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
    """回归：日志栏不得被自动折叠（桌面态与窄屏态都不允许）。

    对应 410498f / 58fcfd4 修复的同类 P0 缺陷：日志栏是常驻功能区。
    该用例最初只约束「桌面态不得无条件折叠」，允许「窄屏 + 用户未选择」时自动折叠；
    但 issue #11 正是这条窄屏分支造成的 —— ≤1200px 打开页面即折叠、实时日志全不可见。
    因此现在收紧为：任何视口都不得自动折叠，只保留用户显式选择与展开入口。
    """
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    # 窄屏判定与用户选择记忆仍是展开/入口逻辑的依赖，必须保留
    assert "isNarrowViewport" in tabs, "缺少窄屏判定（展开入口依赖它）"
    assert "userSetLogPanelState" in tabs, "未尊重用户显式折叠选择"
    # 自动折叠（无论是否带窄屏条件）必须彻底移除
    assert "tab === 'overview' && isNarrowViewport()" not in tabs, \
        "仍保留「窄屏自动折叠日志栏」分支（issue #11 的成因）"
    # 残留的折叠调用只允许出现在「用户显式选择」的恢复分支里
    calls = [l.strip() for l in tabs.splitlines()
             if "collapseLogPanel()" in l and not l.strip().startswith("//")]
    assert calls == ["if(saved === '1') collapseLogPanel();"], \
        "存在 restoreLogPanelState 之外的折叠调用（疑似自动折叠残留）：{}".format(calls)
    assert "saved === '1'" in tabs, "恢复分支未按用户显式选择判断"


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


def test_settings_groups_pack_side_by_side(dist):
    """issue #15：分组不得再纵向独占整行。

    基线：.settings-groups 是 flex 纵向单列，1440px 容器下每个分组独占一行，
    组内单卡被 auto-fit 拉到 1340px，页面高 1756px、空白占比约 72%。
    """
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    groups = re.search(r"\.settings-groups\{(.*?)\}", css, re.S)
    assert groups, "未找到 .settings-groups 样式块"
    block_css = groups.group(1).replace(" ", "")
    assert "display:grid" in block_css, (
        ".settings-groups 必须用 grid 才能让分组并排，flex 纵向单列是其独占整行的根因"
    )
    assert "flex-direction:column" not in block_css, (
        ".settings-groups 回退成了 flex 纵向单列（分组会再次独占整行）"
    )
    assert compact.count("grid-template-columns:minmax(0,1fr)minmax(0,1fr)") >= 1, (
        ".settings-groups 未声明双列轨道，分组无法并排"
    )
    # 分组自身必须是网格容器，否则组内 grid-template-columns 不生效。
    # 注意 .settings-group 有多个样式块（边框块在前），必须遍历全部再判定。
    group_blocks = re.findall(r"\.settings-group\{(.*?)\}", css, re.S)
    assert group_blocks, "未找到 .settings-group 样式块"
    assert any("display:grid" in b.replace(" ", "") for b in group_blocks), (
        "<details class=\"settings-group\"> 默认 block，必须显式声明 display:grid"
    )
    # summary 必须横跨整组，否则会被当作网格子项塞进第一列
    assert ".settings-group>summary{grid-column:1/-1}" in compact, (
        "分组折叠头未横跨整组，会被当成网格子项塞进第一列"
    )


def test_settings_cards_not_force_equal_height(dist):
    """issue #15：同行卡片不得被强行等高。

    基线：「搜索服务」内容 149px，与 241px 的「TMDB 数据源」同行被 stretch
    拉到同高 337px，底部空出 202px。等高拉伸必须去掉，改为按内容高度排布。
    """
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    assert ".settings-group.settings-grid{align-items:start}" in compact, (
        "卡片仍被 align-items:stretch 强行等高（短卡会被撑出整块空白）"
    )
    assert ".settings-group.settings-grid{align-items:stretch}" not in compact, (
        "align-items:stretch 未清除干净，等高会再次撑出空白"
    )
    # 卡片宽度上限必须用 max-width 表达，不能写进 minmax 上界
    assert "max-width:var(--set-card-max" in compact, (
        "卡片缺少 max-width 宽度上限"
    )
    assert "minmax(300px,var(--set-card-max" not in compact.replace(" ", ""), (
        "卡片宽度上限被写进 minmax 上界，会把轨道钉死、同组卡片退回纵向堆叠"
    )


def test_settings_fields_two_column_on_wide_cards(dist):
    """宽卡（单列铺满）时字段走两列 —— 把「留空不修改」等提示占的纵向空间收掉。"""
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    assert 'class="set-fields"' in dist, "缺少卡片字段容器 .set-fields"
    assert dist.count('class="set-fields"') >= 5, "字段容器未覆盖设置页各卡片"
    assert ".set-fields{display:grid;grid-template-columns:minmax(0,1fr)" in compact, (
        "缺少 .set-fields 基础（单列）样式"
    )
    assert "@containersettings-card" in compact, (
        "缺少按卡片宽度切换字段列数的容器查询（卡片内两列依赖它）"
    )
    assert "container-name:settings-card" in compact, (
        "卡片未声明具名容器，容器查询无法命中"
    )
    # 奇数个字段的最后一项必须横跨整行，否则两列布局末尾会空出半格
    assert ".set-fields>.form-group:last-child:nth-child(odd){grid-column:1/-1}" in compact, (
        "两列表段时末尾字段未跨列，会留出半格空白"
    )


def test_settings_dom_script_present(dist):
    """设置页空白度量脚本必须落库，且可被 JSON 化的机器人报告消费。"""
    path = os.path.join(_ROOT, "scripts", "check_settings_dom.py")
    assert os.path.isfile(path), "缺少设置页 DOM 度量脚本 scripts/check_settings_dom.py"
    body = _read(path)
    assert "MAX_CARD_BLANK" in body and "blankOf" in body, "脚本未度量卡片底部空白"
    assert "--json" in body and "--serve" in body, "脚本缺少 --json / --serve 入口"
    assert "return 1 if problems else 0" in body, "脚本未在发现问题时返回非零退出码"


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
    # 首屏两列栅格：左「概览+最近转存」，右「服务状态 + 待办」
    assert ".ov-top{display:grid" in compact.replace(" ", ""), "缺少概览首屏两列栅格"
    assert "--ov-rec-w" in css, "右列宽度未令牌化，首屏两列会随窗口漂移"
    assert ".ov-overview-body{display:grid" in compact.replace(" ", ""), "缺少「指标 | 状态」两列栅格"
    # 禁止回归到旧的「KPI 条 → 工作区 → 推荐」三段式竖排
    for dead in (".ov-kpi-bar", ".ov-kpi-card", ".ov-workspace", ".ov-main-col", ".ov-side-col"):
        assert dead not in css, "旧概览布局类 {} 仍残留，首屏会重新被推长".format(dead)


def test_discovery_quick_panel_removed(dist):
    """回归（issue #15）：概览页不得再有「发现快捷」横滑条。

    背景：TMDB 整块并入概览页（issue #14）之后，首屏上方的「发现快捷」横滑条
    与下方 `#tmdbPanel` 发现区**共用同一份热门数据**（同一接口、同样的海报 + 标题 + 评分），
    只是把同一批内容渲染了两遍（一份 88px 小卡、一份完整网格），
    用户判定为「内容重复」，要求整栏去掉。
    """
    js = _overview_js()
    init_js = _read(os.path.join(_JS_DIR, "init.js"))
    for dead_js in ("renderOvRecs", "ov-rec-card", "ovRecScroll", "ov-rec-skel"):
        assert dead_js not in js, "「发现快捷」残留逻辑：{}".format(dead_js)
    assert "ovRecScroll" not in init_js, "init 仍在初始化「发现快捷」"
    # 概览页不再为「发现快捷」单独拉一份 TMDB 列表（数据源收敛到发现区一处）
    assert "/api/tmdb/list" not in js, "概览页仍在重复拉取 TMDB 列表"
    # 注意用类名精确匹配：`.ov-panel-recent`（最近转存，保留）是它的前缀
    for dead in (r"\.ov-panel-rec(?!ent)", r"\.ov-rec-scroll\b", r"\.ov-rec-card\b", r"\.ov-rec-empty\b"):
        assert not re.search(dead, dist), "「发现快捷」残留 DOM/样式：{}".format(dead)
    assert "发现快捷" not in dist, "「发现快捷」标题仍残留"
    # 去重后「发现区」必须仍在概览页内（不能顺手把发现能力一起删掉）
    assert 'id="tmdbPanel"' in dist, "概览页内的发现区被误删"


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


def test_overview_recent_has_single_history_entry():
    """回归：概览「最近转存」面板只能有一个指向历史页的入口。

    面板头「查看全部 →」+ 面板底「打开历史 →」曾是两个去向相同的链接，
    同屏重复导航（用户反馈：「这 4 个箭头指向的跳转同时指向历史页，我只需要保留一个」）。
    保留面板头的「查看全部 →」作为唯一出口。
    """
    html = _read(os.path.join(_SRC, "body.html"))
    # 源文件：最近转存面板（ov-panel-recent）内 switchTab('history') 只允许出现 1 次
    start = html.index("ov-panel-recent")
    end = html.index("ov-panel-discovery")
    panel = html[start:end]
    assert panel.count("switchTab('history')") == 1, (
        "最近转存面板内出现了多个历史入口（应只保留面板头的「查看全部」）"
    )
    assert "查看全部" in panel, "唯一出口「查看全部」被误删"
    # 底部重复入口及其样式不得回归
    for dead in ("打开历史", "ov-recent-foot", "ov-recent-note"):
        assert dead not in html, "重复的历史入口残留：{}".format(dead)
    css = _read(MAIN_CSS)
    for dead in (".ov-recent-foot", ".ov-recent-note"):
        assert dead not in css, "重复入口的死样式残留：{}".format(dead)


def test_product_dist_has_no_duplicate_history_link(dist):
    """产物层面复核：整份产物中指向历史页的链接不超过 1 个（概览面板头）。"""
    assert dist.count(">查看全部 →</button>") == 1, "产物里「查看全部」入口数量不为 1"
    assert "打开历史" not in dist, "产物里仍残留「打开历史」入口"


def test_overview_date_sorting_and_format():
    """回归：空日期不得插到列表最前，日期格式统一为 MM/DD HH:MM。"""
    js = _overview_js()
    assert "ovDateShort" in js, "缺少日期格式化函数"
    assert "if(da && !db) return -1" in js, "排序未对空日期做兜底（空串会排到最前）"
    assert "'—'" in js or "\\u2014" in js, "空日期缺少兜底显示"
    # 旧实现直接 date.slice(5,16)，且用 '-' 兜底（与真实日期不可区分）
    assert "item.date.slice(5, 16)" not in js, "仍残留无保护的日期切片"


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


# ---------------------------------------------------------------- 日志面板（issue #11）
def test_log_panel_not_auto_collapsed_on_any_viewport(dist):
    """回归：日志栏不得在校窄视口下自动折叠（issue #11 的直接成因）。

    `switchTab` 曾以「概览页 + 窄屏 + 用户未显式选择」为条件自动 `collapseLogPanel()`，
    结果 ≤1200px 打开概览页时日志栏被压成 48px 轨道、正在运行的任务日志一条不可见。
    现在自动折叠已彻底移除，"给内容更多空间"交由用户显式点击折叠按钮。
    """
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "collapseLogPanel()" in tabs, "缺少折叠函数"
    # switchTab 内的自动折叠必须已移除
    switch_body = tabs[tabs.index("function switchTab"):tabs.index("// 方向键在标签间导航")]
    # 只保留可执行代码：注释里会提到历史缺陷，不能算「仍在自动折叠」
    code = "\n".join(l for l in switch_body.splitlines() if not l.strip().startswith("//"))
    assert "collapseLogPanel()" not in code, \
        "switchTab 仍会自动折叠日志栏（窄屏打开页面即看不到实时日志）"


def test_log_panel_defaults_expanded_and_restores_user_choice(dist):
    """回归：首屏默认展开，只在用户自己折叠过时才恢复折叠。"""
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "function restoreLogPanelState" in tabs, "缺少折叠态恢复函数"
    body = tabs[tabs.index("function restoreLogPanelState"):]
    body = body[:body.index("\n}")]
    # 只有显式 '1' 才折叠；其余（含无记录）一律展开
    assert "saved === '1'" in body, "恢复逻辑未按显式选择判断"
    assert "classList.remove('collapsed')" in body, "默认分支未清理折叠态"

    log_js = _read(os.path.join(_JS_DIR, "log.js"))
    assert "restoreLogPanelState()" in log_js, "DOMContentLoaded 未调用折叠态恢复"


def test_log_panel_narrow_expand_opens_drawer(dist):
    """回归：窄屏点展开/FAB 必须进入 .open 抽屉态。

    ≤1200px 时日志栏是 `translateX(100%)` 的覆盖层，只去掉 `.collapsed`
    仍然是屏幕外状态，展开动作会「看起来无效」。
    """
    log_js = _read(os.path.join(_JS_DIR, "log.js"))
    body = log_js[log_js.index("function expandLogPanel"):]
    body = body[:body.index("\n}")]
    assert "isNarrowViewport()" in body, "expandLogPanel 未处理窄屏抽屉态"
    assert "classList.add('open')" in body, "expandLogPanel 未打开覆盖层抽屉"


def test_log_panel_capacity_not_hardcoded(dist):
    """回归：日志区高度必须随视口增长，不得写死小上限。

    旧实现 `max-height:340px` 在 900px 高的屏幕上只用了约 1/3 屏，
    用户感知为「实时任务日志显示不完全」（issue #11）。
    """
    main_css = _read(MAIN_CSS)
    start = main_css.index("/* Log */")
    block = main_css[start:start + 1200]
    # 去注释后只比对真实声明，避免注释里提到旧值就误判
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S).replace(" ", "")
    assert "max-height:340px;" not in block, "日志区仍写死 340px 上限"
    assert "max-height:max(340px,calc(100dvh-320px))" in block, \
        "日志区高度未改为随视口增长"


def test_log_panel_pins_to_latest(dist):
    """回归：日志必须定位到最新一条，且折行续行有缩进。

    旧实现 `renderLog` 只渲染、不定滚动位置，状态同步后 scrollTop 停在 0
    （最旧一行），最新日志在数屏之外 —— 表现为「日志无法显示完全」。
    """
    log_js = _read(os.path.join(_JS_DIR, "log.js"))
    # renderLog / applyLogSearch 都必须回到末尾
    render = log_js[log_js.index("function renderLog"):log_js.index("function appendLine")]
    assert "scrollTop=el.scrollHeight" in render.replace(" ", ""), "renderLog 未定位到最新"
    search = log_js[log_js.index("function applyLogSearch"):log_js.index("function updateLogHint")]
    assert "scrollTop" in search, "搜索后未定位滚动位置"
    # 折行标记 + 续行缩进
    assert "function markWrap" in log_js, "缺少折行检测（续行缩进无法生效）"
    assert "wrapped" in log_js, "未标记折行行"
    main_css = _read(MAIN_CSS)
    assert ".log-line.wrapped" in main_css, "缺少折行续行缩进样式"
    # 缩进方式：padding-left 推开续行 + text-indent 回拉首行（minify 可能重排空格）
    wrap_rule = [l for l in main_css.splitlines() if ".log-line.wrapped" in l]
    assert wrap_rule, "缺少 .log-line.wrapped 规则"
    rule = wrap_rule[0].replace(" ", "")
    assert "--log-wrap-pad" in rule, "续行缩进未使用 --log-wrap-pad 变量"
    assert "text-indent:calc(-1*var(--log-wrap-pad))" in rule, \
        "续行缩进未通过 text-indent 回拉首行（首行会整体右移）"


def test_log_panel_has_position_hint(dist):
    """回归：日志区需常驻位置提示，暂停时锁定并给出可读文案。"""
    assert 'id="logHint"' in dist, "产物缺少日志位置提示节点"
    log_js = _read(os.path.join(_JS_DIR, "log.js"))
    assert "function updateLogHint" in log_js, "缺少提示更新函数"
    for token in ("已定位到末尾", "已暂停"):
        assert token in log_js, "提示文案缺少 {!r}".format(token)
    main_css = _read(MAIN_CSS)
    assert ".log-hint" in main_css, "缺少提示行样式"


def test_log_panel_drawer_reachable_from_every_entry(dist):
    """回归：窄屏抽屉必须能从「首屏 / 缩窄窗口 / 点 FAB」三条路径真正打开。

    issue #11 二次复发（2026-09-15 用户第二次反馈「问题依旧存在」）的根因：
    ≤1200px 时 `.log-panel` 是 `translateX(100%)` 的覆盖层，只有 `.open`
    能让它回到可视区。但三条入口都可能只切 `.collapsed` 而漏掉 `.open`：

      1. 从宽屏（用户折叠过）把窗口缩到 ≤1200px —— `.collapsed` 残留，
         抽屉被 `width:48px !important` 压回轨道，日志一条看不到；
      2. 点 FAB (`toggleLogDrawer`) —— 旧实现只 `toggle('open')`，
         若面板同时带 `.collapsed`，视觉上仍是被压扁的轨道；
      3. 首屏恢复 —— 折叠记忆是宽屏点的，窄屏照搬即等于藏起日志栏。

    这里断言三处归一化逻辑存在，且 `.open` 抽屉必须解除 min-width 束缚。
    """
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "function normalizeLogPanelForViewport" in tabs, \
        "缺少宽窄视口切换时的日志栏状态归一化函数"
    norm = tabs[tabs.index("function normalizeLogPanelForViewport"):]
    norm = norm[:norm.index("\n}")]
    assert "classList.remove('collapsed')" in norm, \
        "归一化未处理「窄屏残留折叠态」（日志栏会停在屏幕外）"
    assert "classList.remove('open')" in norm, \
        "归一化未处理「宽屏残留抽屉态」"
    # 首屏恢复也必须做窄屏归一化
    restore = tabs[tabs.index("function restoreLogPanelState"):]
    restore = restore[:restore.index("\n}")]
    assert "isNarrowViewport()" in restore, \
        "restoreLogPanelState 未在窄屏丢弃遗留折叠记忆"

    init_js = _read(os.path.join(_JS_DIR, "init.js"))
    drawer = init_js[init_js.index("function toggleLogDrawer"):]
    drawer = drawer[:drawer.index("\n}")]
    assert "classList.add('open')" in drawer, "toggleLogDrawer 未打开抽屉"
    assert "classList.remove('collapsed')" in drawer, \
        "toggleLogDrawer 打开抽屉时未清除折叠态（会被 48px !important 压回轨道）"

    # resize 路径：宽→窄必须立刻归一化，不能等防抖
    log_js = _read(os.path.join(_JS_DIR, "log.js"))
    resize = log_js[log_js.index("window.addEventListener('resize'"):]
    resize = resize[:resize.index("}, 150);")]
    assert "normalizeLogPanelForViewport()" in resize, \
        "resize 未归一化日志栏状态（宽→窄会残留折叠态）"

    # CSS：`.open` 抽屉必须解除基础态的 min-width:260px 束缚，
    # 否则 1024 断点的兜底规则会让宽度算成负值、抽屉内容被压没。
    main_css = _read(MAIN_CSS)
    # 取「块首」那条 .log-panel.open{ 规则（媒体查询里的缩进版本另算）
    starts = [i for i, l in enumerate(main_css.splitlines())
              if l.startswith(".log-panel.open{")]
    assert starts, "缺少 .log-panel.open 抽屉规则"
    lines = main_css.splitlines()
    block = []
    for l in lines[starts[0]:]:
        block.append(l)
        if l.rstrip().endswith("}"):
            break
    rule = re.sub(r"/\*.*?\*/", "", "".join(block), flags=re.S).replace(" ", "")
    assert "position:fixed" in rule, ".open 抽屉未脱离网格轨道（仍是静态定位）"
    assert "transform:none" in rule, ".open 抽屉未把自己移回可视区"
    assert "min-width:0" in rule, \
        ".open 抽屉未解除 min-width:260px，1024 断点下会被压成负宽度"
    assert ".log-panel.open.collapsed{" in main_css, \
        "缺少 .open.collapsed 兜底规则（叠态时仍会塌回 48px 轨道）"


def test_ultrawide_content_has_no_blank_gutter(dist):
    """回归：超宽屏下主列不得留下大片空白带。

    `.app` 第三列是 `minmax(0,1fr)`，2560px 下主列宽 2020px；而
    `.content{max-width:1440px;margin:0 auto}` 只把内容居中 ——
    结果是主列左侧空出约 290px 空白，日志栏却被顶到屏幕最右边，
    看起来就像「日志栏跑到屏幕外」（用户 2560px 截图的真实观感）。
    修复方式：≥1901px 把主列夹到内容实际需要的宽度，多余空间交给
    grid 居中分配。
    """
    main_css = _read(MAIN_CSS)
    start = main_css.index("@media(min-width:1901px)")
    block = main_css[start:start + 400]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.S).replace(" ", "")
    assert "grid-template-columns:200pxminmax(0,1440px)340px" in block, \
        "超宽屏未把主列夹到 1440px（空白带会复现）"
    assert "justify-content:center" in block, \
        "超宽屏未居中分配剩余空间"


def test_log_panel_dom_audit_entrypoint_present():
    """issue #11 的 DOM 度量走查脚本必须存在且可执行（本地取证入口）。"""
    script = os.path.join(_ROOT, "scripts", "check_log_panel_dom.py")
    assert os.path.isfile(script), "缺少 scripts/check_log_panel_dom.py"
    src = _read(script)
    for token in ("panelVisible", "atBottom", "wrappedCount", "check_log_panel_dom.py"):
        assert token in src, "走查脚本缺少断言 {}".format(token)


# ------------------------------------------------ TMDB 并入概览页（issue #14 第三轮）
def test_tmdb_merged_into_overview_single_page(dist):
    """回归：TMDB 必须只有一块界面，且这块界面就在概览页里。

    用户诉求（issue #14 追问）：「把 TMDB 页面的完整筛选栏原版移植到概览页，
    这样就可以少一个页面」。因此：
      - 产物里不得再有独立的 #pageTmdb 页面容器；
      - 发现分区 #tmdbPanel 必须落在 #pageOverview 内部；
      - 导航（侧栏 / 底栏 / 页面 id 表）里都不再有 tmdb 目的地。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="pageTmdb"' not in body, "独立 TMDB 页面容器仍然存在（应已并入概览页）"
    # 只查「代码里的引用」，不查注释文案：注释里提到历史页面名是说明，不是残留
    compact = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    assert "pageTmdb" not in compact, "源文件仍引用旧页面容器 #pageTmdb"
    assert 'id="pageTmdb"' not in body and "getElementById('pageTmdb')" not in dist, \
        "产物仍引用旧页面容器 #pageTmdb"

    ov_start = body.index('id="pageOverview"')
    ov_end = body.index("<!-- ===== 手动转存 ===== -->")
    overview = body[ov_start:ov_end]
    assert 'id="tmdbPanel"' in overview, "发现分区 #tmdbPanel 不在概览页内"
    for node in ('id="tmdbGrid"', 'id="tmdbFilters"', 'id="tmdbSelBar"',
                 'id="tmdbPagination"', 'id="tmdbMediaType"', 'id="tmdbListType"',
                 'id="tmdbGenreChips"'):
        assert overview.count(node) == 1, "{} 不在概览页内或出现多次".format(node)

    # 导航里不得再有 tmdb
    assert 'data-tab="tmdb"' not in body, "侧栏仍有「发现」导航项"
    assert "TAB_IDS" not in body
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "tmdb:" not in tabs.split("var PAGES")[0], "PAGE_IDS 仍然映射 tmdb 页面"
    m = re.search(r"var BOTTOM_NAV = \[(.*?)\]", tabs)
    navs = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    # 断言的是「导航集合」而不是写死的项数：tmdb 必须不在其中，
    # 且导航内容与预期完全一致（issue #27：新增「媒体库」并置于「历史」之前，
    # 与媒体库页内「网盘文件置顶」的优先级一致，共 6 项）。
    assert "tmdb" not in navs, "底部导航不应包含 tmdb"
    assert navs == ["overview", "manual", "schedule", "library", "history", "settings"], \
        "底部导航项与预期不一致: {}".format(navs)


def test_tmdb_filters_are_the_original_set(dist):
    """回归：概览页里的筛选栏必须是**原版那套**（不是精简过的仿制品）。

    原版字段：分类 / 榜单类型 / 地区 / 语言 / 排序 / 最低评分 / 年份 / 流媒体平台多选
    + 类型（genre）筛选 chips。少一个都算「移植不完整」。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    for token in ('id="tmdbMediaType"', 'id="tmdbListType"', 'id="tmdbRegion"',
                  'id="tmdbLanguage"', 'id="tmdbSort"', 'id="tmdbMinRating"',
                  'id="tmdbYear"', 'id="tmdbProviderWrap"', 'id="tmdbProviderBtn"',
                  'id="tmdbProviderPanel"', 'id="tmdbGenreChips"',
                  'id="tmdbFilterToggle"', 'id="tmdbTransferBtn"'):
        assert token in body, "筛选/操作控件缺失：{}".format(token)
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    for fn in ("toggleProviderDropdown", "toggleProviderOption", "clearProviderSelection",
               "selectAllTmdbVisible", "clearTmdbSelection", "transferTmdbSelection",
               "tmdbPrevPage", "tmdbNextPage", "onTmdbGridKeydown", "toggleTmdbFilters"):
        assert "function " + fn in js, "原版交互函数缺失：{}".format(fn)


def test_tmdb_scoped_lookup_keeps_page_level_ids_global(dist):
    """回归：作用域查找只能作用于「TMDB 自己的」控件，页面级 id 必须保持全局。

    背景：为了让同一套 ID 在概览页里工作，tmdb.js 的 DOM 访问统一走 getId()。
    如果把 getElementById() 一律改写成「先查 #tmdbPanel 作用域」，
    页面级节点（#log 日志区 / #stopBtn）会解析成 null ——
    实测在 `getId('log').textContent=''` 上抛 TypeError，转存直接中断。
    """
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    assert "DSC_GLOBAL_IDS" in js, "缺少页面级 id 白名单"
    for gid in ("log", "stopBtn"):
        assert "{}:1".format(gid) in js, "页面级 id {} 未列入全局白名单".format(gid)
    assert "function _dscLocalId" in js, "缺少作用域 id 推导函数"
    # 作用域查找必须带「找不到就回退全局」的兜底
    body = js[js.index("function getId("):]
    body = body[:body.index("\n}")]
    assert "document.getElementById(id)" in body, "getId 缺少全局兜底"


def test_tmdb_enter_keeps_filters_instead_of_resetting(dist):
    """回归：从其它 Tab 切回概览时，不得把发现区的筛选/页码打回默认。

    旧版每次 switchTab('tmdb') 都会无脑 loadTmdbList()（回第 1 页）；
    TMDB 常驻概览后若沿用该逻辑，用户翻到第 5 页、去设置再回来会「凭空回到第 1 页」。
    """
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    assert "function enterTmdbDiscovery" in js, "缺少发现区进入函数"
    assert "dscState.entering" in js, "缺少「正在进入」守卫"
    # 进入期间 onTmdbFilterChange 只同步控件态，不重载列表
    change = js[js.index("function onTmdbFilterChange"):]
    change = change[:change.index("\n}")]
    assert "dscState.entering" in change, "onTmdbFilterChange 未识别「正在进入」态"
    assert "syncTmdbDiscoverControls();" in change, "进入态未同步筛选控件可用态"
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "updateTmdbVisibility" in tabs, "switchTab 未在切回概览时刷新发现区状态"


def test_tmdb_visibility_gates_scroll_and_back_to_top(dist):
    """回归：无限滚动与「回到顶部」必须以「发现区是否可见」为准。

    发现区常驻概览页时，用「页面是否激活」判断会让滚动加载在概览页顶部就被触发；
    回顶按钮也会浮在概览工作区上。
    """
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    assert "function updateTmdbVisibility" in js, "缺少发现区可见性判定"
    assert "function tmdbBlockVisible" in js, "缺少可见性读取"
    scroll = js[js.index("function tmdbOnScroll"):]
    scroll = scroll[:scroll.index("\n}")]
    assert "tmdbBlockVisible()" in scroll, "滚动加载未按发现区可见性判定"
    assert "pageTmdb" not in scroll, "滚动加载仍在判断旧的 #pageTmdb"
    # 翻页回顶应回到发现区顶部，而不是整页顶部
    nx = js[js.index("function tmdbNextPage"):]
    nx = nx[:nx.index("\n}")]
    assert "scrollTmdbPanelIntoView()" in nx, "翻页未回到发现区顶部（会把用户甩出发现区）"
    # 快照/命令面板统一走 jumpToDiscovery（同页滚动，不再切页面）
    ov = _read(os.path.join(_JS_DIR, "overview.js"))
    assert "function jumpToDiscovery" in ov, "概览缺少「去发现」入口函数"
    assert "jumpToDiscovery()" in _read(os.path.join(_JS_DIR, "globals.js")), \
        "命令面板的「前往发现」未接线到同页入口"


def test_discovery_adv_block_shares_disabled_state(dist):
    """回归：筛选栏折叠时，高级筛选控件的表单语义必须一起关闭。

    折叠只是 CSS display:none，Tab 键仍会依次走进 5 个不可见控件（键盘用户凭空多按十几次）。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="tmdbAdv"' in body, "缺少高级筛选分组容器"
    assert "<fieldset" in body.split('id="tmdbAdv"')[0][-200:] + body.split('id="tmdbAdv"')[1][:200], \
        "高级筛选分组不是 fieldset（无法一次性关闭表单语义）"
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    body_fn = js[js.index("function toggleTmdbFilters"):]
    body_fn = body_fn[:body_fn.index("\n}")]
    assert "tmdbAdv" in body_fn and "disabled" in body_fn, \
        "toggleTmdbFilters 未同步高级筛选控件的 disabled"
    # 原版字段：options 字段名兜底（曾因读到 countries/languages 以外的字段名让整段初始化中断）
    assert "opts.regions || opts.countries" in js or "opts.countries || opts.regions" in js, \
        "地区选项读取未兼容多字段名"
    assert "(opts.languages || [])" in js or "opts.languages || []" in js, \
        "语言选项读取未做缺失兜底"


def test_merged_frontend_has_no_second_tmdb_surface(dist):
    """回归：产物里 TMDB 只能有一套表面（同构节点必须唯一）。"""
    for token in ('id="tmdbGrid"', 'id="tmdbFilters"', 'id="tmdbSelBar"',
                  'id="tmdbPagination"', 'id="tmdbMediaType"', 'id="tmdbListType"'):
        assert dist.count(token) == 1, "{} 在产物中出现 {} 次".format(token, dist.count(token))
    # 「热门推荐」旧面板（第二块海报墙）不得复活
    assert "热门推荐" not in dist, "旧的「热门推荐」面板标题仍残留"


def test_discovery_selection_bar_is_viewport_fixed(dist):
    """回归（issue #15 第 3 轮）：发现区多选操作条必须相对**视口**定位。

    真凶是 CSS 包含块：`position:fixed` 的元素只要祖先里有 filter /
    backdrop-filter / transform / perspective / contain:paint，就不再相对视口定位。
    发现区的面板（.ov-panel）与卡片（.tmdb-card）都带 backdrop-filter，
    于是选中栏被钉在页面深处 —— 实测 1440×900 下 top=1361（视口高只有 900），
    用户点选资源后**看不到转存按钮**（issue 原话）。

    这里做静态防复发：产物必须同时具备
      1. 选中栏自身不引入 filter / backdrop-filter / transform / perspective；
      2. `.ov-panel`（发现分区容器）不再带 backdrop-filter；
      3. JS 侧有内联样式兜底（内联优先级高于同权重的类规则，正常样式表无法再降级它）。
    真实视觉/命中测试由 scripts/check_discovery_dom.py 负责（9 组视口）。
    """
    css, _ = _extract(dist)
    # 1. 选中栏本身的定位约束
    guard = css[css.index("#tmdbSelBar{") + 1:]
    guard = guard[:guard.index("}")]
    for prop in ("filter:none", "backdrop-filter:none", "transform:none", "perspective:none"):
        assert prop in guard, "#tmdbSelBar 缺少 `{}` 兜底（会被祖先/自身降级成非视口定位）".format(prop)

    def _rule(selector):
        i = css.index(selector)
        return css[i:css.index("}", i)]

    # 2. 发现分区容器不得再制造包含块
    panel_rule = _rule(".ov-panel{")
    assert "backdrop-filter" not in panel_rule, (
        ".ov-panel 又加回了 backdrop-filter：它会成为 fixed 后代的包含块，"
        "发现区的转存栏会再次跑出视口（issue #15 第 3 轮）"
    )

    # 3. 全量样式里不得再出现「把选中栏和带模糊的相邻选择器写在同一条规则」的写法：
    #    产物经 esbuild minify 会去掉注释，因此用「规则选择器列表 → 声明块」的结构化扫描，
    #    逐个找出含 backdrop-filter 的规则，检查它的选择器里有没有 .tmdb-sel-bar。
    for sel, decl in _iter_css_rules(css):
        if "backdrop-filter:var(--blur)" in decl:
            assert ".tmdb-sel-bar" not in sel, (
                "共享 backdrop-filter 规则又包含了 .tmdb-sel-bar：{}".format(sel))

    # 4. 同理，任何一条带 filter / backdrop-filter 的规则都不得命中选中栏
    for sel, decl in _iter_css_rules(css):
        if "filter:none" in decl and "#tmdbSelBar" in sel:
            assert "!important" in decl, "#tmdbSelBar 的 filter 兜底必须带 !important"


def test_discovery_selection_bar_bounds_and_layering(dist):
    """回归：选中栏的边界按内容区实测、层级高于 FAB，且 JS 有兜底。"""
    css, _ = _extract(dist)
    assert "--z-selbar" in css, "缺少 --z-selbar 层级令牌"
    # 选中栏层级必须高于 FAB（150）与回到顶部（400）——否则右下角悬浮按钮会压住「开始转存」
    tokens = _read(os.path.join(_SRC, "styles", "tokens.css"))
    def _z(name):
        s = tokens[tokens.index(name):]
        return int(s[s.index(":") + 1:s.index(";")].strip())

    assert _z("--z-selbar") > _z("--z-backtop") > _z("--z-fab"), \
        "--z-selbar 必须高于 --z-backtop / --z-fab"
    assert "z-index:var(--z-selbar)" in css, "选中栏未使用 --z-selbar"
    # 选中态下两个 FAB 让位
    assert "body:has(#tmdbPanel.sel-active) #tmdbBackToTop" in css, "回到顶部按钮未在选中态让位"
    assert "body:has(#tmdbPanel.sel-active) .log-fab" in css, "日志 FAB 未在选中态让位"

    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    assert "function tmdbSelBarBounds" in js, "缺少选中栏边界计算"
    assert "function applyTmdbSelBarBounds" in js, "缺少选中栏边界应用"
    assert "function bindTmdbSelBarResize" in js, "缺少选中栏 resize 监听绑定"
    assert "bindTmdbSelBarResize()" in js[js.index("async function initTmdbPage"):], \
        "initTmdbPage 未绑定选中栏 resize 监听"
    assert "function tmdbEnsureSelBarContainingBlock" in js, \
        "缺少选中栏内联样式兜底（正常样式表无法再把它降级成非视口定位）"
    # 移动端底部导航高度不应再硬编码，改为实测
    bounds = js[js.index("function tmdbSelBarBounds"):]
    bounds = bounds[:bounds.index("\nfunction applyTmdbSelBarBounds")]
    assert "bottom-nav" in bounds, "未按底部导航实测高度让位"
    assert "76px" not in bounds, "仍在硬编码移动端让位高度"


def test_overview_hero_has_no_dead_blank(dist):
    """回归（issue #15 第 4 轮）：首屏两列不得出现整块纯空白。

    真凶是「用最长的邻居决定所有人高度」：
      - `.ov-top{align-items:stretch}` 让左列「转存概览」被迫与右列
        「最近转存」（固定 10 行 ≈ 480px）同高，而左列内容只有约 210px；
      - `.ov-side-todo{flex:1}` 让「待办事项」块吃掉右列全部剩余高度，
        常态只有一条「系统运行正常，暂无待办」（32px），块却被拉到 360px。

    实测（1440×900）：左列底部死白 279px、待办块内死白 291px。
    修复：左列按内容取高（flex + min-height 对齐 4 行表格）、待办块不拉伸、
    表格行数由 OV_RECENT_ROWS_FIT 固定为 4（不再由 CSS 高度反推，避免正反馈）。

    真实视觉度量由 scripts/check_overview_dom.py 的 leftBlank / todoBlank 断言负责
    （9 组视口，阈值 24px；修复前在 1440×900 等视口会报 271/291px）；
    这里做静态防复发，锁住「不该再出现的写法」。
    """
    css, _ = _extract(dist)
    js = _read(os.path.join(_JS_DIR, "overview.js"))

    # 1. 首屏两列不得再各自按「最长邻居」撑高：左列要有内容下限，
    #    且右列行数必须与它有明确对应关系（常量就在 JS 里，见下）。
    i = css.index(".ov-overview{")
    rule = css[i:css.index("}", i)]
    assert "min-height" in rule, (
        ".ov-overview 缺少 min-height：左列会塌到内容高，"
        "与右侧「最近转存」高度差距又变成死白"
    )

    # 2. 「待办事项」块不得再 flex:1 拉伸 —— 这正是 291px 死白的直接原因
    i = css.index(".ov-side-todo{")
    rule = css[i:css.index("}", i)]
    assert "flex:1" not in rule.replace(" ", ""), (
        ".ov-side-todo 又加回了 flex:1：待办只有一条时会在块内造出约 290px 死白"
        "（issue #15 第 4 轮）"
    )

    # 3. 行数改为固定值：不能再由「实测高度反推行数」——
    #    那会形成「面板越高 → 留的行越多 → 面板越高」的正反馈
    #    （实测 1366×900 从 268px 一路涨到 384px）。
    assert "OV_RECENT_ROWS_FIT" in js, "缺少固定行数常量 OV_RECENT_ROWS_FIT"
    assert "syncOverviewHeroHeight" not in js, (
        "又出现了按高度反推行数的 syncOverviewHeroHeight："
        "它与右列 max-height 互相触发会形成正反馈（issue #15 第 4 轮）"
    )

    # 4. 表格不得靠「面板内滚动」来消化多余行：历史判定为「三重截断」回归
    assert ".ov-panel-recent .ov-table-wrap" in css, "缺少最近转存表体容器规则"
    i = css.index(".ov-panel-recent .ov-table-wrap")
    rule = css[i:css.index("}", i)]
    assert "overflow:auto" in rule, "表体容器应保持 overflow:auto 但行数必须够少（见第 3 条）"


def test_overview_dom_audit_measures_hero_blank():
    """概览页走查脚本必须包含「首屏两列纯空白」断言（本 issue 的核心回归点）。

    这条不做浏览器度量（那是 check_overview_dom.py 的职责，见 AGENTS.md 的
    「确定性部分落库」要求），只保证**门禁本身**没被删掉或阈值放宽。
    """
    src = _read(os.path.join(_ROOT, "scripts", "check_overview_dom.py"))
    assert "leftBlank" in src, "概览页走查缺少 leftBlank（左列底部纯空白）度量"
    assert "todoBlank" in src, "概览页走查缺少 todoBlank（待办块内纯空白）度量"
    assert "MAX_HERO_BLANK" in src, "概览页走查缺少纯空白上限常量"
    m = re.search(r"MAX_HERO_BLANK\s*=\s*(\d+)", src)
    assert m, "MAX_HERO_BLANK 未定义数值"
    assert int(m.group(1)) <= 40, (
        "MAX_HERO_BLANK 被放宽到 {}px：无法拦住 271/291px 那类回归".format(m.group(1))
    )


# ------------------------------------------------ TMDB 筛选栏视觉升级（issue #15 第 5 轮）
def test_tmdb_filters_are_named_and_visually_layered(dist):
    """回归（issue #15 第 5 轮）：筛选栏必须叫「TMDB 发现」，且必须有视觉层次。

    原表现：一行十来个个同款深色方块平铺在面板底上，没有分组、没有图标、
    圆角 4px 与面板的 10px 不协调 —— 用户的原话是「筛选框 UI 太素」。

    这里做静态防复发（真实度量由 scripts/check_tmdb_filters_dom.py 负责，9 组视口）：
      1. 模块标题必须是「TMDB 发现」（用户明确要求重命名）；
      2. 字段标签必须带图标（每个 .tmdb-filter-group 一个 .tmdb-ficon）；
      3. 控件圆角必须 ≥8px；
      4. 控件底色必须是**实色令牌**（--field-bg），不得退回半透明卡片底 ——
         半透明控件与半透明面板合成后只差约 2 个 RGB 通道，肉眼几乎无边界，
         这正是「控件融进面板」的观感来源；
      5. 必须有「快捷筛选」分组标题栏与「重置筛选」按钮。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    css, _ = _extract(dist)
    tokens = _read(os.path.join(_SRC, "styles", "tokens.css"))

    # 1. 命名
    assert "TMDB 发现" in body, "发现区标题未重命名为「TMDB 发现」"
    assert 'id="tmdbPanelTitle"' in body, "发现区标题节点缺失"

    # 2. 字段图标：8 个字段（4 快捷 + 4 高级）都要有
    assert body.count("tmdb-ficon") >= 8, (
        "筛选字段图标少于 8 个（当前 {} 个）：字段标签没有图标是「太素」的一半".format(
            body.count("tmdb-ficon")))
    assert ".tmdb-ficon" in css, "缺少 .tmdb-ficon 图标样式"

    # 3. 圆角
    i = css.index(".tmdb-select{")
    rule = css[i:css.index("}", i)]
    m = re.search(r"border-radius:(\d+)px", rule)
    assert m, ".tmdb-select 未设像素圆角（改回 var(--radius-xs)=4px 会让控件像表格格子）"
    assert int(m.group(1)) >= 8, (
        ".tmdb-select 圆角被改回 {}px（下限 8px）".format(m.group(1)))

    # 4. 控件底色必须是实色：半透明底色会与半透明面板合成到几乎同色
    assert "background-color:var(--field-bg)" in rule.replace(" ", ""), (
        ".tmdb-select 的底色必须是实色令牌 --field-bg："
        "退回 --card-2（rgba(17,18,23,.74)）会与面板 rgba(22,23,28,.55) "
        "合成成几乎同色，控件重新「融进」面板（issue #15 第 5 轮）"
    )
    assert "--field-bg:" in tokens, "缺少 --field-bg 令牌"
    # 深色主题下必须是实色（不得是 rgba(...)）
    fbg = tokens[tokens.index("--field-bg:"):]
    fbg = fbg[:fbg.index(";")]
    assert "rgba" not in fbg, "--field-bg 又变回了半透明值：{}".format(fbg)

    # 5. 分层：分组标题栏 + 重置按钮
    assert "tmdb-fcap-title" in body, "缺少分组标题栏（快捷筛选 / 高级筛选）"
    assert "快捷筛选" in body, "缺少「快捷筛选」分组标题"
    assert 'id="tmdbResetBtn"' in body, "缺少「重置筛选」按钮"


def test_tmdb_reset_filters_semantics(dist):
    """回归：`resetTmdbFilters()` 必须一次还原全部 9 项条件，且不能触发榜单反推。

    ⚠️ 这里锁的是一条**实测踩到的真 bug**（不是假想）：
    `clearProviderSelection() → renderProviderSelection()` 在
    `list_type === 'discover'` 时会顺手调用 `loadTmdbList()`，而
    `loadTmdbList()` 会用当时的 `genre_id / #tmdbMinRating / #tmdbYear`
    重新推导榜单类型。如果在「重置」的中途先动平台多选、后清 genre，
    榜单类型会被重新推导回 discover ——
    实测表现就是「点了重置筛选，榜单类型没变」（1440×900 可复现）。

    因此本用例断言：
      1. 重置路径不得调用 `clearProviderSelection()` / `renderProviderSelection()`
         （它们会旁路触发列表加载）；
      2. 必须用只刷 UI 的 `syncProviderSelectionUi()`；
      3. `dscState.genre_id = 0` 必须出现在 `syncProviderSelectionUi()` **之前**；
      4. 走查脚本必须保留「重置语义」断言，且不得缺少前置条件校验
         （否则「重置成功」可以建立在「其实压根没改」之上）。
    """
    js = _read(os.path.join(_JS_DIR, "tmdb.js"))
    fn = js[js.index("function resetTmdbFilters"):]
    fn = fn[:fn.index("\nfunction ", 10)]
    # 只对「代码」断言，去掉注释：本函数上方的注释里**故意**提到了
    # renderProviderSelection 作为反例说明，不应被当成调用。
    code = re.sub(r"/\*.*?\*/", "", fn, flags=re.S)
    code = re.sub(r"//[^\n]*", "", code)

    assert "function syncProviderSelectionUi" in js, "缺少只刷 UI 的平台多选同步函数"
    assert "syncProviderSelectionUi()" in code, "重置未使用 syncProviderSelectionUi"
    for banned in ("clearProviderSelection", "renderProviderSelection"):
        assert banned not in code, (
            "重置路径调用了 {}：它会旁路触发 loadTmdbList，"
            "把榜单类型重新推导回 discover（issue #15 第 5 轮实测 bug）".format(banned)
        )
    # 顺序：先把 genre_id 清零，再动平台多选
    assert code.index("dscState.genre_id = 0") < code.index("syncProviderSelectionUi()"), (
        "`dscState.genre_id = 0` 必须在 syncProviderSelectionUi() 之前："
        "否则 loadTmdbList 仍会按旧 genre 反推出 discover"
    )
    # 重置必须覆盖全部 9 项条件
    for token in ("tmdbMediaType", "tmdbListType", "tmdbRegion", "tmdbLanguage",
                  "tmdbSort", "tmdbMinRating", "tmdbYear", "providers", "genre_id"):
        assert token in code, "重置函数没有覆盖 {}".format(token)


def test_overview_todo_discovery_jump_is_reachable():
    """回归（issue #24）：待办里的「去 TMDB 发现」必须真的能跳到发现区。

    现场：概览页「待办事项」里的「去 TMDB 发现」按钮点了没反应。
    根因有两条，缺一条就复现：

      1. 按钮 action 写的是 `switchTab('tmdb')` —— #14 把 TMDB 并入概览页后，
         `tmdb` 已从 PAGES / PAGE_IDS（tabs.js）里删掉；
         `switchTab('tmdb')` 只是把 5 个页面全部 `active=false`
         （概览页被切没了），不滚动、不预加载，发现区仍停在折叠线以下；
      2. `jumpToDiscovery()` 只调 `openDiscovery()`，而后者在发现区**已初始化**时
         只做一次原地滚动 —— `behavior:'smooth'` 在「位置本来就对」时不产生位移，
         观感同样是「跳转无效」。

    因此本用例锁两条：
      - 待办不得再产出指向已删除 Tab 的跳转；
      - jumpToDiscovery 必须「先确保在概览页，再滚到发现区」。

    真实位移由 scripts/check_overview_dom.py 的 discoveryReachable 断言负责
    （9 组视口，点按钮后要求发现区顶边真正进入视口）。
    """
    ov = _overview_js()
    assert "switchTab('tmdb')" not in ov, (
        "待办仍在跳转已删除的 tmdb Tab：switchTab 会找不到页面容器，"
        "把概览页一并切掉（issue #24 现场）"
    )
    assert "action:'jumpToDiscovery()'" in ov, "「今日还没有转存记录」未接线到 jumpToDiscovery()"
    # 说明「为什么旧写法是坏的」：tmdb 确实已不是页面
    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "'tmdb'" not in tabs.split("var PAGES")[1].split(";")[0], (
        "tmdb 又回到了 PAGES：本用例的前提（tmdb 非页面）已变，需同步复核按钮接线"
    )

    fn = ov[ov.index("function jumpToDiscovery"):]
    fn = fn[:fn.index("\n}")]
    assert "scrollTmdbPanelIntoView" in fn, (
        "jumpToDiscovery 未调用 scrollTmdbPanelIntoView："
        "发现区已初始化时 openDiscovery 不会产生任何滚动"
    )
    assert "switchTab('overview')" in fn, (
        "jumpToDiscovery 未确保停在概览页：从转存/设置页点命令面板的"
        "「前往 TMDB 发现」时，发现区所在的概览页必须可见"
    )
    # 说明：函数上方的注释里会提到 openDiscovery 作为反例，先剥注释再比顺序
    code = re.sub(r"//[^\n]*", "", fn)
    assert code.index("switchTab('overview')") < code.index("openDiscovery"), (
        "jumpToDiscovery 必须先切回概览页再定位发现区，否则在隐藏页面上算出的滚动位置无意义"
    )


def test_tmdb_filters_dom_audit_is_committed():
    """走查脚本必须落库（AGENTS.md 门禁 2），且阈值不得被放宽。"""
    src = _read(os.path.join(_ROOT, "scripts", "check_tmdb_filters_dom.py"))
    for token in ("MIN_SELECT_RADIUS", "MIN_BG_DIFF", "MIN_ADV_BG_DIFF",
                  "MIN_CHIP_RADIUS", "MAX_PANEL_ALPHA", "RESET_EXPECT"):
        assert token in src, "筛选栏走查脚本缺少断言常量 {}".format(token)
    for name, limit, cmp_ in (("MIN_SELECT_RADIUS", 8, ">="),
                              ("MIN_BG_DIFF", 4, ">="),
                              ("MIN_ADV_BG_DIFF", 3, ">="),
                              ("MIN_CHIP_RADIUS", 100, ">=")):
        m = re.search(r"{}\s*=\s*(\d+)".format(name), src)
        assert m, "{} 未定义数值".format(name)
        val = int(m.group(1))
        assert val >= limit, "{} 被放宽到 {}（下限 {}）：拦不住旧版形态的回归".format(
            name, val, limit)
    # 面板磨砂不能被实底遮罩替代
    m = re.search(r"MAX_PANEL_ALPHA\s*=\s*([\d.]+)", src)
    assert m and float(m.group(1)) <= 0.6, (
        "MAX_PANEL_ALPHA 被放宽到 {}：视觉升级不得靠不透明底遮掉磨砂观感".format(
            m.group(1) if m else "?"))
    assert "清空选择" not in src or "RESET_READ" in src, "缺少重置语义读数探针"
    # 前置条件校验：不能「因为压根没改」而让断言永远通过
    assert "前置条件不成立" in src, (
        "缺少「改乱筛选条件是否真的生效」的前置校验："
        "否则重置断言会在「其实没改」的情况下恒为通过"
    )


# ---------------------------------------------------------------- 高级筛选标题行（issue #24）
def test_adv_legend_is_not_fieldset_legend(dist):
    """回归：高级筛选标题行不得用 <legend>（issue #24「字体重叠」的直接成因）。

    `fieldset > legend` 在 UA 样式里被提到 fieldset 边框上排版：实测 legend 顶边
    相对 fieldset 为 0px，而同一位置的普通子元素是 +46px。于是内层底卡的上边框
    从「高级筛选」标题与「仅「发现」榜单有效」胶囊中间横穿，看起来就是字体重叠。

    这里守两道：产物里不能有 <legend>，源码里必须是普通 div；
    几何层面的「溢出 0px」由 scripts/check_tmdb_filters_dom.py 的 A7 断言覆盖。
    """
    assert "<legend" not in dist, (
        "产物里仍有 <legend>：fieldset 的 legend 会被提到边框上排版，"
        "高级筛选标题会被内层底卡上边框横切（issue #24）"
    )
    assert 'class="tmdb-adv-legend"' in dist, "高级筛选标题行被误删"
    # 标题行的容器必须是 div（普通块级元素才会回到 fieldset 的内边距流内）
    m = re.search(r'<div class="tmdb-adv-legend">', dist)
    assert m, "高级筛选标题行不是 <div class=\"tmdb-adv-legend\">"

    body = _read(os.path.join(_SRC, "body.html"))
    assert "<legend" not in body, "body.html 仍在使用 <legend>"
    # fieldset 的表单语义必须保留（折叠时置 disabled 靠它级联到内部控件）
    assert re.search(r'<fieldset class="tmdb-adv" id="tmdbAdv"[^>]*>', body), \
        "高级筛选的 fieldset 被移除：折叠时无法用 disabled 级联关闭内部控件"
    assert "disabled" in _read(os.path.join(_JS_DIR, "tmdb.js")), "折叠时的 disabled 级联被移除"
    # legend 换 div 后，无障碍名改由 role=group + aria-label 承担，不能两头都丢
    assert 'role="group"' in body and 'aria-label="高级筛选"' in body, \
        "换成 div 后缺少 role=group / aria-label，分组语义与无障碍名丢失"

    # 样式层：标题行必须仍声明为 flex 行（回到内边距流后才谈得上对齐）
    main_css = _read(MAIN_CSS)
    block = main_css[main_css.index(".tmdb-adv-legend{"):]
    block = block[:block.index("}")]
    assert "display:flex" in block, "标题行不再是 flex 行，图标/标题/胶囊无法基线对齐"


def test_discovery_dom_script_asserts_adv_legend_overflow():
    """回归：A7 几何断言必须留在走查脚本里，且阈值不得被放宽。

    静态测试只能证明「没有 legend 标签」，证明不了「标题确实落在卡内」——
    后者只有渲染后量 getBoundingClientRect 才知道，所以两条都要在。
    """
    src = _read(os.path.join(_ROOT, "scripts", "check_tmdb_filters_dom.py"))
    assert "MAX_LEGEND_OVERFLOW" in src, "缺少标题行溢出上限常量"
    m = re.search(r"MAX_LEGEND_OVERFLOW\s*=\s*([\d.]+)", src)
    assert m, "MAX_LEGEND_OVERFLOW 未定义数值"
    assert float(m.group(1)) <= 0.5, (
        "MAX_LEGEND_OVERFLOW 被放宽到 {}：旧版实测溢出 15px，放宽阈值等于放行回归".format(
            m.group(1)))
    assert "legendOverflow" in src and "hintOverflow" in src, "缺少标题/胶囊溢出测量"
    # 正数即「溢出到内容盒之上」，判定必须比上限严格
    assert "legendOverflow\"] > MAX_LEGEND_OVERFLOW" in src, "标题溢出断言丢失"
    assert "hintOverflow\"] > MAX_LEGEND_OVERFLOW" in src, "胶囊溢出断言丢失"
    assert "contentTop" in src and "borderTopWidth" in src and "paddingTop" in src, \
        "内容盒顶边的计算口径丢失（必须含边框 + 内边距）"

def test_library_tab_wired(dist):
    """回归（issue #27）：媒体库页必须完整接线。

    校验「页面容器 / 两侧导航 / 路由分发 / 源文件进入构建清单」四件套，
    防止只加 DOM 不加导航、或只加导航不注册分发的半成品状态。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="tabLibrary"' in body, "缺少媒体库页面容器 #tabLibrary"
    assert 'data-tab="library"' in body, "侧栏缺少媒体库导航项"

    tabs = _read(os.path.join(_JS_DIR, "tabs.js"))
    assert "library: 'tabLibrary'" in tabs, "PAGE_IDS 未映射 library"
    assert "'library'" in tabs.split("var PAGES")[1], "PAGES 未包含 library"

    build = _read(_BUILD_SH)
    assert " library " in build, "library.js 未进入 build.sh 构建清单"

    # 源文件存在 + 产物已包含其模块标记（防「改了源码忘了重建」）
    assert os.path.isfile(os.path.join(_JS_DIR, "library.js")), "缺少 library.js"
    # esbuild minify 会剥掉 `// module:` 注释，故用函数名作为「已进入产物」的判据
    assert "loadLibraryPage" in dist, "产物未包含媒体库逻辑（需重跑 build.sh）"

    # 后端路由已注册
    routes = _read(os.path.join(_ROOT, "app_modules", "routes.py"))
    assert "LibraryRouteMixin" in routes, "LibraryRouteMixin 未注册进路由分发"
    assert "/api/qas/delete" in _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))

def test_library_inline_handlers_use_safe_js_arg():
    """回归（issue #27）：媒体库内联事件处理器的参数必须用单引号字面量。

    踩坑：原先用 JSON.stringify 生成参数，产出 `"f2"`（双引号）与
    `onchange="..."` 的双引号冲突，HTML 解析后属性被截断为
    `onchange="toggleLibSelect(" f2",="" this.checked)"`，导致勾选框失效。
    本用例锁死「library.js 不得再用 JSON.stringify 拼内联参数」。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function jsArg(" in src, "缺少 jsArg 安全参数包装函数"
    assert "JSON.stringify(it._key)" not in src, "内联参数不得用 JSON.stringify"
    assert "JSON.stringify(it.name)" not in src, "内联参数不得用 JSON.stringify"
    assert "JSON.stringify(acc)" not in src, "内联参数不得用 JSON.stringify"
    # 内联处理器必须走 jsArg
    for call in ("toggleLibSelect(", "enterDir(", "deleteOne(", "loadLibrary("):
        idx = src.find(call)
        assert idx != -1, "缺少内联处理器 {}".format(call)


def test_library_files_card_is_first(dist):
    """回归（issue #27 反馈）：媒体库页里「网盘文件」必须排在「QAS 转存任务」之前。

    用户诉求是「把网盘文件模块放到顶部，和夸克自动转存任务交换一下位置」。
    本用例按源文件 DOM 顺序断言（产物是压缩结果，逐个标签抓取不稳），
    并校验页面内确实只有一套模块容器，避免「复制一份挪到前面」式的假交换。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    lib_start = body.index('id="tabLibrary"')
    lib_end = body.index("<!-- ===== 设置 ===== -->")
    section = body[lib_start:lib_end]

    assert section.count('id="libList"') == 1, "网盘文件容器应唯一"
    assert section.count('id="qasTaskList"') == 1, "QAS 任务容器应唯一"
    assert section.index('id="libList"') < section.index('id="qasTaskList"'), \
        "「网盘文件」模块未排在「QAS 转存任务」之前"

    # 加载入口按同一顺序发起：先目录、后任务
    src = _read(os.path.join(_JS_DIR, "library.js"))
    page = src[src.index("function loadLibraryPage()"):src.index("// ---------- 短预算请求")]
    assert page.index("loadLibrary(") < page.index("loadQasTasks("), \
        "loadLibraryPage 应先发起网盘目录请求"

    # 产物已包含重写后的加载逻辑（防「改了源码忘了重建」）
    assert "refreshLibrary" in dist, "产物未包含媒体库刷新逻辑（需重跑 build.sh）"


def test_library_fetch_has_short_budget():
    """回归（issue #27）：媒体库请求不得沿用后端级长重试，必须能快速失败。

    用户反馈「网盘文件模块访问有点慢」的根因之一是等待预算过长
    （后端 15s 超时 × 3 次重试 ≈ 32s）。前端改为「软超时 + 仅 1 次重试」，
    本用例锁死该形态，防止回归成 3 次重试的长等待。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function libFetch(" in src, "缺少短预算请求包装 libFetch"
    assert "AbortController" in src, "缺少软超时（AbortController）"
    assert re.search(r"LIB_REQ_TIMEOUT\s*=\s*(\d+)", src), "LIB_REQ_TIMEOUT 未定义"
    ms = int(re.search(r"LIB_REQ_TIMEOUT\s*=\s*(\d+)", src).group(1))
    assert ms <= 10000, "单请求软超时 {}ms 过长，快速失败形同虚设".format(ms)
    r = re.search(r"LIB_RETRIES\s*=\s*(\d+)", src)
    assert r and int(r.group(1)) <= 1, "重试次数必须 ≤1，否则等待时间会成倍放大"

    # 媒体库不得再直接裸用 apiGet（默认 retries=2 会叠加长等待）
    assert "await apiGet(" not in src, "媒体库请求应统一走 libFetch 短预算通道"

    # 超时预算必须覆盖「整次调用」，而不是「每次尝试各一份」。
    # 踩坑：写成循环内 setTimeout 时，2 次尝试实测等待 16.4s（8s + 退避 + 8s），
    # 「快速失败」直接失效。判据：AbortController 的定时器必须建立在循环之外，
    # 循环内不得再出现 setTimeout(... LIB_REQ_TIMEOUT)。
    fetch = src[src.index("async function libFetch("):src.index("// ---------- QAS 任务")]
    loop = fetch[fetch.index("for("):]
    assert "setTimeout(function(){ try{ ctrl.abort(); }catch(e){} }, LIB_REQ_TIMEOUT)" \
        not in loop, "超时定时器被放进了重试循环：总等待会随重试次数成倍放大"
    assert loop.count("LIB_REQ_TIMEOUT") == 1 or "signal.aborted" in loop, \
        "重试循环内未检查中止状态，可能在中止后继续发起新请求"


def test_qas_task_delete_wired(dist):
    """回归（issue #27 跟进）：QAS 任务条目必须能在媒体库页删除。

    用户确认 QAS 为自部署服务，因此「改任务配置」没有改到别人任务的风险，
    本次把此前刻意排除的「删除 QAS 任务」补上。用例锁死三件事：
      1. 前端确实接线（任务表有删除入口 + 后端接口），且产物已重建；
      2. 删除是「整表写回」而不是「只发被删项」——QAS `/update` 为整表替换，
         少发一条会把其余任务全部抹掉；
      3. 删除前必须确认（不可逆操作），确认文案要说清「只删配置、不动文件」。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="qasActionBar"' in body, "缺少 QAS 任务批量操作条"
    assert "deleteSelectedQasTasks()" in body, "缺少批量删除 QAS 任务的入口"

    src = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function doDeleteQasTasks(" in src, "缺少 QAS 任务删除逻辑"
    assert "function deleteQasTask(" in src, "缺少单条删除 QAS 任务的入口"
    assert "/api/qas/delete_tasks" in src, "前端未调用 QAS 任务删除接口"
    # issue #27 严重问题修复后：必须用 confirmThen（内部收敛 ok===true）+ 早退，
    # 而不是裸 showConfirm —— 裸调用时「只看 await 不看返回值」会点取消也照删。
    qas_fn = src[src.index("async function doDeleteQasTasks("):]
    qas_fn = qas_fn[:qas_fn.index("\nfunction ")] if "\nfunction " in qas_fn else qas_fn
    assert "confirmThen(" in qas_fn, "删除 QAS 任务是不可逆操作，必须先确认取返回值"
    assert re.search(r"if\s*\(\s*!\s*confirmed\s*\)\s*return", qas_fn), \
        "确认被取消时必须提前 return，否则点取消也会删除"
    assert "网盘里已转存的文件不受影响" in src, \
        "确认文案必须说清只删任务配置、不影响网盘文件"

    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert "/api/qas/delete_tasks" in routes, "后端未注册 QAS 任务删除路由"

    # 整表写回：必须调 QASClient.update_tasklist（而不是逐个删）
    api = _read(os.path.join(_ROOT, "app_modules", "api_client.py"))
    assert "def update_tasklist(" in api, "QASClient 缺少 update_tasklist（整表写回）"
    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))
    assert "update_tasklist(" in lib, "media_library 未走整表写回"
    assert "写回列表条目数校验失败" in lib, \
        "整表写回前必须做条目数一致性校验，防止误删其它任务"

    # 产物已包含新逻辑（防「改了源码忘了重建」）
    assert "deleteSelectedQasTasks" in dist, "产物未包含 QAS 任务删除逻辑（需重跑 build.sh）"


def test_qas_task_delete_inline_handlers_use_safe_js_arg():
    """内联处理器只传定位 key，不把任务名/路径拼进参数（沿用 jsArg 约定）。

    踩坑（issue #27）：`JSON.stringify` 产出双引号会把 HTML 属性截断，
    导致勾选框点了没反应。QAS 任务名/路径由用户自由填写，同样含引号风险，
    因此这里要求「行内只传 qasKey()，再在函数里查表」的形态。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))
    for call in ("toggleQasSelect(", "deleteQasTask("):
        assert call in src, "缺少内联处理器 {}".format(call)
    # 不得把 taskname 直接拼进内联参数位
    assert "deleteQasTask(" + "' + esc(" not in src
    i = src.index("function _renderQasTasks(")
    block = src[i:src.index("function findQasTask(")]
    assert "jsArg(key)" in block, "内联处理器参数必须用 jsArg 生成单引号字面量"
    # 行内不得直接引用任务字段（只允许 esc() 渲染文本 + qasKey 定位）
    for fn in ("toggleQasSelect(", "deleteQasTask("):
        seg = block[block.index(fn):]
        seg = seg[:seg.index(")") + 1] if ")" in seg else seg
        assert "t.taskname" not in seg and "t.savepath" not in seg, \
            "内联参数位不得直接拼任务字段，应传 qasKey 后在函数内查表"


def test_library_poster_cards_wired(dist):
    """回归（issue #27）：媒体库网盘文件必须能切成「TMDB 风格图文卡片」。

    用户诉求原文是「媒体库文件夹样式的选中，改成图文并茂，像 TMDB 那样，
    使用 TMDB 的数据就可以」。这里锁死三件事：
      1. 前端确实有卡片渲染与「图文 / 列表」切换，且产物已重建；
      2. 海报来源是后端 /api/tmdb/match（按文件名匹配），不是前端直连 TMDB；
      3. 匹配不到 / TMDB 不可用时降级为占位卡，不得让媒体库整体不可用。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    section = body[body.index('id="tabLibrary"'):body.index("<!-- ===== 设置 ===== -->")]
    assert 'id="libViewBtn"' in section, "缺少「图文 / 列表」视图切换入口"
    assert "toggleLibView()" in section, "视图切换按钮未接线"

    src = _read(os.path.join(_JS_DIR, "library.js"))
    for fn in ("function _libCardHtml(", "function _libFileCardHtml(", "function _libDirCardHtml(",
               "function _libApplyPoster(", "function _libMatchChunked(", "function toggleLibView("):
        assert fn in src, "缺少 {}".format(fn)
    assert "/api/tmdb/match" in src, "前端未调用 TMDB 匹配接口"
    # 默认图文，可切回列表（localStorage 记住偏好）
    assert "lib_view" in src, "视图偏好未持久化"
    # 无海报时用 tmdb-no-poster 占位（复用发现区同一套观感），不留破图
    assert "tmdb-no-poster" in src, "缺少无海报占位"
    assert "libPosterBroken" in src, "海报加载失败未兜底"

    # 后端接口已注册
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_tmdb.py"))
    assert "/api/tmdb/match" in routes, "后端未注册 TMDB 匹配路由"
    tmdb_src = _read(os.path.join(_ROOT, "app_modules", "tmdb.py"))
    assert "def match_tmdb_by_name(" in tmdb_src, "tmdb.py 缺少按文件名匹配的实现"

    # 产物已包含新逻辑（防「改了源码忘了重建」）
    assert "toggleLibView" in dist, "产物未包含视图切换（需重跑 build.sh）"
    assert "lib-card-poster" in dist, "产物未包含卡片海报样式（需重跑 build.sh）"


def test_library_poster_cards_keep_fast_budget():
    """图文卡片的 TMDB 匹配不得把「快速失败」这条底线打回去。

    踩坑背景（issue #27）：目录请求曾因后端 15s×3 重试 + 前端 2 次重试，
    最坏等待 32s。海报匹配是**额外**的一层网络调用，如果绕过 libFetch
    用默认 retries 的 apiGet，等于把这个坑原样搬回来。
    因此要求：匹配请求也走 libFetch 短预算通道。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))
    assert "await apiGet(" not in src, "图文卡片的匹配请求也必须走 libFetch"
    worker = src[src.index("async function _libMatchWorker("):]
    worker = worker[:worker.index("\nfunction ")] if "\nfunction " in worker else worker
    assert "libFetch(" in worker, "匹配请求未走 libFetch 短预算通道"
    # 匹配是「锦上添花」：失败必须静默降级，不能把卡片渲染打断
    assert "catch(e){ return; }" in worker or "catch(e){return;}" in worker, \
        "匹配失败必须静默降级为占位卡"
    # 限流：单目录匹配数量与并发都要有上限，避免一个页面把 TMDB 打满
    assert "LIB_MATCH_LIMIT" in src and "LIB_MATCH_CONCURRENCY" in src, "匹配缺少限流常量"
