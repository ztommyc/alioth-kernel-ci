#!/usr/bin/env python3
# ============================================================
# patch-module-compat.py  (v2 · 引用保留式外科补丁 + 严格校验)
#
# 作用：让自编内核能够加载 ROM 自带的原厂内核模块（/vendor/lib/modules/*.ko）
#
# 背景（2026-10-08 对手机运行内核的实测）：
#   原厂内核 release 串 : 4.19.157-perf-g92c089fc2d37
#   原厂模块 vermagic  : 4.19.157-perf-g92c089fc2d37 SMP preempt mod_unload modversions aarch64
#   原厂内核 CONFIG_MODVERSIONS=y（会比对导出符号 CRC）
#
#   社区内核树是 4.19.325，release 串必然不同 ⇒ vermagic 必然不匹配，
#   符号 CRC 也几乎不可能一致。若不处理，/vendor/lib/modules 下 42 个模块
#   （WiFi / 音频 / 指纹）将全部加载失败。
#
# ------------------------------------------------------------
# 为什么不能用"整段替换函数体"的写法（v1 的错误做法）：
#
#   函数体一旦被抽空，原先只被这两个函数调用的静态函数就失去全部调用者：
#       check_modinfo_retpoline()   ← 原调用点在 check_modinfo() 内
#       set_license()               ← 原调用点在 check_modinfo() 内
#       resolve_rel_crc()           ← 原调用点在 check_version() 内
#   而该内核树 Makefile：
#       KBUILD_CFLAGS := -Wall ...                                    (455 行，-Wall 含 -Wunused-function)
#       KBUILD_CFLAGS += cc-disable-warning unused-but-set-variable   (780 行)
#       KBUILD_CFLAGS += cc-disable-warning unused-const-variable     (782 行)
#   —— 只关掉了 unused-but-set-variable / unused-const-variable，
#      **没有关 unused-function**；且 alioth_defconfig（795 行）中 CONFIG_CC_WERROR=y
#      会加上 -Werror ⇒ "defined but not used" 直接升级为编译错误。
#
# v2 改为**外科式改动**：函数体完整保留，只改判定分支的"返回动作"：
#   1) check_version()  bad_version 处：return 0 → return 1（CRC 不匹配不再拒绝）
#   2) check_modinfo()  vermagic 不匹配分支：去掉 return -ENOEXEC，只记日志后继续
#   于是全部辅助函数（same_magic / try_to_force_load / resolve_rel_crc / set_license /
#   check_modinfo_retpoline / check_modinfo_livepatch …）的调用点原样留存，
#   **不产生任何无引用符号**。且行为更保守：license taint、retpoline 检查、
#   out-of-tree taint 等全部保持不变。
#
# 校验策略：不设"兜底自动改写"（未经验证的代码路径比快速失败更危险）。
#   若预期文本未命中，脚本立即以非 0 退出并打印期望原文，让 CI 在 2 分钟内失败，
#   而不是产出一个可疑的补丁。工作流已把内核树锁定到具体 commit，正常不会发生。
#
# 风险说明：放行后，模块与内核之间的结构体布局差异仍可能导致异常。本树与 Xiaomi
#   官方源码同源（同为 alioth / SM8250 / CLO LA.UM.9.12 系列），差异有限。
#   彻底解法是用同一棵源码编译出的 .ko 覆盖 /vendor/lib/modules（见 README 策略一）。
#
# 幂等：可重复执行。
# ============================================================

import re
import sys

MODFILE = "kernel/module.c"
MARK = "alioth-docker-patch"

# ------------------------------------------------------------
# 外科式改动清单：(函数名, 原文, 替换为)
# 原文必须与内核源码逐字节一致（含制表符）。
# ------------------------------------------------------------
SURGICAL = [
    (
        "check_version",
        # 原：CRC 不匹配 → 警告 + return 0（拒绝该符号）
        "bad_version:\n"
        '\tpr_warn("%s: disagrees about version of symbol %s\\n",\n'
        "\t       info->name, symname);\n"
        "\treturn 0;\n",
        # 改：降级为调试日志 + return 1（放行）
        "bad_version:\n"
        "\t/* " + MARK + ": CRC mismatch ignored, allow vendor module */\n"
        '\tpr_debug("%s: version mismatch for %s (ignored)\\n",\n'
        "\t       info->name, symname);\n"
        "\treturn 1;\n",
    ),
    (
        "check_modinfo",
        # 原：vermagic 不匹配 → 报错 + return -ENOEXEC（拒绝整个模块）
        "\t} else if (!same_magic(modmagic, vermagic, info->index.vers)) {\n"
        '\t\tpr_err("%s: version magic \'%s\' should be \'%s\'\\n",\n'
        "\t\t       info->name, modmagic, vermagic);\n"
        "\t\treturn -ENOEXEC;\n"
        "\t}\n",
        # 改：降级为调试日志，去掉 return，继续走完后续全部检查
        "\t} else if (!same_magic(modmagic, vermagic, info->index.vers)) {\n"
        "\t\t/* " + MARK + ": vermagic mismatch ignored, allow vendor module */\n"
        '\t\tpr_debug("%s: vermagic mismatch \'%s\' vs \'%s\' (ignored)\\n",\n'
        "\t\t       info->name, modmagic, vermagic);\n"
        "\t}\n",
    ),
]


def static_func_names(src):
    """文件中所有文件作用域的 static 函数名"""
    names = set()
    for m in re.finditer(r"^static\s+(?:[\w\s\*]|\n)+?\b(\w+)\s*\(", src, re.M):
        names.add(m.group(1))
    return names


def unused_static_funcs(src):
    """只出现一次（即仅定义、无任何引用）的静态函数"""
    return {n for n in static_func_names(src) if len(re.findall(r"\b" + re.escape(n) + r"\b", src)) <= 1}


def print_all_regions(src, needle, before=1, after=6):
    """打印所有含 needle 的行及其上下文，便于在 CI 日志中审计改动"""
    lines = src.split("\n")
    last_end = -1
    for i, l in enumerate(lines):
        if needle in l:
            start = max(0, i - before)
            if start <= last_end:  # 与上一处重叠，跳过避免重复打印
                continue
            print("   ---- 第 %d 行附近 ----" % (i + 1))
            for j in range(start, min(len(lines), i + after)):
                print("   %5d | %s" % (j + 1, lines[j]))
            print()
            last_end = i + after


def main():
    try:
        with open(MODFILE, "r", encoding="utf-8", errors="surrogateescape") as f:
            src = f.read()
    except FileNotFoundError:
        print("!! 找不到 %s，请确认在源码根目录执行本脚本" % MODFILE)
        return 1

    if src.count("{") != src.count("}"):
        print("!! 源码花括号数量不平衡，patch 中止以免破坏文件")
        return 1

    before_unused = unused_static_funcs(src)

    print("== 应用引用保留式外科改动 ==")
    failed = False
    for func, old, new in SURGICAL:
        if new in src:
            print("  -- %s 已打过补丁，跳过" % func)
        elif old in src:
            src = src.replace(old, new, 1)
            print("  ++ %s 判定分支已改写（全部函数引用保留）" % func)
        else:
            print("  !! %s 未找到预期原文 —— 请检查内核源码版本是否与锁定的 commit 一致" % func)
            print("     期望找到的原文：")
            for l in old.split("\n"):
                print("       %r" % l)
            failed = True

    if failed:
        print("!! 补丁未能全部应用，中止（不产出可疑补丁）")
        return 1

    if MARK not in src:
        print("!! 补丁未生效，中止写入")
        return 1

    if src.count("{") != src.count("}"):
        print("!! 打补丁后花括号数量不平衡，中止写入")
        return 1

    # ---- 关键校验：补丁不得制造任何无引用的静态函数 ----
    after_unused = unused_static_funcs(src)
    new_unused = after_unused - before_unused
    if new_unused:
        print("!! 补丁使以下静态函数失去全部调用者；由于 CONFIG_CC_WERROR=y，"
              "这会因 -Wunused-function 导致编译失败：")
        for n in sorted(new_unused):
            print("     - %s" % n)
        return 1
    print("== 安全检查通过：补丁没有制造任何无引用的静态函数 ==")

    # ---- 打印改动区域，便于在 CI 日志里审计 ----
    print()
    print_all_regions(src, MARK)

    with open(MODFILE, "w", encoding="utf-8", errors="surrogateescape") as f:
        f.write(src)

    print("== %s 模块兼容补丁完成 ==" % MODFILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
