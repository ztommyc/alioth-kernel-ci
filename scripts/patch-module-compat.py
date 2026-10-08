#!/usr/bin/env python3
# ============================================================
# patch-module-compat.py
#
# 作用：让自编内核能够加载 ROM 自带的原厂内核模块（/vendor/lib/modules/*.ko）
#
# 背景（2026-10-08 对手机运行内核的实测）：
#   原厂内核 release 串 : 4.19.157-perf-g92c089fc2d37
#   原厂模块 vermagic  : 4.19.157-perf-g92c089fc2d37 SMP preempt mod_unload modversions aarch64
#   原厂内核 CONFIG_MODVERSIONS=y（会比对导出符号 CRC）
#
#   而社区内核树是 4.19.325，release 串必然不同 ⇒ vermagic 必然不匹配，
#   符号 CRC 也几乎不可能一致。若不处理，/vendor/lib/modules 下 42 个模块
#   （WiFi / 音频 / 指纹）将全部加载失败。
#
# 处理方式：整段替换两个校验函数的函数体（而不是在开头插 return），
#   1) check_modinfo()  —— 跳过 vermagic 字符串比对
#   2) check_version()  —— 跳过导出符号 CRC 比对
#   整段替换可避免 unreachable code / unused variable 警告，
#   从而不会因为内核开启 -Werror 而导致编译失败。
#
# 风险说明：放行后，模块与内核之间的结构体布局差异可能导致异常。本树与 Xiaomi
#   官方源码同源（同为 alioth / SM8250 / CLO LA.UM.9.12 系列），差异有限。
#   彻底解法是用同一棵源码编译出的 .ko 覆盖 /vendor/lib/modules（见 README 策略一）。
#
# 幂等：可重复执行。
# ============================================================

import re
import sys

MODFILE = "kernel/module.c"
MARK = "alioth-docker-patch"

# 目标函数 -> (替换后的函数体, 说明)
TARGETS = {
    "check_modinfo": (
        "\treturn 0;",
        "跳过 vermagic 校验，放行原厂模块",
    ),
    "check_version": (
        "\treturn 1;",
        "跳过导出符号 CRC 校验",
    ),
}


def find_body_span(src: str, start_brace: int):
    """从 '{' 的下标开始，做括号配对，返回 (body_start, body_end, close_index)"""
    i = start_brace
    depth = 0
    n = len(src)
    while i < n:
        c = src[i]
        # 跳过块注释
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = (j + 2) if j != -1 else n
            continue
        # 跳过行注释
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = (j + 1) if j != -1 else n
            continue
        # 跳过字符串 / 字符字面量
        if c in ('"', "'"):
            quote = c
            i += 1
            while i < n:
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return start_brace + 1, i, i
        i += 1
    return None


def patch_function(src: str, func_name: str, body: str, label: str):
    m = re.search(
        r"static\s+int\s+" + re.escape(func_name) + r"\s*\([^)]*\)\s*\{",
        src,
        re.S,
    )
    if not m:
        print(f"  !! 未找到函数 {func_name}，无法打补丁")
        return src, False

    brace = m.end() - 1  # '{' 的下标
    span = find_body_span(src, brace)
    if span is None:
        print(f"  !! {func_name} 括号配对失败，中止")
        return src, False

    body_start, body_end, _ = span
    existing = src[body_start:body_end]
    if MARK in existing:
        print(f"  -- {func_name} 已打过补丁，跳过")
        return src, True

    new_body = f"\n{body} /* {MARK} */  /* {label} */\n"
    new_src = src[:body_start] + new_body + src[body_end:]
    print(f"  ++ {func_name} 函数体已替换（{label}）")
    return new_src, True


def main():
    try:
        with open(MODFILE, "r", encoding="utf-8", errors="surrogateescape") as f:
            src = f.read()
    except FileNotFoundError:
        print(f"!! 找不到 {MODFILE}，请确认在源码根目录执行本脚本")
        return 1

    if src.count("{") != src.count("}"):
        print("!! 源码花括号数量不平衡，patch 中止以免破坏文件")
        return 1

    ok = True
    for func, (body, label) in TARGETS.items():
        src, r = patch_function(src, func, body, label)
        ok &= r

    if not ok:
        print("!! 补丁未全部应用，中止")
        return 1

    if src.count("{") != src.count("}"):
        print("!! 打补丁后花括号数量不平衡，中止写入")
        return 1

    with open(MODFILE, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(src)

    print(f"== kernel/module.c 模块兼容补丁完成（{len(TARGETS)} 处）==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
