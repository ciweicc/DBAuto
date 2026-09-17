"""确认框「取消也执行」回归门禁（issue #27 严重问题）。

背景：
    `showConfirm()` 返回 Promise<bool>：确认 resolve(true)，取消/遮罩/ESC resolve(false)。
    但两个删除入口曾写成

        try{ await showConfirm(...); }catch(e){ return; }
        // 紧接着就执行删除

    —— 它把「Promise 是否 resolve」当成「用户是否同意」，忽略了返回值，
    于是**点取消也会照删**（用户截图实证）。`resolveConfirm(false)` 只 resolve、
    从不 reject，catch 分支永远走不到。

本用例把修复固化为两道防线：
  1. 语义层：破坏性操作必须调用 `confirmThen(...)`（内部把 ok===true 收敛为布尔真值），
     而不是裸 `showConfirm`；
  2. 结构层：调用点后面必须紧跟 `if(!confirmed) return;` 早退。
另外用 node 实跑 confirmThen 的判定，验证取消时返回 false（而非 undefined/truthy）。
"""
import os
import re
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "src", "scripts")
_CONFIRM = os.path.join(_JS, "confirm.js")
_LIBRARY = os.path.join(_JS, "library.js")
_HISTORY = os.path.join(_JS, "history.js")

_NODE = shutil.which("node")


def _read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def test_confirm_then_exists():
    src = _read(_CONFIRM)
    assert "function confirmThen(" in src, "confirm.js 必须提供语义化的 confirmThen"
    # 必须把返回值收敛为严格真值，避免 truthy 误判
    assert re.search(r"return\s+ok\s*===\s*true", src), \
        "confirmThen 必须用 `ok === true` 判定，保证只有明确确认才为真"


def test_showConfirm_resolves_false_on_cancel():
    """resolveConfirm 语义：取消仍 resolve(false)，不 reject（保持向后兼容）。"""
    src = _read(_CONFIRM)
    assert "function resolveConfirm(result)" in src
    assert "_confirmResolve(result)" in src or "_confirmResolve( result )" in src, \
        "resolveConfirm 必须把结果透传给等待方"


def test_destructive_callers_use_confirm_then():
    lib = _read(_LIBRARY)
    # doDelete / doDeleteQasTasks 两个破坏性入口
    assert "confirmThen('确认删除'" in lib, "doDelete 必须用 confirmThen"
    assert "confirmThen('删除 QAS 任务'" in lib, "doDeleteQasTasks 必须用 confirmThen"
    # 不允许再出现裸 await showConfirm 的破坏性调用
    assert "await showConfirm('确认删除'" not in lib
    assert "await showConfirm('删除 QAS 任务'" not in lib


def test_destructive_callers_early_return_on_cancel():
    """每个 confirmThen 调用后都必须有 `if(!confirmed) return;` 早退。"""
    lib = _read(_LIBRARY)
    calls = [m.end() for m in re.finditer(r"=\s*await confirmThen\(", lib)]
    assert len(calls) >= 2, "应至少有 2 处破坏性确认"
    for pos in calls:
        tail = lib[pos:pos + 400]
        assert re.search(r"if\s*\(\s*!\s*confirmed\s*\)\s*return", tail), \
            "confirmThen 之后必须紧跟 `if(!confirmed) return;` 早退，否则取消仍会执行"


def test_history_clear_checks_result():
    src = _read(_HISTORY)
    assert "confirmThen(" in src, "清空历史也应走 confirmThen"
    assert re.search(r"if\s*\(\s*!\s*ok\s*\)\s*return", src), "清空历史必须校验返回值"


@pytest.mark.skipif(_NODE is None, reason="需要 node 实跑 confirmThen 判定")
def test_confirm_then_runtime_semantics():
    """node 实跑：只有确认(true)才返回 true，取消(false)/undefined 都返回 false。"""
    confirm_src = _read(_CONFIRM)
    # 仅取 confirmThen 定义所需的部分，注入一个可控的 showConfirm 桩
    # 只取 confirmThen 定义（避免 confirmThen 以外的 DOM 依赖），并注入可控 showConfirm 桩
    m = re.search(r"function confirmThen\([\s\S]*?^\}", confirm_src, re.M)
    assert m, "未找到 confirmThen 定义"
    confirm_then_src = m.group(0)
    harness = """
    var _results = [true, false, undefined, 0, '', null];
    var _i = 0;
    function showConfirm(){ return Promise.resolve(_results[_i++]); }
    %s
    (async function(){
      var out = [];
      for(var i=0;i<_results.length;i++){ out.push(await confirmThen('t','d','ok','cancel')); }
      process.stdout.write(JSON.stringify(out));
    })();
    """ % confirm_then_src
    r = subprocess.run([_NODE, "-e", harness], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "[true,false,false,false,false,false]", r.stdout


# ---------- 媒体库根目录下界（issue #27「界定资源根目录」） ----------

def test_library_root_is_configurable_and_bounded():
    """媒体库根目录来自设置，且作为浏览下界（不能返回上一级越过它）。"""
    src = _read(_LIBRARY)
    assert "APP_PATHS.library_root" in src, "媒体库根目录必须来自配置 APP_PATHS.library_root"
    assert "function libRoot(" in src, "缺少根目录解析函数 libRoot"
    assert "function libAtRoot(" in src, "缺少「是否已在根目录」判定 libAtRoot"
    # 上一级：到根即停
    up = src[src.index("function libGoUp("):]
    up = up[:up.index("\nfunction ")] if "\nfunction " in up else up
    assert "libAtRoot(libPath)" in up, "libGoUp 必须以 libAtRoot 作为下界判定"
    # 上一级按钮在根目录禁用
    assert "upBtn.disabled = libAtRoot(libPath)" in src, "根目录时「上一级」必须禁用"
    # 面包屑回到配置的根目录，而不是硬编码 '/'
    bc = src[src.index("function renderBreadcrumb("):]
    bc = bc[:bc.index("\nfunction ")] if "\nfunction " in bc else bc
    assert "libRoot()" in bc, "面包屑根节点必须回到配置的根目录"
    assert "libRootDefault" not in src, "不得残留临时占位函数"


def test_library_root_field_in_settings_page():
    body = open(os.path.join(_ROOT, "static", "src", "body.html"), encoding="utf-8").read()
    assert 'id="cfg_path_library_root"' in body, "设置页缺少「媒体库根目录」输入框"
    settings = _read(os.path.join(_JS, "settings.js"))
    assert "cfg_path_library_root" in settings, "设置页未接线 library_root 的读取"
    assert "library_root:" in settings, "保存设置未提交 library_root"
    assert "'cfg_path_library_root'" in settings, "校验列表未包含 library_root"
