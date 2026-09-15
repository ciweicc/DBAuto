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
