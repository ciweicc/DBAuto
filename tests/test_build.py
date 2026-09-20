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


def test_settings_density_tokens_declared(dist):
    """issue #61：卡片内密度必须由一个令牌组统一决定，不能再散落各处。

    用户报的是「设置页显示密度不一致」：卡片外框走 --card-pad（9~14px），
    卡片**内部**却是 13px 标签 + 6px 标签间距 + 14px 字段间距 + 12px 卡片标题间距，
    一组数字对不上；同时「宽松/标准/紧凑」密度切换只改卡片外边距，
    点「紧凑」后卡里的标签纹丝不动 —— 密度设置形同虚设。
    """
    css = dist
    for token in ("--set-label-fs", "--set-label-mb", "--set-field-gap",
                  "--set-hint-fs", "--set-hint-mt"):
        assert token in css, "缺少卡片内密度令牌 {}".format(token)
    compact = re.sub(r"\s+", "", css)
    # 三个密度档都必须显式声明这些令牌：只写标准档的话，
    # 用户先切宽松再切回标准时令牌不会复位（--fs-base 那条注释踩过同样的坑）。
    for density in ("comfortable", "compact"):
        # 产物里的 CSS 会被压缩成 html[data-density=comfortable]（引号被去掉），
        # 且同一选择器可出现多个块（卡片矩形 / 卡片内密度分属不同小节），
        # 因此要把该档的**所有**块拼起来判定。
        blocks = re.findall(
            r"html\[data-density=['\"]?" + density + r"['\"]?\]\{(.*?)\}",
            css, re.S)
        assert blocks, "未找到 {!r} 密度档样式块".format(density)
        joined = "".join(blocks)
        assert "--set-field-gap" in joined, (
            "{!r} 密度档未声明卡片内密度令牌：切档后卡片内部不会跟着变".format(density))
    # 标签与提示必须真的引用令牌，而不是把旧值写死
    assert "font-size:var(--set-label-fs)" in compact, "字段标签未引用密度令牌"
    assert "font-size:var(--set-hint-fs)" in compact, "字段提示未引用密度令牌"
    assert ".settings-group.set-fields>.form-group{margin-bottom:var(--set-field-gap)}" \
        in compact, "卡片内字段间距未引用密度令牌"
    assert ".settings-group.card>.card-title{margin-bottom:var(--set-field-gap)}" \
        in compact, (
        "卡片标题下间距未与字段间距同值：同一条竖线上两个数字不一致，"
        "正是「密度不一致」的直观来源")
    # 旧值不得残留：标签与提示的 6px 间距必须已被令牌取代。
    # （`.form-group{margin-bottom:14px}` 仍保留给登录页 / 弹层等非卡片表单；
    #   卡片内由 .settings-group 选择器用令牌覆盖，见上面的断言。）
    assert "color:var(--text2);margin-bottom:6px;font-weight:600" not in compact, (
        "字段标签的 6px 间距残留（旧值）")
    assert "line-height:1.5;margin-top:6px}" not in compact, (
        "字段提示的 6px 间距残留（旧值）")


def test_settings_mobile_density_keeps_proportion(dist):
    """移动端密度必须与桌面端成比例，不能比桌面还松（issue #61）。"""
    css = dist
    # 产物会被压成单行，按「媒体查询起到下一个 @media / 文末」取块。
    blocks = re.findall(r"@media\(max-width:640px\)\{(.*?)(?=@media|$)", css, re.S)
    assert blocks, "未找到移动端媒体查询"
    block = "\n".join(blocks)
    assert "--set-label-fs" in block and "--set-hint-fs" in block, (
        "移动端未放宽卡片内密度令牌：手机上标签反而比桌面更小，"
        "与卡片内边距（12px）不成比例")


def test_settings_dom_script_asserts_density(dist):
    """走查脚本必须真的断言卡片内密度自洽与「空字段槽」。"""
    body = _read(os.path.join(_ROOT, "scripts", "check_settings_dom.py"))
    assert "同卡密度不一致" in body, "走查脚本未断言同一张卡内的字号/间距自洽"
    assert "网格空出整整一行" in body or "空出了格子" in body, (
        "走查脚本未断言「空字段槽」（列数与字段数不匹配时网格空出一整行）")
    assert "labelToCtrl" in body, "走查脚本未断言「标签必须压着自己的控件」"
    assert "density" in body and "fieldRows" in body, (
        "走查脚本未把密度与字段几何纳入度量")


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
    """回归：最近转存列表不得再「硬编码 8 条 + 面板内滚动」双重截断。

    注：issue #39 之后概览页已无「查看全部」出口，截断层数只剩「固定行数」一层。
    """
    js = _overview_js()
    assert "OV_RECENT_LIMIT" in js, "渲染上限未抽成常量"
    m = re.search(r"OV_RECENT_LIMIT\s*=\s*(\d+)", js)
    assert m and int(m.group(1)) >= 10, "最近转存渲染上限过小（应为面板容量，而非 8 条）"
    assert "slice(0, 8)" not in js, "仍残留硬编码 8 条截断"
    assert "item.title.length > 30" not in js, "仍对标题做硬截断（应交给 CSS 省略）"
    # 面板内滚动应取消：列表随内容等高（出口已收敛到左侧导航的「历史」）
    css, _ = _extract(dist)
    compact = re.sub(r"\s+", "", css)
    assert ".ov-panel-recent.ov-table-wrap{flex:1;min-height:0;overflow:auto}" in compact, (
        "最近转存面板未改为随内容等高（面板内滚动会让高度与左侧概览脱钩）"
    )
    # 旧的面板内滚动约束（.ov-table-wrap{overflow-x:auto;max-height:320px}）不得回归
    assert ".ov-table-wrap{overflow-x:auto;max-height:320px" not in compact, \
        "最近转存面板仍被限制在 320px 内滚动"


def test_overview_has_no_history_entry_at_all():
    """回归（issue #39）：概览页不得再有任何指向历史页的入口。

    历史沿革：面板头的「查看全部 →」与面板底的「打开历史 →」一度是两个去向相同的链接，
    上一轮只删了面板底那个；用户随即指出**面板头这个同样冗余**——
    左侧导航栏本来就有「历史」项，而概览页左侧「转存概览」面板头还挂着
    「历史记录 →」，同屏 3 个入口去向完全相同。
    按「同去向入口只留一个」的既有判据，概览页内收敛到 0 个，走左侧导航。
    """
    html = _read(os.path.join(_SRC, "body.html"))
    start = html.index('id="pageOverview"')
    end = html.index('id="tabManual"')
    # 去掉 HTML 注释再断言：注释里说明本次沿革时会提到「查看全部」等字样，
    # 那不是入口。只看真实 DOM 与事件绑定。
    overview = re.sub(r"<!--.*?-->", "", html[start:end], flags=re.S)
    assert overview.count("switchTab('history')") == 0, (
        "概览页内仍保留指向历史页的入口（同屏与左侧导航重复）"
    )
    for dead in ("查看全部", "历史记录 →", "打开历史"):
        assert dead not in overview, "概览页内的历史入口残留：{}".format(dead)
    # 左侧导航的「历史」必须仍在 —— 收敛的前提是唯一出口仍在
    assert 'data-tab="history"' in html and ">历史</button>" in html, \
        "左侧导航的「历史」被误删，历史页将没有入口"
    # 上一轮已删除的底部重复入口及其样式不得回归
    for dead in ("打开历史", "ov-recent-foot", "ov-recent-note"):
        assert dead not in html, "重复的历史入口残留：{}".format(dead)
    css = _read(MAIN_CSS)
    for dead in (".ov-recent-foot", ".ov-recent-note"):
        assert dead not in css, "重复入口的死样式残留：{}".format(dead)


def test_product_dist_has_no_duplicate_history_link(dist):
    """产物层面复核：整份产物中不再有「查看全部 / 打开历史」入口（收敛到左侧导航）。"""
    assert dist.count(">查看全部 →</button>") == 0, "产物里仍残留「查看全部」入口"
    assert "打开历史" not in dist, "产物里仍残留「打开历史」入口"
    # 唯一出口：左侧导航的「历史」项
    assert dist.count('data-tab="history"') == 1, "产物里左侧导航的「历史」项数量不为 1"


def test_overview_recent_constant_columns_are_folded():
    """回归（issue #39）：恒等于常量 / 恒为空的列不得继续占宽度。

    用户截图里「库内」列 4 行全是「是」、「链接」列 4 行全是「—」，
    两列在当前数据下都是纯占位（一列常量、一列空）。
    判据：列只有在**本屏渲染出的行**里携带区分度时才渲染，
    折叠后的结论并进面板副标题（「均在库内」/「暂无分享链接」）。
    """
    js = _overview_js()
    assert "setOvRecentCol" in js, "缺少按需列的可见性切换函数"
    assert "showLib" in js and "showLink" in js, "缺少按需列的判据变量"
    # 判据必须看「本屏渲染出的行」，不是全量 items，也不是无条件显示
    assert "rows.some(function(it){ return !ovInLibrary(it); })" in js, \
        "「库内」列未按「是否有行不在库内」判定"
    assert "rows.some(function(it){ return !!it.shareurl; })" in js, \
        "「链接」列未按「是否有行带分享链接」判定"
    # 常量列的结论必须并进副标题，否则信息直接丢失
    assert "均在库内" in js and "暂无分享链接" in js, "折叠掉的列没有把结论并进副标题"
    # colspan 必须跟着可见列数走，否则空态/加载态会错位
    assert "var colCount = 3 + (showLib ? 1 : 0) + (showLink ? 1 : 0)" in js, \
        "空态 colspan 未按可见列数计算"

    # 表头用 data-ov-col 标记，td 同名 —— 两处判据同源
    html = _read(os.path.join(_SRC, "body.html"))
    assert 'data-ov-col="lib"' in html and 'data-ov-col="link"' in html, \
        "「库内 / 链接」表头缺少 data-ov-col 标记"
    assert '<td colspan="3" class="ov-table-empty">加载中...' in html, \
        "首载占位行的 colspan 未与最小列数（3 列）对齐"
    # 折叠后不得残留列宽
    css = re.sub(r"\s+", "", _read(MAIN_CSS))
    assert ".ov-tabletheadth[hidden]{display:none}" in css, \
        "缺少按需列的隐藏规则，折叠后仍会占列宽"


def test_overview_recent_columns_are_content_sized(dist):
    """回归（issue #39 追问）：标题列必须独占剩余宽度，不被短列平分。

    原表现：`table-layout:fixed` 下**未声明宽度**的列平分剩余宽度，
    于是「标题 / 时间 / 状态」各拿 1/4 —— 2560px 视口下标题列只有 83px，
    而一条带分类前缀的常规标题实测需要约 157px，恒定被省略号截断。
    用户原话：「能不能让这个模块的横向面积更大一点显示完整内容？」

    修法是两件事一起做（缺任一个都无效，见 PR 描述的反向验证）：
      1. 时间/状态/链接按内容钉死宽度 → 标题成为唯一 auto 列，独占剩余宽度；
      2. `--ov-rec-w` 由 300px 提到 390px → 剩余宽度本身够用。
    真实度量由 scripts/check_overview_dom.py 负责（9 组视口 + 标题不截断断言）。
    """
    html = _read(os.path.join(_SRC, "body.html"))
    assert 'class="ov-col-date"' in html and 'class="ov-col-state"' in html, \
        "时间/状态表头缺少定宽类名（否则会与标题列平分宽度）"
    # 标题表头必须**没有** width 声明类，否则它就不再是 auto 列
    m = re.search(r"<th([^>]*)>标题</th>", html)
    assert m, "找不到「标题」表头"
    assert "ov-col" not in m.group(1), "「标题」表头被钉死宽度，无法独占剩余空间"

    css = re.sub(r"\s+", "", _read(MAIN_CSS))
    # 定宽列：时间/状态/链接必须显式声明宽度 —— 这是「标题独占剩余宽度」的前提
    assert ".ov-table.ov-col-date,.ov-table.ov-table-date{width:90px}" in css, \
        "「时间」列未按内容定宽"
    assert ".ov-table.ov-col-state{width:88px}" in css, "「状态」列未按内容定宽"
    assert ".ov-table.ov-col-link,.ov-table.ov-table-actions{width:44px" in css, \
        "「链接」列未按内容定宽"

    tokens = _read(TOKENS_CSS)
    m = re.search(r"--ov-rec-w:\s*(\d+)px", tokens)
    assert m, "找不到 --ov-rec-w 声明"
    assert int(m.group(1)) >= 380, (
        "--ov-rec-w 被缩回 {}px：定宽列合计约 222px，剩余宽度不足以放下常规标题"
        .format(m.group(1))
    )

    # 走查脚本必须保留「标题不截断」门禁，且阈值不得放宽
    audit = _read(os.path.join(_ROOT, "scripts", "check_overview_dom.py"))
    assert "recentTitle" in audit, "概览页走查缺少 recentTitle（标题列宽度）度量"
    assert "titleColW" in audit, "概览页走查缺少 titleColW 断言"
    assert 't["titleColW"] < 150' in audit, "标题列宽度下限断言被删（应拦住 83px 那类回归）"
    assert 't["fixedColW"] > 240' in audit, "定宽列上限断言被删（防定宽列反向膨胀）"


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


def test_library_loading_hint_names_openlist_not_qas(dist):
    """回归（issue #57）：媒体库加载提示必须指向 OpenList，不得再写「等 QAS 响应」。

    用户复现：网盘文件模块数据源已改为**只走 OpenList**（PR #60），
    但加载态文案仍写着「正在等 QAS 响应…」。用户据此去查 QAS，
    而 QAS 根本不在这条链路上 —— 这是一句把人引向错误方向的提示。

    判据：库源码与构建产物里，加载提示的两处文案都必须说 OpenList；
    且**任何**用户可见的「等 QAS 响应」都不允许残留。
    """
    def _decoded(text):
        """产物把非 ASCII 与空格都转成了 \\uXXXX 转义，先还原再判定。"""
        return re.sub(r"\\u([0-9a-fA-F]{4})",
                      lambda m: chr(int(m.group(1), 16)), text)

    for label, text in (("源码", _read(os.path.join(_JS_DIR, "library.js"))),
                        ("产物", _decoded(dist))):
        assert "正在等 QAS 响应" not in text, \
            "{} 里仍在写「正在等 QAS 响应」：数据源已是 OpenList，会误导排查方向".format(label)
        assert "正在等 OpenList 响应" in text, \
            "{} 的加载提示未点明在等 OpenList".format(label)


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


def test_inline_arg_key_has_no_control_chars():
    r"""回归（issue #27「删除按钮失效」）：内联参数里的控制字符会被 HTML 改写。

    根因：QAS 任务定位键 qasKey() 曾用 NUL（\u0000）作分隔符，而该 key 会经
    jsArg 拼进内联 `onclick="deleteQasTask('...')"` 属性。HTML 解析器会把属性里的
    裸 NUL 替换成 U+FFFD，浏览器回传的 key 与 qasKey() 生成的 key 不再相等，
    findQasTask 落空 → 删除函数静默 return → 按钮「点了没反应」。

    本用例锁死两条防线（任一即可拦住该类事故）：
      1. qasKey 的分隔符不得使用控制字符；
      2. jsArg 必须把控制字符转义成 \uXXXX 字面量再拼进属性。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))

    # 防线 1：qasKey 分隔符常量必须是「非控制字符」
    assert "QAS_KEY_SEP" in src, "缺少 qasKey 分隔符常量"
    sep_line = src[src.index("var QAS_KEY_SEP"):]
    sep_line = sep_line[:sep_line.index("\n")]
    # 常量里不得出现 \uXXXX 形式的控制码位（\\u0000-\\u001f / \\u007f）
    for m in re.finditer(r"\\u([0-9a-fA-F]{4})", sep_line):
        assert int(m.group(1), 16) > 0x1F and int(m.group(1), 16) != 0x7F, \
            "QAS_KEY_SEP 不得使用控制字符（会被 HTML 解析器改写）"
    # 也不得直接嵌裸控制字符
    assert not re.search(r"[\x00-\x1f]", sep_line), "QAS_KEY_SEP 含裸控制字符"

    # qasKey 拼接体本身也不得再拼字符串分隔符（必须用常量）
    key_fn = src[src.index("function qasKey("):]
    key_fn = key_fn[:key_fn.index("\n}") + 2]
    assert "QAS_KEY_SEP" in key_fn, "qasKey 必须用 QAS_KEY_SEP 常量作分隔符"

    # 防线 2：jsArg 必须显式转义控制字符区间
    js_fn = src[src.index("function jsArg("):]
    js_fn = js_fn[:js_fn.index("\n}") + 2]
    assert "u0000-\\u001f" in js_fn or "u0000-\u001f" in js_fn, \
        "jsArg 必须转义控制字符（否则内联属性会被 HTML 解析器改写）"


def test_library_cards_have_no_images(dist):
    """回归（issue #27 第三轮反馈）：媒体库卡片不再显示任何封面图。

    用户反馈原文：「有一些有图，有一些没图，太丑了，干脆直接不要图了」。
    根因是 TMDB 匹配率天然不可能 100%：命中的卡贴海报、未命中的卡是一大块灰色
    占位块，同一行两种形态混排。本轮把缩略图整块去掉。

    本用例从**四个方向**钉死「不再有图」，避免任何一处回潮：
      1. 前端代码里不再有 `<img>` / 海报挂载点 / 匹配请求；
      2. 样式里不再有缩略块、占位块、评分角标；
      3. 视图切换文案不再是「图文视图」（已经没有图了）；
      4. 产物已重建。
    """
    src = _read(os.path.join(_JS_DIR, "library.js"))

    # 1. 前端：卡片不再有图片与海报补丁链路
    for gone in ("function _libApplyPoster(", "function libPosterBroken(",
                 "function _libPlaceholderHtml(", "function _libMatchWorker(",
                 "function _libPatchPosters(", "function _libFileNames("):
        assert gone not in src, "残留海报逻辑 {}（媒体库已不显示图）".format(gone)
    # 「不再调用」按可执行代码判定（注释里说明历史是允许的）
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
    assert "/api/tmdb/match" not in code, "前端不应再调用 TMDB 匹配接口"
    assert "libPoster-" not in src, "卡片仍存在海报挂载点"
    # 卡片里不得出现任何 <img>
    card_fn = src[src.index("function _libCardShell("):]
    card_fn = card_fn[:card_fn.index("function _libMetaText(")]
    assert "<img" not in card_fn, "卡片结构里仍有 <img>"
    # 保留类型图标（文件夹 / 文件），否则「这是文件还是目录」要靠字形猜
    assert "lib-card-icon" in card_fn, "卡片结构里没有类型图标位"
    dir_fn = src[src.index("function _libDirCardHtml("):src.index("function _libFileCardHtml(")]
    assert "'dir'" in dir_fn, "文件夹卡未声明文件夹图标类型"
    file_fn = src[src.index("function _libFileCardHtml("):src.index("function invalidateLibCache(")]
    assert "'file'" in file_fn, "文件卡未声明文件图标类型"
    # 图标图形本身在 issue #47 后由 _libCardIconSvg 内联（不再走 sprite <use>），
    # 形状/尺寸的回归断言见 test_library_card_icons_are_rendered_inline

    # 2. 样式：缩略块 / 占位块 / 评分角标全部移除
    css = _read(_CSS_MAIN)
    for gone in (".lib-card-thumb", ".lib-card-ph", ".lib-dir-badge",
                 ".lib-card-thumb .tmdb-rating-badge"):
        assert gone not in css, "残留卡片图片样式 {}（媒体库已不显示图）".format(gone)

    # 3. 视图切换文案：不再叫「图文视图」（注释里说明历史是允许的）
    assert "图文视图" not in code, "视图切换文案仍写「图文视图」（已经没有图了）"
    assert "卡片视图" in code, "缺少「卡片视图」文案"
    btn_fn = src[src.index("function _updateLibViewBtn("):src.index("// 单请求预算")]
    assert "'列表视图'" in btn_fn and "'卡片视图'" in btn_fn, "视图切换按钮文案未更新"
    body = _read(os.path.join(_SRC, "body.html"))
    section = body[body.index('id="tabLibrary"'):body.index("<!-- ===== 设置 ===== -->")]
    assert 'id="libViewBtn"' in section and "toggleLibView()" in section, \
        "「卡片 / 列表」视图切换入口丢失"
    assert "图文" not in section, "媒体库页仍有「图文」字样"
    # 说明文案不得再承诺「自动匹配海报」
    assert "TMDB" not in section[section.index('id="libTip"'):section.index("libActionBar")], \
        "页面说明仍在承诺 TMDB 海报"

    # 4. 产物已重建（防「改了源码忘了重建」）
    assert "function _libCardShell(" in dist, "产物未包含新的卡片结构（需重跑 build.sh）"
    assert "libPosterBroken" not in dist, "产物仍含旧的海报兜底逻辑（需重跑 build.sh）"


# ============================================================
# 媒体库卡片类型图标（issue #47：文件缩略图图标异常）
# ============================================================
def test_library_card_icons_are_rendered_inline(dist):
    """回归（issue #47「文件缩略图图标异常」）：卡片类型图标必须自包含地渲染出来。

    用户只给了一张截图：媒体库卡片上「图标看起来不对」。看代码就明白为什么 ——
    卡片图标走的是 `<use href="#icon-movie">`，即**依赖文档里的 sprite `<defs>`**：

      * sprite 缺失 / id 改名 / 被 CSP 拦掉时，`<use>` 不会报错，只是**什么都画不出来**，
        表现就是「图标异常」，而且静态检查（HTML 合法、JS 合法）全都不报警；
      * 用 `viewBox="0 0 24 24"` 却把内联尺寸写成 18px，与同一行的 18px 勾选框、
        14px 删除图标比例不齐，视觉上也像「渲染错了」；
      * 语义也错：文件卡一律画**电影胶片**（icon-movie），而「文件」不都是影片
        （截图里正是一堆 9.5 KB / 21.4 GB 的非影片文件）。

    因此本用例钉死四件事：
      1. 图形由 `_libCardIconSvg` 内联产出（不经过 sprite `<use>`）；
      2. 两种类型都有图形，并且**文件不是胶片**；
      3. 尺寸/填充有对应样式，且与勾选框同量级；
      4. 产物已重建。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    css = _read(_CSS_MAIN)

    assert "function _libCardIconSvg(" in js, "卡片图标未内联产出（仍依赖 sprite <use>）"
    icon_fn = js[js.index("function _libCardIconSvg("):js.index("function _libCardShell(")]
    # 1. 自包含：图形是 <svg> + <path>，不含 <use>（<use> 正是「静默画不出」的来源）
    assert "<svg " in icon_fn, "图标未内联 svg"
    assert "<path " in icon_fn, "图标未内联 path 图形"
    assert "<use" not in icon_fn, "卡片图标仍走 sprite <use>（sprite 缺失时图标会静默消失）"
    assert "icon-movie" not in icon_fn and "icon-folder" not in icon_fn, \
        "卡片图标仍引用 sprite id，未内联图形"
    # 2. 两种类型都有图形，且互不相同（不能两种情况画同一个形状）
    assert "kind === 'dir'" in icon_fn or 'kind === "dir"' in icon_fn, \
        "未按「文件夹 / 文件」分岔图形"
    paths = re.findall(r'd="([^"]+)"', icon_fn)
    assert len(paths) >= 3, "图标图形过少，疑似只画了一种类型：{}".format(paths)
    # 文件卡不得再用胶片语义（图标名/路径都不该出现胶片）
    file_fn = js[js.index("function _libFileCardHtml("):js.index("function invalidateLibCache(")]
    assert "icon-movie" not in file_fn, "文件卡仍使用影片（胶片）图标，会误导文件类型"
    # 3. 卡片模板必须真的调用它（写了函数不调用 = 图标位是空的）
    card_fn = js[js.index("function _libCardShell("):js.index("function _libMetaHtml(")]
    assert "_libCardIconSvg(kind)" in card_fn, "卡片模板未调用 _libCardIconSvg，图标位为空"

    # 4. 样式：图标盒与图形同尺寸，且与外层视口一致（避免「小得离谱」）
    assert ".lib-card-icon{" in css, "缺少卡片图标样式"
    # 用「选择器 + {」精确定位：`.lib-card-icon{` 的裸字符串也会命中
    # `.lib-grid-compact .lib-card-icon{display:none}`（本轮实测踩到），
    # 按它切块就切到了紧凑密度那条规则上。
    block = css[css.index("\n.lib-card-icon{"):]
    block = block[:block.index("}")]
    assert "width:20px" in block and "height:20px" in block, \
        "图标盒不是 20px（与勾选框/删除图标比例不齐）"
    assert ".lib-card-icon svg{" in css, "缺少图标图形尺寸约束（内联 svg 会按外层尺寸跑）"
    svg_block = css[css.index(".lib-card-icon svg{"):]
    svg_block = svg_block[:svg_block.index("}")]
    assert "width:20px" in svg_block and "height:20px" in svg_block, \
        "图标图形未钉死 20px"
    assert ".lib-card-icon-fill{" in css, "缺少文件夹的填充样式（实心感）"
    # 视图口径：图形视口必须与内联尺寸一致，否则笔画会发虚/被裁
    assert 'viewBox="0 0 20 20"' in icon_fn, "图标 viewBox 与 20px 内联尺寸不匹配"
    # 骨架卡占位块与真实图标同宽，否则首帧→真实卡片仍会有轻微横向跳动
    sk_block = css[css.index(".lib-sk-icon{"):]
    sk_block = sk_block[:sk_block.index("}")]
    assert "width:20px" in sk_block and "height:20px" in sk_block, \
        "骨架图标占位块与真实图标尺寸不一致（首帧会跳动）"

    # 5. 产物已重建（防「改了源码忘了重建」）
    assert "function _libCardIconSvg(" in dist, "产物未包含内联图标实现（需重跑 build.sh）"
    assert 'viewBox="0 0 20 20"' in dist, "产物未包含新的图标视口（需重跑 build.sh）"
    assert "lib-card-icon-fill" in dist, "产物未包含图标填充样式（需重跑 build.sh）"


def test_library_backend_match_api_kept():
    """后端文件名 → TMDB 匹配能力保留（前端已不用，但不要顺手删）。

    这一轮只是**界面不再显示图**。匹配实现（`match_tmdb_by_name` / `/api/tmdb/match`）
    是独立可测的能力，留着以后要用可以直接接回来；本用例防止后人误删。
    """
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_tmdb.py"))
    assert "/api/tmdb/match" in routes, "后端 TMDB 匹配路由被误删"
    tmdb_src = _read(os.path.join(_ROOT, "app_modules", "tmdb.py"))
    assert "def match_tmdb_by_name(" in tmdb_src, "tmdb.py 的按文件名匹配实现被误删"


def test_library_grid_layout_is_aligned(dist):
    """回归（issue #27 反馈）：媒体库卡片视图必须是「整齐的等高紧凑卡」。

    用户两轮反馈：「排版太丑了，实在是很乱」→「有一些有图，有一些没图，干脆不要图了」。
    可量化的成因与对策，本用例把它们钉死：

      1. 第一版缩略块按海报比例撑高 → 行内高矮参差（已通过整块去掉根治）；
      2. 卡片是单行 flex（图标 + 名称 + 删除），所有卡天然等高；
      3. 标题 / 元信息单行省略 → 长中文名不折行、不撑高卡片；
      4. 「全选」不独占一行 → 卡片底部不留空条。
    """
    css = _read(_CSS_MAIN)
    js = _read(os.path.join(_JS_DIR, "library.js"))

    # 1. 卡片是单行 flex：图标 / 勾选 / 名称 / 删除在同一行，天然等高
    # 同图标那条：必须用「行首选择器 + {」定位，`.lib-grid-compact .lib-card{` 里
    # 也含 `.lib-card{` 这串字符（本轮实测踩到），裸字符串定位会切错规则。
    card = css[css.index("\n.lib-card{"):]
    card = card[:card.index("}")]
    assert "display:flex" in card and "align-items:center" in card, \
        "卡片不是「单行居中」布局，会出现内部高低不齐"
    assert "flex-direction:column" not in card, "卡片又变回多行堆叠（旧版参差的根源）"

    # 2. 标题 / 元信息单行省略（长中文名不得折行撑高卡片）
    for sel in ("\n.lib-card-title{", "\n.lib-card-meta{"):
        block = css[css.index(sel):]
        block = block[:block.index("}")]
        assert "white-space:nowrap" in block, "{} 未单行省略，长文件名会把卡片撑高".format(sel)
        assert "text-overflow:ellipsis" in block, "{} 缺少省略号".format(sel)

    # 3. 删除入口收成图标按钮（标题宽度不被按钮吃掉）
    assert ".lib-icon-btn{" in css, "删除按钮未收成图标按钮"
    assert "lib-icon-btn" in js, "删除入口未使用图标按钮"
    assert ".lib-card-icon{" in css, "缺少卡片类型图标样式"

    # 4. 全选与面包屑同一行（不再独占一条空条）
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="libSelAllWrap"' in body, "缺少行内「全选」容器"
    assert 'class="lib-crumbbar"' in body, "面包屑与全选未合并为同一条控制条"
    assert 'id="libSelectAllBar"' not in body, "旧的全选独立占位行应已移除（否则底部又多一条空条）"
    assert ".lib-crumbbar{" in css, "缺少目录控制条样式"

    # 5. 产物已重建
    assert "lib-crumbbar" in dist, "产物未包含目录控制条（需重跑 build.sh）"
    assert "lib-card-icon" in dist, "产物未包含卡片类型图标样式（需重跑 build.sh）"


def test_library_grid_layout_audit_is_committed():
    """回归（issue #27）：「排版乱 / 有没有图」必须由落库的走查脚本用渲染后数值守住。

    静态扫描只能看出「写了 flex」，看不出渲染后同一行卡片到底高矮差多少、
    也看不出页面上是否真的没有再出现图片。本轮实测该脚本能把两种回退抓出来：
      - 给卡片加回缩略块并让图片决定高度 → 同一行卡片高度差 > 8px；
      - 标题允许多行 → 同一行卡片高度差同样超标。
    """
    script = os.path.join(_ROOT, "scripts", "check_library_dom.py")
    src = _read(script)
    assert "rowSpread" in src, "走查脚本没断言「同一行卡片高度一致」"
    assert "titleWrapped" in src and "tMetaWrapped" in src, "走查脚本没断言标题/元信息单行省略"
    assert "selAllSameRow" in src, "走查脚本没断言「全选与面包屑同一行」"
    assert "gridCols" in src and "cardW" in src, "走查脚本没断言网格列数与卡片宽度区间"
    # 关键：必须断言页面上**没有任何图片**（否则「有图/没图混排」还会复发）
    assert "imgsInList" in src, "走查脚本没断言媒体库列表里不存在图片"


def test_library_cards_show_modified_time(dist):
    """回归（issue #27 跟进）：媒体库必须**显式显示修改时间**。

    用户诉求原文：「添加显示修改时间」。这里的坑不是「有没有调用 fmtTs」——
    列表视图早就渲染了时间，但卡片视图把「大小 · 修改时间」挤在同一行，
    两段都是数字，扫视时区分不出哪一段是时间（用户会以为「没显示」）。

    因此本用例钉死三件事，缺一条都会让用户再次觉得「没显示」：
      1. 卡片视图的文件卡：大小与修改时间**各占一行**，时间行带时钟图标与「修改」前缀；
      2. 两种视图共用同一套时间格式（fmtTs）与同一套语义（列表视图表头有「修改时间」）；
      3. 产物已重建。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    css = _read(_CSS_MAIN)
    body = _read(os.path.join(_SRC, "body.html"))

    # 1. 文件卡有独立的修改时间行（不是拼在「大小 · 时间」一行里）
    assert "function _libMetaHtml(" in js, "卡片元信息未拆成「大小 / 修改时间」两行"
    meta_fn = js[js.index("function _libMetaHtml("):js.index("function _libMetaText(")]
    assert "lib-card-time" in meta_fn, "修改时间行缺少专用容器 lib-card-time"
    assert "icon-clock" in meta_fn, "修改时间行缺少时钟图标（无法与大小区分）"
    assert "修改 " in meta_fn, "修改时间行缺少「修改」前缀"
    assert "fmtTs(it.updated_at)" in meta_fn, "修改时间未走统一格式化函数 fmtTs"
    assert "fmtTsShort(it.updated_at)" in meta_fn, \
        "窄卡缺少紧凑时间格式（完整格式在 180~240px 卡上必被截断）"
    assert "title=\"修改时间 " in meta_fn, "修改时间未提供完整值（悬停/复制）"
    # 两行结构：大小行（lib-card-meta）+ 时间行（lib-card-time）
    assert "lib-card-meta" in meta_fn and "lib-card-time" in meta_fn, \
        "卡片元信息应为「大小」与「修改时间」两行"
    # 目录没有「修改时间」语义，不应显示时间行（避免看着像文件）
    assert "it.dir" in meta_fn and "文件夹" in meta_fn, "文件夹卡仍应显示「文件夹」而不是时间"

    # 2. 样式：时间行单行省略 + 比标题淡（层级）+ 等宽数字（行间对齐）
    block = css[css.index(".lib-card-time{"):]
    block = block[:block.index("}")]
    assert "font-variant-numeric:tabular-nums" not in block  # 数字在 .lib-time-text 上
    text_block = css[css.index(".lib-time-text{"):]
    text_block = text_block[:text_block.index("}")]
    assert "white-space:nowrap" in text_block and "text-overflow:ellipsis" in text_block, \
        "时间文本未单行省略（长格式会把卡片撑高）"
    assert "tabular-nums" in text_block, "时间数字未等宽，行间无法对齐"
    # 紧凑/完整两种格式必须有按容器宽度的取舍规则（本轮实测的坑：
    # 单一完整格式会在 1440/2560 这类窄卡上被省略号截成「修改 2025-0…」）
    assert ".lib-time-short{" in css and ".lib-time-full{" in css, \
        "缺少「完整 / 紧凑」两种时间格式的样式开关"
    assert "@container libname" in css, "两种时间格式未按卡片实际宽度取舍（应使用容器查询）"
    assert "container-name:libname" in css, "缺少供时间行判断的命名容器"
    for gone in (".lib-time-full{display:none}", ):
        assert gone in css, "未在窄卡下隐藏完整格式" 

    # 3. 列表视图：表头与数据列都要有时间语义
    table_fn = js[js.index("function _libTableHtml("):js.index("// ---------- 卡片视图")]
    assert "修改时间</span>" in table_fn, "列表视图表头缺少「修改时间」列"
    assert "fmtTs(it.updated_at)" in table_fn, "列表视图未渲染修改时间"
    assert "lib-sub-time" in table_fn, "列表视图时间列未使用专用样式"
    assert ".lib-sub-time{" in css, "缺少列表视图时间列样式"

    # 4. 页面说明文案同步（不承诺没有的能力，也不遗漏已显示的信息）
    assert "修改时间" in body[body.index('id="libTip"'):body.index("libActionBar")], \
        "媒体库说明文案未提到修改时间"

    # 5. 产物已重建
    assert "lib-card-time" in dist, "产物未包含修改时间行（需重跑 build.sh）"
    assert "lib-sub-time" in dist, "产物未包含列表视图时间列（需重跑 build.sh）"
    assert "lib-time-short" in dist, "产物未包含紧凑时间格式（需重跑 build.sh）"
    assert "fmtTsShort" in dist, "产物未包含紧凑时间格式化函数（需重跑 build.sh）"


def test_library_modified_time_audit_is_committed():
    """回归（issue #27 跟进）：「修改时间是否真的显示出来」必须由走查脚本用渲染后数值守住。

    静态扫描只能证明「写了 lib-card-time」，证明不了渲染后每张文件卡真的有时间、
    格式可读、不折行、与标题有层级差。本轮实测该脚本能抓出这几种回退：
      - 文件卡漏渲染时间行 → fileTimeRows != fileCards；
      - 时间格式退回「—」或裸时间戳 → timeBadFormat > 0；
      - 时间行与标题同色 → timeColor == titleColor；
      - 列表视图丢了时间列 → rowsWithTime < 3。
    """
    src = _read(os.path.join(_ROOT, "scripts", "check_library_dom.py"))
    for key in ("fileTimeRows", "fileCardsWithTime", "timeBadFormat", "timeWrapped",
                "timeIcons", "timeColor", "rowsWithTime",
                # 关键：必须断言「时间没有被省略号截断」——本轮实测回退（单一完整格式）
                # 会被它抓出 8 条截断；只断言「有时间行」是抓不到的。
                "timeTruncated", "timeTitleMissing"):
        assert key in src, "走查脚本缺少「修改时间」断言 {}".format(key)


def test_library_first_paint_skeleton_is_geometric_stable(dist):
    """回归（issue #27：「每次打开都要等几秒」）：媒体库首帧必须占住位置。

    上一版首帧只有一个空态文字块（约 60px），数据一到换成 12 张卡的网格
    （数百 px）—— **页面整体下移**，用户刚点下的「刷新 / 上一级 / 视图切换」
    被推到别处，第二次点击落到空白处。观感上就是「这个页面很慢、很难用」。

    对策：首帧用与卡片视图**完全相同的网格几何**渲染骨架卡，数据到达时
    在同一高度内替换内容，页面不位移。本用例钉死三件事：
      1. 骨架用同一套 .lib-grid / .lib-card 结构（几何一致，不是另画一套）；
      2. 骨架由 CSS 给高度（占位有效），且不含 <img>（媒体库不显示封面图）；
      3. 走查脚本必须把「首帧位移」量出来（视觉问题只能靠渲染后数值守住）。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    css = _read(_CSS_MAIN)

    assert "function _libLoadingHtml(" in js, "缺少首帧加载态渲染"
    assert "function _libSkeletonCardHtml(" in js, "缺少骨架卡渲染（首帧占位）"
    loading = js[js.index("function _libLoadingHtml("):js.index("function _libSkeletonCardHtml(")]
    skel = js[js.index("function _libSkeletonCardHtml("):]
    skel = skel[:skel.index("async function loadLibrary(")] if "async function loadLibrary(" in skel else skel
    # 1. 同一套网格几何
    assert "lib-grid lib-grid-skeleton" in loading, \
        "骨架必须复用 .lib-grid 网格（另画一套会让高度对不上，位移照旧）"
    assert "lib-card lib-card-skeleton" in skel, "骨架卡必须复用 .lib-card 结构"
    assert "lib-card-name-col" in skel, \
        "骨架卡必须包含名称列（否则高度只有真实卡的一半，位移仍会发生）"
    # 2. 无图片 + CSS 给高度
    assert "<img" not in skel, "骨架卡里不应有 <img>（媒体库已不显示封面图）"
    assert ".lib-card-skeleton{" in css, "缺少骨架卡样式"
    assert ".lib-sk{" in css, "缺少骨架灰块样式（高度必须由 CSS 给出，否则占位无效）"
    for sel in (".lib-sk-cb{", ".lib-sk-icon{", ".lib-sk-line{", ".lib-sk-btn{"):
        assert sel in css, "缺少骨架元素尺寸 {}".format(sel)
    # 灰块本体可以只给颜色，但**每种骨架元素都必须有显式高度**——
    # 高度缺失时骨架会塌成 0，首帧又变成「一条细线」，位移照旧
    for sel in (".lib-sk-cb{", ".lib-sk-icon{", ".lib-sk-line{", ".lib-sk-btn{"):
        b = css[css.index(sel):]
        b = b[:b.index("}")]
        assert "height:" in b, "骨架元素 {} 未给高度：占位会塌掉".format(sel)
    assert ".lib-sk-line-sub{" in css, "缺少次要行骨架（高度必须与真实元信息行对应）"
    # 3. 骨架行数可被历史真实条数校准（避免「首帧 8 行 → 真实 30 行」又跳一次）
    assert "LIB_SKELETON_KEY" in js and "_libRememberRows(" in js, \
        "缺少「按上次真实条数校准骨架行数」的逻辑"
    # 4. 产物已重建
    assert "lib-card-skeleton" in dist, "产物未包含骨架卡（需重跑 build.sh）"


def test_library_open_speed_audit_is_committed():
    """回归（issue #27：「每次打开都要等几秒」）：打开速度必须由落库脚本计时守住。

    静态扫描证明不了「等了多久」，所以本轮新增 `scripts/check_library_speed.py`：
    在真实浏览器里跑构建产物，把假上游的延迟显式设定（默认 0.8s），
    分段量「首帧反馈 / 目录可见 / 任务表可见 / 再次打开」，
    并断言首帧位移不超过上限。本用例防止这套度量被删或放宽。
    """
    script = os.path.join(_ROOT, "scripts", "check_library_speed.py")
    assert os.path.isfile(script), "缺少媒体库打开速度走查脚本"
    src = _read(script)
    for key in ("first_paint", "directory_ready", "qas_ready",
                "reopen_directory_ready", "firstH", "afterH"):
        assert key in src, "走查脚本缺少计时项 {}".format(key)
    assert "max-shift-px" in src, "走查脚本缺少「首帧位移」上限参数"
    # 上限必须真的被用于断言（不能只是加了个参数）
    assert "abs(shift) > args.max_shift_px" in src, "首帧位移断言丢失"
    assert "* 1000 + 1500" in src or "budget_ms" in src, "缺少按上游延迟推导的耗时上限"


def test_library_open_speed_backend_budget():
    """回归：读接口必须「总预算 + 禁用底层重试」，否则等待会被重试翻倍。

    上一轮把单次尝试从 15s 收到 6s，但重试仍是 utils 默认的 3 次
    （HTTP_RETRY_TOTAL=12s），实测最坏 ≈ 12.5s 全部压给用户。
    """
    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))
    assert "LIBRARY_READ_BUDGET" in lib, "缺少读接口总预算常量"
    assert "retries=1" in lib, "读接口必须禁用底层重试（重试的是同一个卡住的上游）"
    assert "LIBRARY_CACHE_TTL" in lib and "invalidate_library_cache" in lib, \
        "缺少目录短命缓存 / 作废入口"
    # 缓存必须在删除后被作废（否则删完再看同一目录还显示已删条目）
    assert "invalidate_library_cache()" in lib, "删除后未作废目录缓存"
    api = _read(os.path.join(_ROOT, "app_modules", "api_client.py"))
    assert "def _http_kwargs(" in api, "APIClient 未提供 timeout/retries 统一下传"
    assert 'kwargs["retries"] = self.retries' in api, "retries 未透传到底层 http_*"
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    # 缓存开关由 list_library_entries(refresh=...) 下传；
    # issue #57 起数据源只剩 OpenList，refresh 必须一路传到 openlist.list_dir
    # （它才是现在唯一有「列表缓存」的那一层）。
    assert "refresh=" in routes, "路由未把「是否绕过缓存」下传"
    assert "openlist.list_dir(target, refresh=refresh)" in lib, \
        "OpenList 列表缓存开关未接到 refresh 上"


def test_library_refresh_bypasses_server_cache():
    """前端「刷新」按钮必须带 refresh=1：否则后端 30s 缓存会让刷新点了没反应。"""
    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "refresh=1" in js, "前端刷新未带 refresh=1，后端缓存会让「刷新」失效"
    load = js[js.index("async function loadLibrary("):js.index("function _renderLibError(")]
    assert "force ? '&refresh=1' : ''" in load, "refresh=1 必须只由 force 触发"


# ---------------------------------------------------------------- OpenList 数据源（issue #27 追问）
def test_openlist_source_is_wired_end_to_end(dist):
    """回归（issue #27 追问）：OpenList 作为媒体库数据源必须前后端完整接线。

    用户原话：「可以通过调用 openlist 进行管理吗，但我同时需要管理夸克自动转存的
    自动任务条目的需求。当前通过夸克自动转存的媒体库访问速度太慢了」。

    本用例锁四件事，缺任何一环功能都会「看起来做了其实没通」：
      1. 后端客户端存在且走 OpenList 真实接口（/api/fs/list、/api/fs/remove、
         /api/auth/login）—— 不能只是加了个配置项；
      2. 目录浏览能按数据源分发，且**未配置时不炸**（回落到 QAS）；
      3. 前端把「谁在回答」显示出来（否则用户无从归因快慢）、删除按源回传；
      4. 产物已重建。
    """
    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    # 注意：这里刻意**不**要求 /api/auth/login —— issue #47 已把它移除
    # （OpenList 只保留令牌验证），下面 test_openlist_account_login_is_removed
    # 反向锁住「不得复活」。
    for path in ("/api/fs/list", "/api/fs/remove"):
        assert path in ol, "openlist.py 未调用 {}".format(path)
    assert "def list_dir(" in ol and "def remove(" in ol, "缺少目录浏览 / 删除实现"
    assert "def reconcile_tasks(" in ol, "缺少任务对账实现"

    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))
    assert "def library_source(" in lib, "缺少数据源选择函数"
    assert "def list_library_entries(" in lib, "缺少统一目录入口"
    assert "def delete_entries_by_source(" in lib, "缺少按源删除"
    assert "def reconcile_tasks(" in lib, "media_library 未暴露任务对账"

    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert "/api/library/source" in routes, "缺少数据源自检路由"
    assert "/api/openlist/tasks/audit" in routes, "缺少任务对账路由"

    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="libSourceBadge"' in body, "媒体库缺少数据源徽标"
    assert 'id="auditResult"' in body, "缺少任务对账结果容器"
    assert 'id="cfg_openlist"' in body, "设置页缺少 OpenList 地址输入"
    assert 'id="cfg_openlist_token"' in body, "设置页缺少 OpenList 令牌输入"

    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function _renderLibSourceBadge(" in js, "缺少数据源徽标渲染"
    assert "function runTaskAudit(" in js, "缺少任务对账前端逻辑"
    assert "source: libSource" in js, "删除未按数据源回传（跨源会误删）"
    assert "function _libDeleteEntry(" in js, "删除条目未按数据源构造"

    st = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "function checkOpenList(" in st, "缺少 OpenList 连接自检"
    assert "openlist_token" in st, "保存配置未带上 OpenList 令牌"

    assert "lib-src-badge" in dist and "runTaskAudit" in dist, \
        "产物未包含 OpenList 数据源改动（需重跑 build.sh）"


def test_openlist_token_is_never_echoed_to_frontend():
    """回归：OpenList 令牌必须按敏感字段处理（加密落盘 + 脱敏回传）。

    与 qas_token 同口径，任何一条漏掉都会把凭据写进明文配置或直接回给浏览器。
    issue #47 起它还是 OpenList **唯一**的凭据，漏一处就没有第二条路可退。
    """
    cfg = _read(os.path.join(_ROOT, "app_modules", "config.py"))
    sensitive = cfg.split("_SENSITIVE_FIELDS = ")[1]
    sensitive = sensitive[:sensitive.index("}")]
    assert "openlist_token" in sensitive, \
        "openlist_token 未进 _SENSITIVE_FIELDS（会明文落盘）"
    routes_cfg = _read(os.path.join(_ROOT, "app_modules", "routes_config.py"))
    mask_block = routes_cfg[routes_cfg.index("def build_config_payload"):]
    mask_block = mask_block[:mask_block.index("return masked")]
    assert "openlist_token" in mask_block, "openlist_token 未脱敏（会把凭据回传给前端）"
    assert "elif k in (\"pansou\", \"qas\", \"openlist\")" in routes_cfg, \
        "openlist 地址未走 URL 校验（与 pansou/qas 同口径）"


def test_openlist_account_login_is_removed(dist):
    """回归（issue #47）：OpenList 只保留令牌验证，账号密码链路必须**整体**消失。

    用户要求：「把 OpenList 用户名密码验证的功能去掉，只保留令牌验证」。

    「删功能」最容易只删一半，而每一半都留下一个独立的坑：
      - 只删前端输入框 → 旧配置里的人还在被读取，行为与用户看到的不一致；
      - 只删后端调用 → 界面上仍有两个框，用户填了一个永远不生效的字段；
      - 只删配置项 → `unknown config key` 这种含糊报错，用户以为是参数名拼错。
    因此这里把四层一起锁死：实现、配置、校验、界面（含产物）。
    """
    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    assert "/api/auth/login" not in ol, "openlist.py 仍会调登录接口（账号密码没删干净）"
    assert "def _login(" not in ol, "openlist.py 仍保留 _login 实现"
    for name in ("AUTH_PASSWORD", "AUTH_MIXED"):
        assert name not in ol, "{} 语义仍在（「账号密码」这条路还留在代码里）".format(name)
    assert "def _resolve_auth(" in ol and "AUTH_TOKEN" in ol, "令牌鉴权入口被误删"

    cfg = _read(os.path.join(_ROOT, "app_modules", "config.py"))
    for key in ("openlist_user", "openlist_pass"):
        assert '"{}"'.format(key) not in cfg, \
            "{} 仍在默认配置里（功能没有真正移除）".format(key)

    routes_cfg = _read(os.path.join(_ROOT, "app_modules", "routes_config.py"))
    assert "_REMOVED_OPENLIST_KEYS" in routes_cfg, \
        "缺少对已移除字段的显式拒绝：旧客户端回传时只会得到含糊的 unknown config key"

    body = _read(os.path.join(_SRC, "body.html"))
    for eid in ("cfg_openlist_user", "cfg_openlist_pass"):
        assert 'id="{}"'.format(eid) not in body, \
            "设置页仍有 {} 输入框：用户会继续填一个后端已不再接受的字段".format(eid)
    assert 'id="cfg_openlist_token"' in body, "设置页缺少令牌输入（唯一凭据入口）"

    js = _read(os.path.join(_JS_DIR, "settings.js"))
    # 只看**保存配置**那段：自检提示里的 d.openlist_user 是 OpenList /api/me
    # 回报的身份名，与配置无关（不能一并禁掉，否则等于禁止展示「以谁的身份在读」）。
    save_fn = js[js.index("async function saveConfig("):js.index("// 3.6 前置校验辅助")]
    for key in ("openlist_user", "openlist_pass"):
        assert key not in save_fn, \
            "保存配置仍在回传已移除的字段 {}（后端会 400，用户看到「保存失败」）".format(key)
    assert "openlist_token" in save_fn, "保存配置未带上 OpenList 令牌"

    # 产物：删掉的输入框不能还留在生成文件里
    assert "cfg_openlist_user" not in dist and "openlist_pass" not in dist, \
        "产物里仍带着账号/密码入口（需重跑 build.sh）"
    assert "cfg_openlist_token" in dist, "产物未包含令牌输入（需重跑 build.sh）"


def test_openlist_delete_requires_same_source():
    """回归：删除必须与浏览同源。

    OpenList 侧删除用条目名、QAS 侧用 fid。跨源需要猜父目录才能转换，
    猜错就删掉别的东西 —— 后端必须按 source 校验必需字段，不能「尽力而为」。
    """
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert "LIBRARY_SOURCE_OPENLIST" in routes and "必须包含 name" in routes, \
        "OpenList 侧删除未校验 name"
    # issue #57：数据源唯一（OpenList），因此「同源」进一步收紧成
    # 「只认 openlist，其它值一律拒绝」—— 路由层与实现层各拦一道。
    assert 'source != LIBRARY_SOURCE_OPENLIST' in routes, \
        "路由未拒绝非 OpenList 的 source（跨源删除会删错东西）"
    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))
    assert "谁列出来的谁删" in lib, "缺少「同源删除」的说明（这条约束必须写下来防回退）"
    assert "拒绝执行" in lib, \
        "实现层缺少「非 OpenList 源一律拒绝执行」的说明（不能降级到另一条删除路径）"


def test_task_audit_distinguishes_unverified_from_missing():
    """回归：任务对账必须区分「未核对」与「失效」。

    这是这类功能最大的误报来源：任务 savepath 落在扫描范围外时，
    结论只能是「未核对」。混进 missing 会让用户去删掉本来有效的任务配置。
    """
    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    assert '"unverified"' in ol, "对账结果缺少 unverified 分类"
    assert "没扫到" in ol, "缺少「没扫到 ≠ 不存在」的判据说明"
    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "unverified" in js, "前端未展示「未核对」分类"
    assert "不代表失效" in js, "前端未说明「未核对」的语义"
    # 只读：前端不得给对账配删除入口
    audit_fn = js[js.index("async function runTaskAudit("):]
    audit_fn = audit_fn[:audit_fn.index("\nvar AUDIT_HINT")]
    for banned in ("doDelete", "deleteOne", "/api/qas/delete"):
        assert banned not in audit_fn, "对账路径出现破坏性操作（应为只读巡检）"


def test_openlist_settings_subsection_is_collapsible(dist):
    """回归：OpenList 配置必须是可折叠子分区，且默认态随「是否配过」变化。

    为什么要折叠而不是简单加一张卡：设置页有硬性高度门禁（issue #15，
    超宽视口 ≤1500px）。加一张 4 字段的卡实测把整页从 1490px 顶到 1531px，
    直接顶破门禁 —— 而门禁本身是有价值的（它拦的正是「卡片占位过多、大片空白」）。
    折叠是唯一同时满足两边的做法。

    代价是必须防「折叠 = 藏起来」：未配置时默认展开，否则用户只会看到
    媒体库慢，却找不到在哪儿改。本用例把这两个方向都锁住。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="openlistSubGroup"' in body, "OpenList 配置未做成可折叠子分区"
    assert "details" in body[body.index('id="openlistSubGroup"') - 80:
                            body.index('id="openlistSubGroup"') + 40], \
        "子分区容器不是 <details>（无法折叠）"
    # 同一张卡里（不新增设置卡），因此不能在 数据源 组里
    src_group = body[body.index('id="setGroupSource"'):body.index('id="setGroupTransfer"')]
    assert 'id="cfg_openlist"' not in src_group, \
        "OpenList 又变回「数据源」组的独立卡片：会新增一行网格，顶破设置页高度门禁"

    st = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "function _applyOpenListSubState(" in st, "缺少子分区默认态逻辑"
    call = st[st.index("function _applyOpenListSubState("):]
    call = call[:call.index("\n}")]
    assert "d.open=!openlist" in call.replace(" ", ""), \
        "默认态必须由「是否配过」决定（未配置→展开，已配置→收起）"
    assert "_applyOpenListSubState(cfg.openlist)" in st, "加载配置后未应用默认态"

    # 走查脚本必须同时覆盖两种态（已配置→收起，够矮；未配置→展开，够用）
    chk = _read(os.path.join(_ROOT, "scripts", "check_settings_dom.py"))
    assert "inCollapsedDetails" in chk, \
        "走查脚本未跳过折叠区内的元素：会给折叠区算出负数「空白」"
    assert "collapsedSub" in chk, "走查脚本未断言折叠子分区可用性"
    assert "OpenList 未配置" in chk, "走查脚本未覆盖「未配置→默认展开」这一态"
    # issue #47：账号 / 密码入口必须被真实渲染结果断言为「不存在」，
    # 静态扫描只能证明 body.html 里没有，证明不了产物与运行时 DOM 里没有。
    assert "removedEls" in chk, "走查脚本未断言「账号/密码入口已从页面消失」"

    assert "openlistSubGroup" in dist, "产物未包含 OpenList 折叠子分区（需重跑 build.sh）"


def test_openlist_token_auth_is_wired_end_to_end(dist):
    """回归（issue #27 追问）：OpenList 支持「令牌验证」，且令牌必须直发。

    用户原话：「可以把 OpenList 的验证方式变成 OpenList 令牌验证吗，是不是令牌验证的
    权限更大一点」。

    本用例锁住实现的关键形状（行为细节见 tests/test_openlist.py）：
      1. 存在令牌配置项，且按敏感字段处理（加密落盘 + 脱敏回传）；
      2. 令牌走的仍是 OpenList 标准的 Authorization 头，**直发不登录**
         —— 这才是令牌相对其它凭据的收益（少一轮往返）；
      3. 令牌失效时**不许**有任何「自动重登」路径（手填令牌没法重登，只会白等一轮）；
      4. 设置页提供令牌输入，自检把「以什么身份在读」显示出来，产物已重建。
    """
    cfg = _read(os.path.join(_ROOT, "app_modules", "config.py"))
    assert '"openlist_token"' in cfg, "缺少 openlist_token 配置项"
    assert 'OPENLIST_TOKEN' in cfg, "环境变量可设置令牌（与其它配置项同口径）"

    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    assert "def _resolve_auth(" in ol, "缺少鉴权解析入口"
    assert "AUTH_TOKEN" in ol and "AUTH_GUEST" in ol, \
        "缺少鉴权方式区分（无法把「用令牌还是游客」如实告诉用户）"
    assert "def auth_status(" in ol, "缺少不联网的鉴权自检"
    # 令牌直发：命中即返回，不经过任何登录
    auth_fn = ol[ol.index("def _resolve_auth("):]
    auth_fn = auth_fn[:auth_fn.index("def reset_token_cache(")]
    assert "if token:" in auth_fn, "令牌未被直接采用（配了令牌仍可能绕一圈）"
    assert "auth/login" not in auth_fn, "鉴权解析里又出现了登录接口"
    assert 'headers["Authorization"] = token' in ol, "令牌未放进 Authorization 头"
    # 401 只有一条处理：如实报错（不再有「重登一次」这条路）
    api_fn = ol[ol.index("def _api("):]
    api_fn = api_fn[:api_fn.index("def ping(")]
    assert "令牌无效或已过期" in api_fn, "令牌失效未给出可操作的原因"
    assert "AUTH_PASSWORD" not in api_fn, "401 分支里仍留着账号密码的重登逻辑"

    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert "openlist.auth_status()" in routes, "数据源自检未回传鉴权方式"
    assert "openlist_auth" in routes, "自检响应缺少鉴权字段"

    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="cfg_openlist_token"' in body, "设置页缺少 OpenList 令牌输入"

    js = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "openlist_token" in js, "保存配置未带上 OpenList 令牌"
    assert "openlist_auth" in js, "设置页未展示当前鉴权方式"

    assert "cfg_openlist_token" in dist and "openlist_token" in dist, \
        "产物未包含 OpenList 令牌鉴权改动（需重跑 build.sh）"


def test_openlist_token_ui_keeps_mode_hint_honest():
    """回归：设置页不得暗示「令牌权限更大」。

    用户问的就是这个。OpenList 的 Authorization 就是账号自己的 JWT，
    令牌只回答「你是谁」；权限来自角色与目录权限。若 UI 写成
    「令牌权限更大」，用户会以为填了令牌就能删掉原本 403 的东西 ——
    这种误解在删除这种不可逆操作上代价很高，必须由界面文案挡住。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    hint_start = body.index('id="openlistAuthHint"')
    hint = body[hint_start:hint_start + 900]
    assert "权限" in hint, "令牌分区缺少「权限取决于账号/目录权限」的说明"
    assert "令牌" in hint and "游客" in hint, \
        "令牌分区未说明留空时按游客模式访问（现在的另一档语义）"
    assert "唯一" in hint, "令牌分区未点明它是 OpenList 唯一的鉴权方式"

    js = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "不会获得额外权限" in js, \
        "自检提示未点明「令牌不会带来越权」，用户会继续以为令牌权限更大"


def test_openlist_selfcheck_keeps_auth_and_path_semantics(dist):
    """回归：自检链路上「鉴权方式」与「路径前缀自检」必须**联立**生效。

    两条改动落在同一个 `/api/library/source` 上（issue #27 的两次追问）：
      - 一路回答「我到底在用令牌还是账号密码」（问的是鉴权）；
      - 一路回答「登录通过但目录列不出来，多半是路径前缀填错」（问的是回落）。
    合并时若取一边丢一边，就会各丢半个答案：
      只有 `ping` → 前缀填错时自检仍报「可用」，用户继续看着 QAS 徽标找不到原因；
      只有 `self_check` → 答不出「凭据是什么」，配了令牌也不知道生效没有。
    """
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    src_block = routes[routes.index('if route == "/api/library/source":'):]
    src_block = src_block[:src_block.index('if route == "/api/openlist/tasks/audit":')]
    # 鉴权侧（不联网，配置里有什么）
    assert "openlist.auth_status()" in src_block, \
        "自检未回传「配置里是什么凭据」——配了令牌也不知道生效没有"
    assert "openlist_auth_mode" in src_block, "自检未回传鉴权方式"
    # 路径侧（真的列一次目录）
    assert "openlist.self_check(" in src_block, \
        "自检退回成只 ping：路径前缀填错会被误判成可用（「配了却仍回落」的成因）"
    assert "openlist_root_prefix" in src_block, \
        "自检未回传路径前缀：用户无法从提示定位到该改哪个字段"
    assert 'info["openlist_stage"]' in src_block, \
        "自检未区分「登录失败」与「登录通过但列目录失败」"
    # issue #57：数据源唯一，自检失败时 source 保持 openlist ——
    # 徽标与错误态必须指向同一个结论（都怪 OpenList），不能残留一个 qas 源
    assert "LIBRARY_SOURCE_OPENLIST" in src_block, \
        "自检失败时 source 未保持为 openlist（数据源唯一，已无回落）"
    assert "LIBRARY_SOURCE_QAS" not in src_block, \
        "自检仍在回落 qas：QAS 直连已在 issue #57 移除"
    # 两侧字段必须并存，而不是互相覆盖
    for field in ("openlist_auth", "openlist_auth_mode", "openlist_auth_source",
                  "openlist_stage", "openlist_path", "openlist_count",
                  "openlist_root_prefix"):
        assert field in src_block, "自检响应缺少 {}（合并时丢了一边）".format(field)

    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    sc = ol[ol.index("def self_check("):]
    sc = sc[:sc.index("# OpenList / AList")]
    assert "list_dir(" in sc, "自检没有真的列目录：只验登录无法发现路径不匹配"
    assert "stage" in sc, "自检未回报失败发生在哪一步"

    # 设置页文案：登录通过时优先引导到「路径前缀」，而不是让用户去翻令牌
    js = _read(os.path.join(_JS_DIR, "settings.js"))
    assert "openlist_stage==='list'" in js.replace(" ", ""), \
        "自检提示未区分「登录通过但列目录失败」档位"
    assert "路径前缀" in js, "自检提示未把用户引向「路径前缀」"
    # 同时仍要报出「谁 + 什么身份 + 列到了什么」
    assert "openlist_browse_path" in js and "openlist_count" in js, \
        "自检提示丢了实测结果（前缀对不对只能靠它看出来）"
    assert "openlist_auth_mode" in js, "自检提示丢了鉴权方式"

    # 配置校验：两个维度各管各的（令牌=敏感字段不修改，前缀=绝对路径）
    rc = _read(os.path.join(_ROOT, "app_modules", "routes_config.py"))
    assert 'k == "openlist_token"' in rc and 'k == "openlist_root"' in rc, \
        "令牌与路径前缀必须都能保存（两者缺任一个都表现为回落 QAS）"

    assert "cfg_openlist_token" in dist and "cfg_openlist_root" in dist, \
        "产物未包含令牌 + 路径前缀两个输入框（需重跑 build.sh）"


def test_delete_sync_switch_is_wired_end_to_end(dist):
    """回归（issue #57）：删除范围开关必须前后端完整接线，且**默认关**。

    用户诉求：「给 OpenList 模块增加一个开关用于删除 OpenList 指定文件夹下的
    资源库文件时同步删除同名自动夸克转存任务」。

    「加个开关」最容易做成三种半成品，每一种都比不加更危险：
      1. 只有 UI 没有后端 —— 用户勾了、以为删了，任务照样明天把片子拉回来；
      2. 只有后端没有 UI —— 字符串里搜得到 sync_tasks，用户却永远开不了；
      3. 默认打开 —— 用户在「删文件」的弹窗里顺手删掉一批任务配置，不可逆。
    因此这里把「UI + 传输 + 后端 + 默认值」四层一起锁死。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="syncQasTasksChk"' in body, "QAS 任务操作条缺少删除范围开关"
    assert 'id="syncQasTasksFromLibChk"' in body, "网盘删除操作条缺少删除范围开关"
    # 默认态必须是「不勾」：checkbox 不得带 checked 属性
    for eid in ("syncQasTasksChk", "syncQasTasksFromLibChk"):
        seg = body[body.index('id="{}"'.format(eid)) - 160:body.index('id="{}"'.format(eid)) + 40]
        assert "checked" not in seg, \
            "{} 默认是勾上的：用户在「删文件」的弹窗里会顺手删掉一批任务配置".format(eid)
        # 位置必须在对应操作条里（勾选条目的同时才看得到，别处勾了也不该生效）
        bar = "qasActionBar" if eid == "syncQasTasksChk" else "libActionBar"
        bar_start = body.index('id="{}"'.format(bar))
        assert 0 < body.index('id="{}"'.format(eid)) - bar_start < 1200, \
            "{} 不在 {} 里（用户勾选条目时看不到这个选项）".format(eid, bar)

    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function _syncTasksExtra(" in js, "缺少任务侧删除的联动参数构造"
    assert "function _syncTasksFromLibExtra(" in js, "缺少网盘侧删除的联动参数构造"
    # 关着时连字段都不发（写错默认值 = 静默删任务）
    for fn in ("function _syncTasksExtra(", "function _syncTasksFromLibExtra("):
        blk = js[js.index(fn):]
        blk = blk[:blk.index("\n}")]
        assert "syncTasks: false" in blk, "{} 在开关关闭时未短路".format(fn)
    assert "payload.sync_tasks = true" in js, "开关值没有真的发给后端"
    assert "sync_tasks_path_filter" in js, "联动范围没有带给后端（会撞到范围外的同名任务）"
    # 确认框必须复述联动范围：删任务不可逆，不能藏在「确认删除文件」里
    assert "同时会删除同名" in js, "确认文案未复述「会连带删任务」"
    assert "function _tasksResultMsg(" in js, "缺少联动结果的如实回显"

    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert 'body.get("sync_tasks")' in routes, "路由未读取删除范围开关"
    assert "sync_tasks_path_filter" in routes, "路由未读取联动范围"
    assert "不允许包含 .." in routes, "联动范围未做路径越界拦截"
    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))
    assert "def sync_tasks_by_names(" in lib, "缺少按任务名联动删除"
    assert "def match_tasks_by_names(" in lib, "缺少同名匹配"
    assert "def sync_library_entries_by_names(" in lib, "缺少反方向的网盘联动删除"
    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    assert "def match_task_names(" in ol, "openlist 未暴露同名匹配（与扫描共用一套语义）"
    assert "def scan_dir_names(" in ol, "缺少目录扫描入口（对账与联动必须共用范围口径）"

    assert "syncQasTasksChk" in dist and "sync_tasks" in dist, \
        "产物未包含删除范围开关（需重跑 build.sh）"


def test_delete_switches_share_toggle_style_and_persist(dist):
    """回归（issue #57 追问）：两个删除联动开关形态一致 + 状态持久化。

    用户诉求原文：「这个开关和旁边的开关风格保持一致」「并且开关状态在设置后
    持久化保持状态」。指的是网盘删除操作条（#libActionBar）上的两个开关：
      * #syncHistoryChk         「同步清理转存历史」（默认开）
      * #syncQasTasksFromLibChk 「同步删除同名转存任务」（默认关）

    这里锁住两件静态可见的事（浏览器侧的几何/还原由
    scripts/check_library_task_filter_dom.py 走查，见其第 11 节）：

      ① **形态一致**：后者也必须用 .toggle（自绘滑轨）本体，而不是原生 checkbox
         —— 否则又回到「一个是系统蓝方块、一个是滑轨」的视觉裂缝；
      ② **持久化接线**：两个开关都要有 onchange 落点，且 JS 侧读写 localStorage
         （进页还原 + 切换写入）。少了任一半，用户重启页面就回到默认值。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    # ① 两个开关都必须是 .toggle 本体（自绘滑轨 + 隐藏 input）
    for eid in ("syncHistoryChk", "syncQasTasksFromLibChk"):
        idx = body.index('id="{}"'.format(eid))
        seg = body[max(0, idx - 120):idx + 200]
        assert "toggle" in seg and "slider" in seg, \
            "{} 不是 .toggle 滑动开关：两个开关形态不一致（issue #57 追问）".format(eid)
        assert 'onchange="saveLibSyncPrefs()"' in seg, \
            "{} 缺少持久化落点 onchange=saveLibSyncPrefs()".format(eid)
    # `.lib-danger-opt` 只负责配色，不能再用原生 checkbox 当本体
    assert "<input type=\"checkbox\" id=\"syncQasTasksFromLibChk\">" not in body, \
        "破坏性开关仍在用原生 checkbox（应复用 .toggle 本体）"

    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function _restoreLibSyncPrefs(" in js, "缺少开关状态的还原入口"
    assert "function saveLibSyncPrefs(" in js, "缺少开关状态的写入入口"
    assert "LIB_SYNC_HISTORY_KEY" in js and "LIB_SYNC_TASKS_KEY" in js, \
        "两个开关必须各用独立的存储键（互不干扰、便于以后扩展）"
    # 还原必须真的在进页路径上被调用（定义了不调 = 功能不生效）
    assert "_restoreLibSyncPrefs();" in js, "还原入口没有被调用（进页不会恢复状态）"
    # 默认值不能被存储悄悄改掉：无键时必须保留 HTML 的 checked 默认
    restore = js[js.index("function _restoreLibSyncPrefs("):]
    restore = restore[:restore.index("\n}")]
    assert "!== null" in restore, \
        "还原逻辑必须区分「存储里没有键」与「存的是 false」，否则会用存储覆盖 HTML 默认值"

    assert "saveLibSyncPrefs" in dist and "lib_sync_history" in dist, \
        "产物未包含开关持久化逻辑（需重跑 build.sh）"


def test_delete_sync_switch_does_not_refetch_directory_per_entry():
    """回归（issue #57）：删除不得「每删一条就重新列一次目录」。

    旧实现每条删除都 `invalidate_dir_cache()` + `_warm_dir_after_remove()`，
    批量删 N 条就是 N 次全量目录网络往返 —— 用户在媒体库里最常用的操作
    反而成了最慢的。现在改为「命中缓存时只摘掉已删条目」。
    """
    ol = _read(os.path.join(_ROOT, "app_modules", "openlist.py"))
    assert "def drop_dir_cache_entry(" in ol, "缺少「只摘掉已删条目」的缓存维护"
    rem = ol[ol.index("def remove("):ol.index("def drop_dir_cache_entry(")]
    assert "_warm_dir_after_remove" in rem, "缓存冷启动时仍需预热（否则下一次是冷查询）"
    assert "if not drop_dir_cache_entry(" in rem, \
        "未按「缓存是否命中」决定是否预热：会退回成「每条删除都打一次目录网络」"
    assert "invalidate_dir_cache()" not in rem.replace("if not drop_dir_cache_entry", ""), \
        "删除后又整目录作废缓存：下一次浏览会变冷查询（超时 → 回落 QAS）"


def test_openlist_dom_audit_is_committed():
    """走查脚本必须覆盖数据源徽标与对账面板（渲染后才能验证的部分）。"""
    src = _read(os.path.join(_ROOT, "scripts", "check_library_dom.py"))
    assert "libSourceBadge" in src, "走查脚本未断言数据源徽标"
    assert "auditMissingRows" in src or "audit" in src, "走查脚本未断言对账面板"



# ============================================================
# 登录页视觉一致性（issue #27：登录页显示效果与主页不一致）
# ============================================================
_LOGIN = os.path.join(_ROOT, "static", "login_new.html")
_TOKENS = os.path.join(_SRC, "styles", "tokens.css")


def _login_css():
    """拆出 login_new.html 内联 <style> 的内容。"""
    html = _read(_LOGIN)
    return html, "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))


def _theme_block_vars(css, selector):
    """取 tokens.css 里某个主题块内定义的令牌名。

    不能用 `([^{}]+){([^{}]*)}` 这种扁平正则：tokens.css 的主题块
    前面挂着大段注释与标题（选择器里含 `/* ... */`），扁平正则会把选择器
    整体吃掉，导致一个令牌都解析不到（本轮实测踩到过）。
    这里按「找到选择器的 `{` → 配平花括号」来切块，与
    scripts/check_contrast.py 的 block_vars 口径一致。
    """
    idx = css.find(selector)
    if idx < 0:
        return set()
    start = css.index("{", idx)
    depth, end = 0, start
    for i in range(start, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = css[start + 1:end]
    names = set()
    for line in body.splitlines():
        line = line.split("/*")[0].strip()
        m = re.match(r"^(--[\w-]+)\s*:", line)
        if m:
            names.add(m.group(1))
    return names


_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)


def _strip_comments(css):
    """去掉 CSS 注释。

    必须先去掉注释再解析：`:root{` 前面紧挨着一段块注释时，
    扁平的选择器正则会连注释正文一起吃进「选择器」，于是 `:root`
    匹配不上、一个令牌都扫描不到（本轮实测踩到过）。
    """
    return _COMMENT_RE.sub("", css)


def _login_page_local_vars():
    """产出登录页内联 CSS 里定义的自定义属性名（仅顶层 :root / [data-theme] 块）。"""
    _, css = _login_css()
    css = _strip_comments(css)
    names = set()
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sel, body = m.group(1).strip(), m.group(2)
        if sel not in (":root", '[data-theme="light"]', '[data-theme="dark"]'):
            continue
        names |= set(re.findall(r"(--[\w-]+)\s*:", body))
    return names


def test_login_page_does_not_redefine_shared_tokens():
    """回归（issue #27）：登录页不得再重定义 tokens.css 里的设计令牌。

    背景：登录页内联 CSS 里写死了一份深色令牌（`--text:#ffffff`、
    `--border:rgba(255,255,255,.08)`…）。它是**同优先级、后出现**的规则，
    于是把 tokens.css 浅色主题的 `--text:#1c1c1e` 覆盖成了白色 ——
    整页白字白底，主标题在浅色主题下几乎不可见。

    `scripts/check_contrast.py` 只审计 tokens.css，看不见这层覆盖，
    所以这条必须单独锁死：登录页只能用 token，不能再造 token。
    """
    tokens_css = _read(_TOKENS)
    shared = _theme_block_vars(tokens_css, ":root")
    shared |= _theme_block_vars(tokens_css, '[data-theme="light"]')

    assert shared, "未从 tokens.css 解析到任何设计令牌"
    clash = sorted(shared & _login_page_local_vars())
    assert not clash, (
        "登录页重定义了 tokens.css 的令牌 {}：同优先级后出现，会把浅色主题的"
        "取值覆盖掉（历史上正是这样「白字白底」的）".format(clash))


def test_login_page_uses_shared_shell_visuals():
    """回归（issue #27）：登录页与主页必须共用同一套形态/配色口径。

    逐项对应「与主页的视觉效果一致」：
      - 背景：与 main.css 的 body 同一组渐变 + 网格 + --bg-deep 底色；
      - 磨砂面：用 tokens 的 --card / --glass-border / --blur（不是写死的 rgba）；
      - 主色：用 --gradient-btn / --accent，不再自己拼一套蓝紫；
      - 字体字号：用 --font / --font-display / --fs-*。
    """
    html, css = _login_css()
    main_css = _read(os.path.join(_SRC, "styles", "main.css"))

    # 1. tokens.css 仍然被引入（而且是引入 main.css 用的同一份）
    assert '<link rel="stylesheet" href="/static/src/styles/tokens.css">' in html, \
        "登录页未引入 tokens.css，token 单一出处不成立"

    # 2. 背景与主页同一组参数
    for token in ("var(--bg-deep)", "var(--grid-line)", "var(--grid-size)"):
        assert token in css, "登录页背景未使用 {}".format(token)
    for frag in ("radial-gradient(54% 46% at 12% 8%", "linear-gradient(90deg, var(--grid-line) 1px, transparent 1px)"):
        assert frag in css, "登录页背景缺少与主页一致的渐变：{}".format(frag)
        assert frag in main_css, "主页 body 背景里已经找不到 {}，两页已漂移".format(frag)

    # 3. 玻璃面 / 主色 / 字体走 token
    for token in ("var(--card)", "var(--glass-border)", "var(--blur)",
                  "var(--gradient-btn)", "var(--accent)",
                  "var(--font-display)", "var(--fs-"):
        assert token in css, "登录页未使用设计令牌 {}".format(token)

    # 4. 深色硬编码不得回归（这些是上一版「和主页不像」的来源）
    for hard in ("background-color:#0a0a0d", "#0d0d10", "rgba(22,22,26,.5)",
                 "rgba(255,255,255,.045)"):
        assert hard not in css, "登录页仍在写死深色值 {}（应改用 token）".format(hard)

    # 5. 浅色主题降级策略与 main.css 对齐（不具备 backdrop-filter 时给实底）
    assert "@supports not ((backdrop-filter: blur(4px)) or (-webkit-backdrop-filter: blur(4px)))" in css, \
        "登录页缺少 backdrop-filter 降级分支（会退化成半透明叠半透明、文字发灰）"

    # 6. 键盘焦点环 + 减弱动效
    assert ":focus-visible" in css, "登录页缺少键盘焦点环"
    assert "prefers-reduced-motion" in css, "登录页未响应 prefers-reduced-motion"


def test_login_page_dom_audit_is_committed():
    """回归（issue #27）：登录页的「渲染后」缺陷必须由落库的走查脚本守住。

    静态扫描只能看出「有没有用 token」，看不出「渲染出来到底看不看得见」。
    本轮两个缺陷都只有渲染后才能量化：
      1. 浅色主题下主标题实际对比度 1.0:1（白字白底）；
      2. 品牌列深色底图被 background-attachment:fixed 按**视口**铺开，
         在 1336px 宽屏上色带横穿登录按钮，按钮底下是一条浅色渐变横带。

    因此把度量脚本固化进 scripts/，并要求它至少覆盖这两件事。
    """
    script = os.path.join(_ROOT, "scripts", "check_login_dom.py")
    assert os.path.isfile(script), "缺少 scripts/check_login_dom.py（登录页 DOM 度量走查）"
    src = _read(script)

    assert "login_new.html" in src, "走查脚本没加载登录页产物"
    assert "AA_NORMAL" in src and "4.5" in src, "走查脚本没做 WCAG 对比度断言"
    assert "bgOf" in src, "走查脚本必须合成「元素背后的实际底色」才算得出真实对比度"
    assert "backdrop-filter" in src, "走查脚本没断言磨砂是否真的生效"
    assert "for theme in THEMES" in src or "THEMES" in src, "走查脚本没覆盖两套主题"
    # 反例守卫：脚本必须能在「登录页重定义 token」时失败（本轮实测能抓到 1.0:1）
    assert "背景" in src or "bg" in src, "走查脚本缺少背景解析说明"


# 复选 / 单选控件统一（issue #27 反馈：复选框有点丑）
# ============================================================
_CSS_MAIN = os.path.join(_SRC, "styles", "main.css")


def test_shared_checkbox_control_is_defined_once():
    """回归（issue #27）：勾选框必须由**全局一份**规则定义，禁止各处再画一套。

    用户反馈「复选框有点丑」。排查下来丑的原因是「三种长相」：

      - 媒体库卡片角标：15px 系统蓝方块，压在自绘的深色圆底上；
      - 媒体库表格行：16px 原生框 + `accent-color`，跟随浏览器/系统配色；
      - 全选 / 其它页面：裸 `input`，尺寸还被行内 font-size 带着跑。

    所以这里断言「全局控件规则存在且被重置外观」，并把「各处再写一套尺寸」
    的部分一起锁住。
    """
    css = _read(_CSS_MAIN)
    assert 'input[type="checkbox"],\ninput[type="radio"]{' in css or \
           'input[type="checkbox"], input[type="radio"]{' in css, \
        "缺少全局复选/单选控件规则（勾选框会退回浏览器原生外观）"
    assert "appearance:none" in css, "未重置 appearance，控件外观仍由浏览器决定"
    assert 'input[type="checkbox"]:checked' in css, "缺少已勾选态样式"
    assert "::after" in css and "rotate(43deg)" in css, "勾形应由 ::after 绘制（跨浏览器一致）"
    assert ":indeterminate" in css, "缺少半选（indeterminate）态样式"
    assert 'input[type="checkbox"]:focus-visible' in css, "缺少键盘焦点环"

    # 尺寸不受 font-size 影响：必须钉像素（历史：13px/15px/16px 三种）
    block_start = css.index('/* ============ 复选 / 单选控件（全局统一')
    block_end = css.index("/* Sched row */", block_start)
    block = css[block_start:block_end]
    assert re.search(r"width:18px;height:18px", block), \
        "控件尺寸必须固定（不能跟随 font-size，否则每处显示都不同）"

    # 各处不得再自定义勾选框尺寸：只允许保留「热区/光标」这类不改变外观的属性
    for path, snippet in (
        (os.path.join(_SRC, "styles", "main.css"), ".lib-cb input{"),
        (os.path.join(_SRC, "styles", "main.css"), ".lib-card-cb input{"),
    ):
        css_all = _read(path)
        assert snippet in css_all, "缺少 {}".format(snippet)
        body = css_all[css_all.index(snippet) + len(snippet):]
        body = body[:body.index("}")]
        assert "width:" not in body and "height:" not in body, (
            "{} 又自定了勾选框尺寸，会让同一页出现不同大小的勾选框（三种长相复发）".format(
                snippet))


def test_checkbox_dom_audit_is_committed():
    """回归（issue #27）：勾选框的「渲染后」缺陷必须由落库的走查脚本守住。

    静态扫描只能看出「写了什么规则」，看不出：

      1. 浏览器是否真的按自绘样式渲染（appearance 是否被重置、尺寸是否被字号缩放）；
      2. 同一页所有勾选框是否真的**一处一样**（卡片角标 / 表格行 / 全选）；
      3. 已勾选 / 半选态是否真的有可见勾形与横杠；
      4. 深浅两套主题下控件是否仍能从背景里「显形」出来。

    本轮实测该脚本能把「删掉全局控件规则」（报出 12 个原生控件、13x13 尺寸、
    无勾形）与「卡片角标改回 15px」（报出尺寸不一致）两种回退抓出来。
    """
    script = os.path.join(_ROOT, "scripts", "check_checkbox_dom.py")
    assert os.path.isfile(script), "缺少 scripts/check_checkbox_dom.py（勾选框 DOM 度量走查）"
    src = _read(script)
    assert "index_new.html" in src, "走查脚本没加载构建产物"
    assert ".lib-card-cb input" in src and "#libSelAll" in src, \
        "走查脚本必须同时采样卡片角标、表格行与全选三处（三种长相的来源）"
    assert "appearance" in src, "走查脚本没断言 appearance 被重置"
    assert "indeterminate" in src, "走查脚本没断言半选态"
    assert "sizeUnderZoom" in src, "走查脚本没断言尺寸不受 font-size 影响"
    # 「全选」在有数据时必须可见（issue #27：它从独占一行挪进了目录控制条，
    # 采样点因此可能因为容器隐藏而量成 0x0；脚本必须区分「有意隐藏」与「真的消失」）
    assert "required" in src, "走查脚本未区分「必需采样点」与「允许隐藏的采样点」"
    assert "THEMES" in src, "走查脚本没覆盖两套主题"
    assert "MIN_NON_TEXT" in src, "走查脚本缺少非文本对比度阈值"


# ============================================================
# 合并门禁：OpenList 数据源（#43）× 媒体库打开速度（#42）
# ============================================================
# 两轮改动落在同一条「目录读取」链路上：#43 把目录读取改为 OpenList 优先、
# 失败回落 QAS；#42 引入后端目录短命缓存与 refresh 绕过、首帧骨架占位。
# 冲突解决时极易「取一边丢一边」（实测本次合并中 routes 曾把 refresh 语义取反，
# 导致「刷新」按钮失效）。本用例把合并后的**联立**行为钉死，防再次回归。

def test_library_read_path_keeps_both_source_and_cache_semantics(dist):
    """目录读取链路必须同时保留「数据源分发」与「缓存/骨架」两套语义。"""
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    lib = _read(os.path.join(_ROOT, "app_modules", "media_library.py"))

    # 1. 读取入口（#57）：路由走统一入口 list_library_entries（唯一数据源 OpenList）
    assert "media_library.list_library_entries(" in routes, \
        "路由未走目录读取入口（会绕过数据源与失败归因逻辑）"
    assert "refresh=self._cache_disabled(params)" in routes, \
        "refresh 语义取反：refresh=1 必须表示「绕过缓存」，取反会让「刷新」失效"

    # 2. 缓存语义（#42）：refresh 必须真正下传到 OpenList 的列表缓存
    assert "openlist.list_dir(target, refresh=refresh)" in lib, \
        "OpenList 列表缓存开关未接到 refresh 上（合并时丢了缓存绕过逻辑）"

    # 3. 前端（#42+#43）：一次响应同时写「缓存条目 + 骨架行数 + 数据源元信息」
    js = _read(os.path.join(_JS_DIR, "library.js"))
    load = js[js.index("async function loadLibrary("):js.index("function _renderLibError(")]
    assert "meta = {source: res.source, fallback: res.fallback," in load, \
        "前端未保留数据源元信息（合并时把 #43 的 meta 丢了）"
    # 回落原因必须一路带到前端（issue #27「配了 OpenList 却仍回落」）：
    # 只显示「已回落」而不给原因，用户无从判断是地址错还是路径前缀错。
    assert "fallback_reason" in load and "fallback_hint" in load, \
        "前端未保留回落原因（回落只会显示「已回落」，无法排查）"
    assert "_libRememberRows(entries.length)" in load, \
        "前端未保留骨架行数校准（合并时把 #42 的首帧占位丢了）"

    # 4. 产物两侧特性都在
    assert "libSourceBadge" in dist, "产物缺少数据源徽标（#43）"
    assert "lib-grid-skeleton" in dist, "产物缺少首帧骨架（#42）"


def test_library_cache_never_asserts_data_source(dist):
    """缓存内容可以复用，但**数据源结论只能来自最近一次真实响应**。

    背景（issue #47 追问「在文件目录返回上一级时 OpenList 自动回落」）：
    渲染缓存时把缓存里的 meta 一起当「当前状态」用，会产生一个很坑的现象 ——
      根目录第一次回落 QAS → 进子目录（OpenList 正常）→ 返回根目录命中缓存
      → 徽标一直停在「QAS（OpenList 回落）」、提示说「OpenList 不可用」，
      而 OpenList 其实完全正常，而且此后**不会再有任何请求**来纠正它。

    实测确认过：`loadLibrary('/')` 在缓存命中时只调一次 `_renderLibrary`
    （fromCache=true），所以「徽标只可能来自那次渲染」是硬事实。

    这条用例用**结构断言**把不变式钉住：缓存分支渲染时必须传 null
    （= 只画内容、不对数据源下结论）。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    load = js[js.index("async function loadLibrary("):js.index("function _renderLibError(")]
    # 两条缓存分支都不得把缓存里的 meta 当当前状态渲染
    assert "_renderLibrary(target, hit.entries, true, null, hit.meta)" not in load, \
        "缓存命中时用缓存的 meta 断言数据源：徽标会停在过期结论且无人纠正"
    assert load.count("_renderLibrary(target, hit.entries, true, null, null)") == 2, \
        "两条缓存分支（新鲜 / 陈旧）都必须「只画内容、不对数据源下结论」"

    # _renderLibrary 在 source 为 null 时必须**原样保留**当前状态
    # （无条件覆盖会把回落原因清空，留下一个自相矛盾的状态）
    render = js[js.index("function _renderLibrary("):js.index("// ---------- 列表视图")]
    assert "if(source){" in render, \
        "_renderLibrary 未区分「无 source 断言」与「source 为空对象」"
    assert "if(source && source.source) libSource = source.source;" not in render, \
        "仍在使用「部分覆盖」：null 会清空回落原因却留下旧 source"


def test_library_error_state_clears_stale_source_badge(dist):
    """失败态不得继续挂着上一轮的目录结论（徽标是断言，隐藏 ≠ 撤销）。

    实测：先在某目录读到内容，再打开另一个目录、这次失败了 —— 页面会同时
    显示「读取失败」和一个关于上一轮的陈旧徽标，用户据此判断时必然出错。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    err = js[js.index("function _renderLibError("):js.index("// 渲染一次目录")]
    assert "libSource = 'openlist';" in err, \
        "失败态未把数据源重置为唯一事实（openlist）"
    assert "libFallback = false;" in err, \
        "失败态未清掉「回落」标记：会残留早就不成立的结论"
    assert "_renderLibSourceBadge();" in err, \
        "失败态未重绘徽标：会残留上一轮的数据源结论"
    # issue #57：失败原因（kind）**不能**在失败态里被清空 ——
    # 下面两行文案正是靠它区分「未配置」与「配了但读不到」。
    assert "libFallbackKind = '';" not in err, \
        "失败态清空了失败归类：错误提示会退化成一句笼统的「读取失败」"
    assert "未配置 OpenList" in err, \
        "失败态未针对「未配置」给出去哪配置的引导"


def test_library_drops_entries_without_name(dist):
    """无名条目必须被丢弃，并且如实计数。

    OpenList / QAS 都可能返回 name 为空串的条目：渲染出来是一张
    名称空白、勾选框与删除按钮 aria-label 全空的卡片（实测确认），
    而且按条目名删除时根本不成立（后端会报「未指定要删除的条目」）——
    用户点了删除却什么都没发生，且无从理解为什么。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    load = js[js.index("async function loadLibrary("):js.index("function _renderLibError(")]
    assert "String(it.name || '').trim()" in load, \
        "未过滤无名条目（会渲染出无法删除、无标签的空壳卡片）"
    assert "libHiddenNoName" in load, \
        "丢弃了条目却没如实计数：用户会以为「数据少了」"
    # 计数文案必须把这件事说出来
    assert "项无名称未显示" in js, "计数里未说明有条目因无名称被隐藏"


def test_library_delete_guards_entry_dir_consistency(dist):
    """OpenList 侧删除必须按「条目所属目录 + 条目名」提交，并校验一致性。

    OpenList 的 /fs/remove 认的是「目录 + 条目名」这一对，而目录如果取
    「删除那一刻的 libPath」，就等于假设渲染目录与删除目录始终相同 ——
    这个假设一旦不成立就会**删掉别的东西**（不可逆）。
    因此：条目所属目录在渲染时固定（it._dir），删除前再校验一次。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "it._dir = path;" in js, \
        "条目未在渲染时固定所属目录（删除时会用漂移后的 libPath）"
    assert "e.dir = it._dir || libPath;" in js, \
        "删除未使用条目自己的目录"
    do_del = js[js.index("async function doDelete("):js.index("function deleteOne(")]
    assert "删除已取消" in do_del and "e.dir !== libPath" in do_del, \
        "删除前缺少「条目目录 vs 当前目录」一致性校验"


def test_openlist_prefers_diagnostic_budget_unknown_before_browsing(dist):
    """自检不得因为「kind 不认识」而丢掉「目录不存在」这个信息。

    openlist.self_check 在 path_missing 时由 list_dir 给出 kind，
    路由若不透传 openlist_path_missing，前端就只能给笼统提示，
    用户又会去乱试地址 / 令牌。
    """
    routes = _read(os.path.join(_ROOT, "app_modules", "routes_library.py"))
    assert 'info["openlist_path_missing"]' in routes, \
        "自检未透传「目录不存在」标记（前端只能笼统提示，用户会乱试配置）"


# ---------- 媒体库键盘选择（issue #53） ----------
#
# 本组是**静态**门禁（不依赖浏览器），与 scripts/check_library_keys_dom.py
# 的渲染后走查互补：
#   - 这里挡的是「实现被删掉/接线被拆掉」这类回归（改一次代码就触发）；
#   - 走查挡的是「代码在、但行为不对」（需要浏览器，CI 里可能跳过）。
# 两者都必要：静态扫描证明不了「Ctrl+A 之后真的勾上了」，而走查在
# 无 playwright 的机器上不会跑。


def test_library_keyboard_selection_is_implemented(dist):
    """媒体库必须实现整套键盘选择（issue #53：「没有批量全选和范围选择的快捷键」）。"""
    js = _read(os.path.join(_JS_DIR, "library.js"))
    # 全选 / 清空 / 范围 / 当前行 / 勾选，缺一不可
    for name in ("_libKeydown", "libSelectAll", "libClearSelection",
                 "_libSelectRange", "libFocusRow", "libCardClick", "libRowKeydown"):
        assert "function " + name in js, "媒体库缺少键盘选择实现 {}".format(name)

    # Ctrl/⌘+A 与 Esc 必须在按键处理器里显式分支
    kd = js[js.index("function _libKeydown("):]
    assert "lower === 'a'" in kd and "preventDefault" in kd, \
        "未实现 Ctrl/⌘+A 全选（或未阻止浏览器「选中整页文字」的默认行为）"
    assert "'Escape'" in kd, "未实现 Esc 清空选择"
    assert "ArrowUp" in kd and "ArrowDown" in kd, "未实现方向键移动当前行"
    assert "e.shiftKey" in kd, "未实现 Shift 范围选择"


def test_library_keyboard_wired_into_global_shortcuts(dist):
    """键盘选择必须接在全局 keydown 上，且**排在 Esc 分支之前**。

    踩坑（实测）：init.js 的 Esc 分支原来对任何 Escape 都 `return`
    （只处理确认框/Sheet，没有就什么都不做）。把媒体库处理器排在它后面时，
    「Ctrl+A 全选后按 Esc」永远收不到按键 —— 功能写了但完全不生效。
    这条断言把这个顺序钉死。
    """
    init = _read(os.path.join(_JS_DIR, "init.js"))
    assert "_libKeydown(e)" in init, "全局快捷键未接入媒体库的键盘选择"
    # 顺序断言：_libKeydown 的调用必须出现在「Esc 分支」之前
    idx_lib = init.index("_libKeydown(e)")
    idx_esc = init.index("// Esc：关闭确认框 / Sheet")
    assert idx_lib < idx_esc, \
        "媒体库键盘处理排在 Esc 分支之后：Esc 会被提前 return 掉，清空选择永远不生效"


def test_library_keyboard_syncs_rendered_checkboxes(dist):
    """键盘选择必须把状态回写到勾选框 DOM。

    踩坑（实测）：原来只改 libSelected（鼠标点击时浏览器自己会翻转勾选框），
    于是键盘范围选择出现「操作条显示已选 3 项、屏幕上 12 个勾选框全空」——
    用户看到的就是「按了 Shift+↓ 什么都没发生」。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "function _libSyncCheckboxes" in js, \
        "缺少「把选择状态写回勾选框」的统一出口"
    rng = js[js.index("function _libSelectRange("):js.index("function libSelectAll(")]
    assert "_libSyncCheckboxes(rows)" in rng, \
        "范围选择没有同步勾选框 DOM（内部状态与屏幕会不一致）"
    assert "updateLibActionBar()" in rng, \
        "范围选择没有刷新操作条计数/删除按钮状态"


def test_library_rows_carry_key_for_keyboard(dist):
    """两种视图的行都必须带 data-key，否则键盘逻辑找不到对应条目。

    踩坑（实测）：列表视图的行最初只有 tabindex/onclick，没有 data-key，
    结果 findEntry 恒为空 —— 按空格「有反应但什么都没选中」（静默失效）。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    shell = js[js.index("function _libCardShell("):js.index("function _libMetaHtml(")]
    assert "data-key=\"' + esc(it._key) + '\"" in shell, \
        "卡片视图的行缺少 data-key"
    table = js[js.index("function _libTableHtml("):js.index("function _libCardHtml(")]
    assert "data-key=\"' + esc(it._key) + '\"" in table, \
        "列表视图的行缺少 data-key（键盘选择会静默失效）"


def test_library_keyboard_covers_both_views(dist):
    """键盘逻辑必须同时覆盖卡片视图与列表视图。

    列表视图**没有** #libGrid（它是 .lib-table），只查 #libGrid 会让
    「切成列表视图后快捷键全部失效」—— 实测就是这样。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    els = js[js.index("function libRowEls("):js.index("function _libFocusIdx(")]
    assert "lib-table" in els, \
        "行元素查询未覆盖列表视图（.lib-table），切视图后快捷键会失效"
    # 收敛容器：任务表用同一套 .lib-row，不限定 #libList 就会把 20 条任务
    # 混进媒体库的行列表（issue #53 追问：「这里也需要快捷」时实测踩到）。
    assert "#libList" in els, \
        "行元素查询未收敛在 #libList：会把 QAS 任务表的行一起捞进来（选择全错位）"


def test_library_keyboard_resets_state_on_dir_change(dist):
    """换目录 / 重渲染必须清空 focus、anchor 与范围基线。

    否则「在 A 目录按 Shift 选了一段，进 B 目录再按 Shift+↓」会拿旧锚点
    去算新列表的下标，选出来的范围莫名其妙。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    render = js[js.index("function _renderLibrary("):js.index("// ---------- 列表视图")]
    for token in ("libFocusKey = null", "libSelectAnchor = null", "libRangeBase = null"):
        assert token in render, \
            "重渲染未重置键盘状态 {}（换目录后选择会串味）".format(token)
    err = js[js.index("function _renderLibError("):js.index("function _renderLibrary(")]
    for token in ("libFocusKey = null", "libSelectAnchor = null", "libRangeBase = null"):
        assert token in err, "失败态未重置键盘状态 {}".format(token)


def test_library_keyboard_hint_is_visible(dist):
    """快捷键要「看得见」才有人用（issue #53 的诉求就是「不知道怎么选」）。"""
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="libKeyHint"' in body, "目录栏缺少键盘操作提示"
    for token in ("Ctrl/⌘", "A", "Shift", "Esc"):
        assert token in body[body.index('id="libKeyHint"'):body.index('id="libKeyHint"') + 500], \
            "键盘提示缺少 {} 的说明".format(token)
    css = _read(os.path.join(_SRC, "styles", "main.css"))
    assert ".lib-keyhint{" in css.replace(" ", ""), "缺少键盘提示的样式"
    # 窄屏（触屏）要收起提示，别占地方
    assert ".lib-keyhint{display:none}" in css.replace("\n", "").replace(" ", "") or \
        re.search(r"@media\(max-width:900px\)\{\.lib-keyhint\{display:none\}\}", css.replace("\n", "").replace(" ", "")), \
        "窄屏未收起键盘提示"


def test_library_keyboard_row_has_visible_cursor(dist):
    """「当前行」必须有可见标记（虚线框）+ 选中行底色。

    没有光标标记就谈不上范围选择 —— 这正是「选中效率太低」的根因。
    """
    css = _read(os.path.join(_SRC, "styles", "main.css"))
    flat = css.replace("\n", "").replace(" ", "")
    assert ".lib-row.lib-current,.lib-card.lib-current{" in flat, \
        "两种视图的「当前行」标记未统一（卡片看得见、列表看不见 = 会被当 bug 报回来）"
    assert "border-top:1pxdashedvar(--accent)" in flat, \
        "「当前行」没有虚线标记（用户看不出 ↑↓ 会从哪一行开始）"
    assert ":has(input:checked)" in flat, \
        "缺少「已选中行」的底色标记（全选 70 项时只能靠 18px 勾选框辨认）"


# ---------------------------------------------------------------------------
# 媒体库显示密度（issue #53 追问：「信息密度太低了」）
# ---------------------------------------------------------------------------

def test_library_density_toggle_exists(dist):
    """媒体库必须能单独切换「显示密度」（issue #53 追问）。

    用户原话：「新增切换显示密度的功能当前只能列表显示，实在是信息密度太低了」。

    读法有两种，本轮按**字面**取第一种，并把第二种一并做掉：
      ① 只有一个「密度」开关，但它只会切到列表视图 → 那就要一个真正的密度维度；
      ② 想要的是「卡片视图下也有一屏塞更多条目的档位」→ 就是紧凑密度。
    两种读法都需要同一件事：**密度与布局解耦**，各自一个按钮。
    另外旧版本把展示形态存成**一个** `lib_view` 枚举，这里锁死它被读懂、不被重置。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    css = _read(_CSS_MAIN)
    body = _read(os.path.join(_SRC, "body.html"))

    # 1. 两个独立的偏好键，且旧值 'card' / 'list' 仍被认作布局
    assert "LIB_DENSITY_KEY" in js and "LIB_VIEW_KEY" in js, \
        "密度必须是独立偏好键（合并成一个枚举以后再扩展就要二次迁移）"
    assert "'comfortable'" in js and "'compact'" in js, "缺少两档密度的取值"
    assert "function libDensityMode(" in js, "缺少密度偏好读取"
    assert "function toggleLibDensity(" in js, "缺少密度切换入口"
    assert "function _updateLibDensityBtn(" in js, "缺少密度按钮文案更新"
    # 2. 布局与密度共用同一条渲染出口（否则会出现「密度切了、键盘逻辑失灵」）
    assert "function _applyLibView(" in js, "布局/密度未收敛到同一条渲染出口"
    apply_fn = js[js.index("function _applyLibView("):js.index("function toggleLibView(")]
    assert "toggleLibView" not in apply_fn.replace("function toggleLibView", ""), \
        "_applyLibView 里混入了布局切换自身"
    for name in ("_updateLibViewBtn()", "_updateLibDensityBtn()", "_renderLibrary("):
        assert name in apply_fn, "切换展示形态时未调用 {}".format(name)
    assert "localStorage.setItem(LIB_DENSITY_KEY" in apply_fn, "密度切换未持久化"

    # 3. 密度落在网格类名上 + 紧凑列宽必须比舒适更窄（否则「密度」没有实感）
    assert "lib-grid-compact" in js, "密度未落到网格类名（CSS 无从生效）"
    assert ".lib-grid.lib-grid-compact{" in css, "缺少紧凑密度的网格样式"
    dense = css[css.index(".lib-grid.lib-grid-compact{"):]
    dense = dense[:dense.index("}")]
    assert "minmax(190px" not in dense, "紧凑密度没有收窄列宽（等于没做）"
    assert "列宽" in css or "密度" in css, "紧凑密度缺少说明性注释"

    # 4. 页面入口 + 产物已重建
    section = body[body.index('id="tabLibrary"'):body.index("<!-- ===== 设置 ===== -->")]
    assert 'id="libDensityBtn"' in section, "媒体库页缺少密度切换按钮"
    assert "toggleLibDensity()" in section, "密度按钮未接线"
    for token in ("libDensityBtn", "lib-grid-compact", "toggleLibDensity"):
        assert token in dist, "产物未包含{}（需重跑 build.sh）".format(token)


def test_library_density_keeps_keyboard_and_time(dist):
    """紧凑密度是**卡片形态**的加密，不得把键盘选择与修改时间搞丢。

    两个必然的分叉点，写在这里挡回归：
      1. 键盘选择靠 `data-key` + `#libGrid` / `.lib-table` 定位行。紧凑视图仍是
         `.lib-card`（只是网格更密），所以 Ctrl+A / Shift+连选必须照旧 ——
         如果哪一轮为了「再密一点」另起一套 DOM（比如换成假的 div 列表），
         快捷键会**静默失效**，和第 53 期「切列表视图后快捷键全失灵」是同一类坑；
      2. 修改时间是 issue #27 起用户明确要的信息，密度开关只能压缩它的**呈现**
         （字号/图标），不能把它整条去掉。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    css = _read(_CSS_MAIN)

    # 1. 紧凑密度不改行结构：行元素查询与卡片模板都不因密度分岔
    els = js[js.index("function libRowEls("):js.index("function _libFocusIdx(")]
    assert "libDensity" not in els, \
        "行元素查询按密度分岔：另一档密度下键盘选择会失灵"
    # 密度**只允许**出现在网格容器类名这一处；单张卡的模板（shell / meta）里
    # 不得出现 libDensity —— 一旦每张卡各自判断密度，行的结构就开始分叉。
    shell_fn = js[js.index("function _libCardShell("):js.index("function _libMetaHtml(")]
    meta_fn = js[js.index("function _libMetaHtml("):js.index("function _libMetaText(")]
    for fn, name in ((shell_fn, "_libCardShell"), (meta_fn, "_libMetaHtml")):
        assert "libDensity" not in fn, \
            "{} 按密度分岔：另一档密度下的行结构/选择语义会走丢".format(name)

    # 2. 修改时间仍在：紧凑密度只允许压字号/隐藏图标，不允许 display:none 整行
    # 取紧凑密度这一整段（从第一条紧凑规则到「首帧骨架」注释为止）。
    # 固定窗口会随注释增删而切错位置（本轮实测）：直接按结束标记切。
    dense_start = css.index(".lib-grid.lib-grid-compact{")
    dense = css[dense_start:]
    end_mark = "/* ---------- 首帧骨架"
    compact_rules = dense[:dense.index(end_mark)] if end_mark in dense else dense
    assert ".lib-card-time{display:none}" not in compact_rules.replace(" ", ""), \
        "紧凑密度把修改时间整行去掉了（用户明确要过「显示修改时间」）"
    flat_rules = compact_rules.replace(" ", "")
    assert ".lib-time-text.lib-time-full{display:none}" in flat_rules, \
        "紧凑密度下完整时间格式未被隐藏（窄卡会被省略号截成「修改 09-16 1…」）"
    assert ".lib-time-text.lib-time-short{display:block;font-size" in flat_rules, \
        "紧凑密度没有对时间做任何降级处理（说明这条规则没生效）"
    # 降级必须真的塞得下：7.5px 是量出来的（63px 名称列，「修改 09-16 13:21」
    # 在 10px 下要 76px）。写死上限，避免后人「顺手调大一档」又把时间截断。
    px = float(flat_rules.split(
        "lib-time-short{display:block;font-size:", 1)[1].split("px", 1)[0])
    assert px <= 8, \
        "紧凑密度的时间字号 {}px 过大：「修改 09-16 13:21」在 63px 名称列里会被截断".format(px)
    # 3. 快捷键提示与操作条不受密度影响（不然「切密后不知道能不能多选」）
    assert "lib-keyhint" not in compact_rules, "紧凑密度规则误伤了快捷键提示条"


# ---------------------------------------------------------------------------
# QAS 转存任务路径筛选（issue #53 追问：「转存任务的管理可以加入路径筛选功能」）
# ---------------------------------------------------------------------------

def test_qas_task_filter_ui_exists(dist):
    """筛选栏三件套必须在源与产物里都在：路径包含 / 任务名包含 / 清除。

    诉求本身就是「能按路径筛」，所以这里先钉住控件存在性；
    行为（真的只剩匹配行、全选不越界）由 scripts/check_library_task_filter_dom.py 验。
    """
    body = _read(os.path.join(_SRC, "body.html"))
    for cid in ("qasFilterBar", "qasFilterPath", "qasFilterName", "qasFilterClear",
                "qasPathChips", "qasPageSize", "qasPrev", "qasNext"):
        assert 'id="{}"'.format(cid) in body, "任务筛选栏缺少控件 #{}".format(cid)
    # 输入框必须回填模型（oninput），否则打字界面不动
    for cid in ("qasFilterPath", "qasFilterName"):
        seg = body[body.index('id="{}"'.format(cid)):]
        assert "oninput=" in seg[:seg.index(">")], \
            "#{} 缺少 oninput 处理器（打字不会触发筛选）".format(cid)

    js = _read(os.path.join(_JS_DIR, "library.js"))
    for name in ("onQasFilterInput", "clearQasFilter", "setQasPathFilter",
                 "filteredQasTasks", "_qasVisibleTasks", "_qasVisibleKeys",
                 "_qasPathPrefixes", "setQasPageSize", "qasPage"):
        assert "function " + name in js, "缺少任务筛选实现 {}".format(name)

    css = _read(os.path.join(_SRC, "styles", "main.css")).replace("\n", "").replace(" ", "")
    for sel in (".task-filter{", ".task-chip{", ".task-chip.on{", ".task-pager{"):
        assert sel in css, "缺少任务筛选栏样式 {}".format(sel)


def test_qas_task_filter_input_feeds_the_model(dist):
    """输入框的 value 必须**被读进模型**。

    踩坑（实测，被走查脚本第 1 条断言抓出）：onQasFilterInput 最初只写
    「重置页码 + 重渲染」，以为 value 会被渲染逻辑自动收编 —— 结果 input 事件
    确实在跑、界面却纹丝不动，模型永远是空的。这里把「读 value」钉死。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    fn = js[js.index("function onQasFilterInput("):js.index("function onQasFilterKeydown(")]
    assert "qasFilter.path" in fn and "qasFilter.name" in fn, \
        "onQasFilterInput 没有把输入框的 value 写进 qasFilter 模型"
    assert ".value" in fn, "onQasFilterInput 没有读输入框的 value"


def test_qas_select_all_is_limited_to_visible_rows(dist):
    """全选**只能**作用于当前可见行（筛选结果 + 当前页）。

    这是本轮最危险的一条：旧实现是「qasSelected = {} 后遍历 qasTasks 全量勾选」。
    加了筛选/分页之后，那等于「看着 19 行、点一下全选、实际勾上 291 条」。
    断言钉在「遍历的是可见行」这一实现事实上。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    fn = js[js.index("function toggleQasSelectAll("):]
    fn = fn[:fn.index("\n}")]
    assert "_qasVisibleTasks()" in fn, \
        "toggleQasSelectAll 没有以可见行为范围（会把被筛掉的行一起勾上）"
    assert "qasTasks.forEach" not in fn, \
        "toggleQasSelectAll 仍在遍历全量 qasTasks —— 筛选后全选会勾上隐藏行"


def test_qas_action_bar_reports_hidden_selection(dist):
    """操作条必须如实标出「不在当前筛选/页内」的勾选数。

    静默带着隐藏勾选去删除，是这类界面最危险的失败模式：
    用户看到「已选 3 条」，实际删掉的可能是 291 条。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    fn = js[js.index("function updateQasActionBar("):]
    fn = fn[:fn.index("function toggleQasSelectAll(")]
    assert "_qasVisibleKeys()" in fn, "操作条没有用可见行去比对勾选，无法发现隐藏勾选"
    assert "不在当前筛选" in fn, "没有把「隐藏勾选」的条数写进操作条文案"


def test_qas_delete_confirm_repeats_filter_scope(dist):
    """筛选生效时，删除确认框必须复述筛选条件与作用条数。

    只说「所选 N 条」，用户会怀疑「我明明有 200 条任务，它是不是要全删」。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    fn = js[js.index("function deleteSelectedQasTasks("):]
    fn = fn[:fn.index("\n}")]
    assert "qasFilterActive()" in fn, "删除所选时没有判断筛选是否生效"
    assert "筛选：" in fn, "删除确认文案没有复述筛选条件"


def test_qas_task_list_is_paginated(dist):
    """任务列表必须有界分页（291 行全渲染 ≈ 6 屏高，且首屏被拖慢）。"""
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="qasPageSize"' in body, "缺少「每页条数」控件"
    for n in ('value="10"', 'value="20"', 'value="50"', 'value="100"'):
        assert n in body, "每页条数缺少选项 {}".format(n)
    js = _read(os.path.join(_JS_DIR, "library.js"))
    assert "QAS_CHIP_MIN" in js, "路径前缀 chips 缺少「至少命中几次」的阈值"
    # 筛选条件变化必须回到第 1 页（否则会停在越界页拿到空列表）
    for fname in ("function onQasFilterInput(", "function setQasPathFilter(",
                  "function setQasPageSize("):
        fn = js[js.index(fname):]
        fn = fn[:fn.index("\n}")]
        assert "qasPageNo = 1" in fn, "{} 没有把页码重置回第 1 页".format(fname)


# ---------------------------------------------------------------------------
# QAS 任务表键盘选择（issue #53 追问：「这里也需要快捷」）
# ---------------------------------------------------------------------------
#
# 用户给 QAS 任务清单的截图配了一句「这里也需要快捷」。这与媒体库那条是
# **同一个诉求的第二处**：媒体库已经有一整套键盘选择，任务表却还得靠鼠标
# 一行行点。本轮把同一套语义搬过去。
#
# 本组同样是**静态**门禁（挡「实现被删掉/接线被拆掉」），
# 「按下 Ctrl+A 之后真的勾上了几行」由 scripts/check_qas_keys_dom.py 验。


def test_qas_keyboard_selection_is_implemented(dist):
    """任务表必须实现整套键盘选择，且按键与媒体库一致。"""
    js = _read(os.path.join(_JS_DIR, "library.js"))
    for name in ("_qasKeydown", "qasSelectAll", "qasClearSelection",
                 "_qasSelectRange", "qasFocusRow", "qasRowClick", "qasRowKeydown",
                 "qasRowEls", "_qasSyncCheckboxes"):
        assert "function " + name in js, "任务表缺少键盘选择实现 {}".format(name)

    kd = js[js.index("function _qasKeydown("):]
    kd = kd[:kd.index("function kbPageAvailable(")]
    assert "lower === 'a'" in kd and "preventDefault" in kd, \
        "任务表未实现 Ctrl/⌘+A 全选（或未阻止浏览器「选中整页文字」的默认行为）"
    assert "'Escape'" in kd, "任务表未实现 Esc 清空选择"
    assert "ArrowUp" in kd and "ArrowDown" in kd, "任务表未实现方向键移动当前行"
    assert "e.shiftKey" in kd, "任务表未实现 Shift 范围选择"
    assert "' '" in kd or "Spacebar" in kd, "任务表未实现空格切换当前行"
    assert "e.ctrlKey || e.metaKey" in kd, "任务表未实现 Ctrl/⌘ 修饰键（macOS 上 ⌘ 会失效）"

    # 两张表必须共用同一套按键语义：把差异钉到「只有一个前缀不同」的层面上。
    kd_sig = set(re.findall(r"e\.key === '([^']+)'", kd)) | set(
        re.findall(r"k === '([^']+)'", kd))
    lib = _read(os.path.join(_JS_DIR, "library.js"))
    lib_kd = lib[lib.index("function _libKeydown("):lib.index("function libFocusRowIdx(")]
    lib_sig = set(re.findall(r"e\.key === '([^']+)'", lib_kd)) | set(
        re.findall(r"k === '([^']+)'", lib_kd))
    missing = lib_sig - kd_sig
    assert not missing, \
        "任务表键盘语义与媒体库分岔，缺了 {}".format(sorted(missing))


def test_qas_keyboard_wired_into_global_shortcuts(dist):
    """任务表键盘处理必须接在全局 keydown 上，且排在 Esc 分支之前。

    与媒体库同一个坑（实测过）：init.js 的 Esc 分支对任何 Escape 都 return，
    排在它后面就永远收不到 Esc —— 「Ctrl+A 全选后按 Esc 没反应」。
    """
    init = _read(os.path.join(_JS_DIR, "init.js"))
    assert "_qasKeydown(e)" in init, "全局快捷键未接入任务表的键盘选择"
    idx_qas = init.index("_qasKeydown(e)")
    idx_esc = init.index("// Esc：关闭确认框 / Sheet")
    assert idx_qas < idx_esc, \
        "任务表键盘处理排在 Esc 分支之后：Esc 会被提前 return 掉，清空选择永远不生效"


def test_qas_keyboard_ignores_typing_in_inputs(dist):
    """焦点在输入框里时，任务表的键盘选择必须完全放行。

    任务表上有两个搜索框（路径包含 / 任务名包含）。不加这层守卫，
    「在搜索框里打一个空格」会被当成「勾选当前行」，而 Esc 也会被当成清空选择，
    用户根本没法正常输入 —— 这是把快捷键搬到带输入框的表上时最容易漏的一条。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    kd = js[js.index("function _qasKeydown("):js.index("function kbPageAvailable(")]
    guard = kd[:kd.index("var rows = qasRowEls()")]
    for tag in ("INPUT", "TEXTAREA", "SELECT"):
        assert "'" + tag + "'" in guard, \
            "焦点在 {} 里时未放行（打字会被快捷键吃掉）".format(tag)


def test_qas_keyboard_scope_is_visible_rows_only(dist):
    """任务表的全选/范围选择只能作用于**可见行**（筛选结果 + 当前页）。

    这是从媒体库继承过来、又在任务表上被放大的一条：任务表有筛选 + 分页，
    「看着 20 行、按一下 Ctrl+A、实际勾上 291 条」在这里是真实可达的。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    # 1. 行元素只能来自 DOM（筛选/分页/每页条数都会改它）
    els = js[js.index("function qasRowEls("):js.index("function _qasFocusIdx(")]
    assert "qasTaskList .lib-row:not(.lib-head)" in els, \
        "行元素查询未走 #qasTaskList（会拿到被筛掉/不在当前页的行）"
    assert "qasTasks" not in els, "行元素查询绕过了渲染结果，直接用全量任务"
    # 2. 全选复用「只动可见行」的既有入口
    allfn = js[js.index("function qasSelectAll("):js.index("function qasClearSelection(")]
    assert "toggleQasSelectAll(true)" in allfn, \
        "任务表全选没有复用「只勾可见行」的既有入口（自己另写一份遍历就会越界）"
    assert "qasTasks" not in allfn, \
        "全选函数里出现了全量 qasTasks —— 这就是「看着 20 行、勾上 291 条」的写法"
    # 3. 范围选择只按行下标在**当前 DOM 行**里取
    rng = js[js.index("function _qasSelectRange("):js.index("function qasSelectAll(")]
    assert "rows[j]" in rng and "findQasTask(" in rng, \
        "范围选择没有按下标在可见行里取（会选到列表外的任务）"
    assert "_qasSyncCheckboxes(rows)" in rng, \
        "范围选择没有同步勾选框 DOM（内部状态与屏幕会不一致）"
    assert "updateQasActionBar()" in rng, "范围选择没有刷新操作条计数/删除按钮状态"


def test_qas_rows_carry_key_and_handlers_for_keyboard(dist):
    """任务表的行必须带 data-key 与鼠标/键盘处理器，否则快捷键静默失效。

    媒体库那边踩过：列表视图的行缺 data-key 时，「按空格有反应但什么都没选中」。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    render = js[js.index("function _renderQasTasks("):js.index("function findQasTask(")]
    assert 'data-key="' in render, '任务行缺少 data-key（键盘选择会静默失效）'
    assert "qasRowKeydown(" in render, "任务行未接 onkeydown（行内空格会翻两次勾选框）"
    assert "qasRowClick(" in render, "任务行未接 onclick（Shift/⌘+点击无法连选）"


def test_qas_keyboard_state_resets_on_reload_and_error(dist):
    """重新加载 / 失败态必须清空焦点、锚点与范围基线。

    否则「在旧任务列表按 Shift 选了一段，刷新后再按 Shift+↓」会拿旧锚点算新
    列表的下标，选出来的范围莫名其妙。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    load = js[js.index("async function loadQasTasks("):js.index("// ---------- 任务筛选（issue #53 追问）")]
    for token in ("qasFocusKey = null", "qasSelectAnchor = null", "qasRangeBase = null"):
        assert token in load, "重新加载任务未重置键盘状态 {}（选择会串味）".format(token)
    err = js[js.index("function _renderQasTasksError("):js.index("function _renderQasTasks(")]
    for token in ("qasFocusKey = null", "qasSelectAnchor = null", "qasRangeBase = null"):
        assert token in err, "任务失败态未重置键盘状态 {}".format(token)


def test_qas_keyboard_keeps_visible_cursor_after_rerender(dist):
    """重渲染后「当前行」的虚线框必须补回来。

    _renderQasTasks 是 innerHTML 整体替换，.lib-current 会被一起丢掉 ——
    翻页/筛选之后虚线框消失，用户就以为「当前行没了」，Shift 连选失去参照。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    render = js[js.index("function _renderQasTasks("):js.index("function findQasTask(")]
    assert "qasFocusRow(" in render, \
        "重渲染后没有恢复「当前行」标记（翻页/筛选后虚线框会消失）"


def test_qas_keyboard_hint_is_visible(dist):
    """快捷键要「看得见」才有人用（诉求本身就是「不知道怎么选」）。"""
    body = _read(os.path.join(_SRC, "body.html"))
    assert 'id="qasKeyHint"' in body, "任务表缺少键盘操作提示"
    seg = body[body.index('id="qasKeyHint"'):body.index('id="qasKeyHint"') + 500]
    for token in ("Ctrl/⌘", "A", "Shift", "Esc", "空格"):
        assert token in seg, "任务表键盘提示缺少 {} 的说明".format(token)
    # 与媒体库共用同一套提示样式：两处口径必须一致，否则「一个看得见一个看不见」
    assert 'class="lib-keyhint qas-keyhint"' in body, \
        "任务表提示条未复用 .lib-keyhint 样式（与媒体库的收起断点会漂移）"
    css = _read(_SRC and os.path.join(_SRC, "styles", "main.css"))
    assert ".qas-keyhint{" in css.replace(" ", ""), "缺少任务表提示条的样式"
    # 收口在 .lib-keyhint 的窄屏规则上：两处一起藏，不会出现只藏一个
    assert ".lib-keyhint{display:none}" in css.replace("\n", "").replace(" ", ""), \
        "窄屏未收起键盘提示（触屏没有这些键，白占地方）"


def test_qas_keyboard_page_turn_on_arrow_at_edge(dist):
    """↑↓ 到页末/页首要能接着翻页，而不是「按了没反应」。

    任务表默认每页 20 条，而一个 /影视/电视剧 前缀常有上百条。
    只能在当前页里移动光标的话，用户要「选到第 21 条」得先放下键盘去点「下一页」
    —— 快捷键的价值会被分页吃掉一半。
    """
    js = _read(os.path.join(_JS_DIR, "library.js"))
    kd = js[js.index("function _qasKeydown("):js.index("function kbPageAvailable(")]
    assert "kbPageAvailable(" in kd, "↑↓ 到边界时没有考虑翻页"
    assert "qasGotoPage(" in kd, "边界处没有走翻页入口"
    goto = js[js.index("function qasGotoPage("):js.index("function qasRowClick(")]
    assert "qasPage(delta)" in goto, "翻页入口没有复用既有的 qasPage（筛选/页码语义会漂移）"
    # 落到哪一端由**方向**决定，跟翻书一致：下一页 → 首行（继续 ↓ 往下走），
    # 上一页 → 末行（继续 ↑ 往上走）。两处都写死 0 的话，「按 ↑ 回上一页」
    # 会把光标甩到页首，用户再按 ↑ 立刻又翻回去 —— 看起来像翻页失灵
    # （本轮实测踩到，走查第 7 条抓的）。
    assert "qasFocusRow(" in goto, "翻页后没有把「当前行」放到本页（继续按键会失去参照）"
    assert "delta < 0" in goto, \
        "翻页后未按方向决定落到页首还是页末（按 ↑ 回上一页会停在页首，再按 ↑ 又翻回去）"
    assert "rows.length - 1" in goto, "上一页没有把「当前行」放到本页最后一行"

    # 产物已重建
    for token in ("_qasKeydown", "qasRowClick", "qasSelectAll", "qasKeyHint"):
        assert token in dist, "产物未包含{}（需重跑 build.sh）".format(token)
