# 红米 K40 (alioth) Docker 内核 · 云端编译工程

在 GitHub Actions 上编译一棵**支持 Docker 的 alioth 内核**，产出可直接刷入 `boot_a` 的 `boot.img`。

---

## 0. 这个工程在做什么

| 项目 | 内容 |
|---|---|
| 编译目标 | Redmi K40 / POCO F3 / Mi 11X（`alioth`），Android 13 / Non-GKI |
| 内核源码 | `zfdx123/kernel_xiaomi_alioth`（Linux 4.19.325，CLO `LA.UM.9.12.r1-18500-SMxx50.QSSI14.0`，2026-06 仍在维护）<br>**已锁定 commit `8b19a1dd26ae283a2bfc33781905255339aae485`**（原因见 ②） |
| Root | **源码树已内置 ReSukiSU manual-hook**（`CONFIG_KSU_MANUAL_HOOK=y`），不需要再单独集成 KernelSU |
| 要补的能力 | 原厂内核缺失 `CONFIG_PID_NS` / `CONFIG_CGROUP_DEVICE` 等 9 项 → 补齐后 `dockerd` 才能跑起来 |
| 工具链 | Ubuntu 22.04 + clang-14（与 Android 13 内核所用的 clang-r450784d「clang 14.0.7」同主版本） |
| 产出 | `Image` / `Image.gz` / `boot.img` / 全部 `.ko` / 合并后的 `.config` |

### 为什么需要两个额外步骤

**① 补 Docker 配置** → `config/docker-kernel.fragment`

原厂内核实测关闭了这些（详见另一份《内核实测结论》文档）：

```config
CONFIG_PID_NS=y            # 致命
CONFIG_CGROUP_DEVICE=y     # 致命
CONFIG_USER_NS=y
CONFIG_CGROUP_PIDS=y
CONFIG_IPC_NS=y
CONFIG_NETFILTER_XT_MATCH_ADDRTYPE=y
CONFIG_BRIDGE_NETFILTER=y
CONFIG_SYSVIPC=y
CONFIG_POSIX_MQUEUE=y
```

**② 打模块兼容补丁** → `scripts/patch-module-compat.py`

手机 ROM 里 `/vendor/lib/modules/` 有 **42 个原厂模块**（WiFi / 音频 / 指纹），它们的 vermagic 是：

```
4.19.157-perf-g92c089fc2d37 SMP preempt mod_unload modversions aarch64
```

而我们要编的是 **4.19.325**，release 串必然不同 → vermagic 不匹配；原厂还开了 `CONFIG_MODVERSIONS=y`，符号 CRC 也几乎不可能一致 → **模块会全部加载失败**。

补丁只做两处**外科式**改动，函数体完整保留：

| 位置 | 改动 |
|---|---|
| `check_version()` 的 `bad_version:` 处 | `return 0` → `return 1`（CRC 不匹配不再拒绝） |
| `check_modinfo()` 的 vermagic 不匹配分支 | 去掉 `return -ENOEXEC`，只降级记日志后继续 |

**为什么不能"清空函数体"**：清空后，原调用点只在这两个函数内的三个静态函数会失去全部调用者 —— `check_modinfo_retpoline()`、`set_license()`、`resolve_rel_crc()`。而本内核树 Makefile：

```
KBUILD_CFLAGS := -Wall ...                                   (455 行，-Wall 含 -Wunused-function)
KBUILD_CFLAGS += cc-disable-warning unused-but-set-variable  (780 行)
KBUILD_CFLAGS += cc-disable-warning unused-const-variable    (782 行)
```

只关掉了 `unused-but-set-variable` / `unused-const-variable`，**没有关 `unused-function`**；而 `alioth_defconfig`（795 行）有 `CONFIG_CC_WERROR=y` 会加 `-Werror` ⇒ *defined but not used* 直接变成编译错误。外科改动让所有引用原样留存，从根上避开这个坑，同时 license/retpoline/out-of-tree taint 等原有行为也全部保持不变。

脚本会自我校验：两处改动必须命中、补丁不得制造任何无引用的静态函数、花括号必须平衡 —— 任一不满足即以非 0 退出，让 CI 在 2 分钟内失败，而不是产出可疑的补丁。

> 因此**内核树必须锁 commit**：该仓库 master 仍在维护，而外科改动依赖精确文本。工作流默认使用已验证的 `8b19a1dd…`，可用 `kernel_ref` 输入覆盖。

> ⚠️ 这个补丁只能保证「能加载」。若结构体布局有差异仍可能异常。彻底解法见第 6 节「策略一」。

---

## 1. 一次性准备：建仓库

需要一个 GitHub 账号。

**方式 A（最简单，网页上传）**
1. 在 github.com 新建一个仓库（Private 也行，Actions 对私有仓库有免费额度；建议 Public 以免额度限制）
2. 进入仓库 → `Add file` → `Upload files`
3. 把 **本目录 `ci-build/` 里的全部内容**（含隐藏的 `.github` 目录）拖进去
4. Commit

> ⚠️ `.github/workflows/` 必须在仓库根目录下，不能多套一层。

**方式 B（命令行，推荐）**

本目录**已经是一个初始化好的 git 仓库并完成了首次提交**，你只需要指定远程并推送：

```bash
cd ci-build

# 1) 设置提交身份（改成你自己的，否则提交不会归属到你的 GitHub 账号）
git config user.name  "你的名字"
git config user.email "你的邮箱"
git commit --amend --reset-author --no-edit

# 2) 关联远程（把 URL 换成你刚建的仓库）
git remote add origin https://github.com/<你的用户名>/<仓库名>.git

# 3) 推送（会弹出 GitHub 认证窗口，登录授权即可）
git push -u origin main
```

> 若第 3 步提示 `could not read Username`，说明本机没有可用的凭据助手。
> 用 Personal Access Token（Settings → Developer settings → Personal access tokens，勾 `repo` 权限）：
> `git remote set-url origin https://<用户名>:<TOKEN>@github.com/<用户名>/<仓库名>.git` 后再 push。

**首次启用 Actions**：进仓库的 `Actions` 标签页，如果顶部有提示按钮，点一下启用工作流。

---

## 2. 触发编译

`Actions` → 左侧选 `Build alioth kernel (Docker-enabled)` → 右侧 `Run workflow`：

| 输入项 | 填什么 |
|---|---|
| `defconfig` | 留空即可（默认 `alioth_defconfig`） |
| `repack` | 留空（默认 `auto`，没有 `stock_boot.img` 时会自动跳过重打包） |
| `kernel_ref` | **留空**（使用已验证锁定的 commit；仅在需要换内核版本时填写其它 commit） |

耗时约 **40–90 分钟**。

### 中途的配置校验闸门（重要）

工作流在第 8 步会**逐项检查关键配置**，缺任何一项就**直接中止**，不会白等一小时：

```
===== 致命项（Docker 必需）=====
  [OK]   CONFIG_PID_NS
  [OK]   CONFIG_CGROUP_DEVICE
  ...
全部关键配置校验通过 ✅
```

看到 `[FAIL]` 就是片段没合并进去，把日志发我。

---

## 3. 下载产物

构建成功后，在本次 Run 页面底部 `Artifacts` 下载 **`alioth-docker-kernel`**：

```
Image               ← 内核本体（重打包 boot.img 时用这个）
Image.gz            ← 压缩版（如果生成）
boot.img            ← 可直接刷入（前提：仓库里有 stock_boot.img）
kernel.config       ← 合并后的完整配置，可作为编译证据留存
modules/*.ko        ← 编译出的内核模块（策略一要用）
```

---

## 4. 生成可刷入的 boot.img

仓库里**没有** `stock_boot.img` 时，构建只产出 `Image`。拿到可直接 `fastboot flash` 的 `boot.img` 有三种做法。

### 做法一：独立重打包工作流（推荐 —— 不重新编译）

`Actions` → 左侧 `Repack boot.img (from an existing build)` → `Run workflow`：

| 输入项 | 填什么 |
|---|---|
| `run_id` | 上一步 `Build alioth kernel` 那次**成功** Run 的 ID（Run 页面 URL 末尾的数字，如 `.../actions/runs/1234567890`） |
| `base` | 留空（默认 `stock_boot.img`） |

前提：仓库根目录已有 `stock_boot.img`。耗时约 **2 分钟**，产物名 `alioth-boot-img`。

> 这个工作流通过 `run-id` 直接复用上一次 Run 的 artifact，**因此不需要重新编译内核**（省掉约 1 小时）。

### 做法二：构建时自动重打包

把原厂 `boot.img` 放进仓库根目录并命名为 **`stock_boot.img`**，然后**重新触发** `Build alioth kernel`（`repack` 选 `auto`）。工作流最后一步会自动 unpack / 替换 kernel / repack。

缺点：要连带重编一遍内核。

### 做法三：本地重打包

把 `Image` 和原厂 `boot.img` 放一起，用 Magisk 的 `magiskboot` 手工执行 unpack → 替换 `kernel` → repack。

> 三种做法产出的 boot.img 都**完整保留原厂分区头、ramdisk、cmdline、dtb 配置，只替换内核段**。
>
> 原厂 `boot.img` 从哪来？线刷包 `alioth_images_OS1.0.10.0.TKHCNXM_*.tgz` 解开的 `images/boot.img`。
> 仓库单文件上限 **100 MB**，boot.img 通常 64–96 MB，可以直接提交。

---

## 5. 刷入

**完整兜底方案与刷机命令见另一份文档《红米K40-刷机兜底方案与执行清单》。核心三步：**

```bash
adb reboot bootloader
fastboot getvar current-slot   # 必须输出 b
fastboot flash boot_a boot.img # 注意是 a，不是 b
fastboot reboot
```

刷完**手机仍从 B 槽正常启动**，你可以先放着不切，确认一切正常后再：

```bash
fastboot set_active a && fastboot reboot
```

**出任何问题，回滚就是一条命令：**

```bash
fastboot set_active b && fastboot reboot
```

---

## 6. 刷入之后：让功能完整（策略一）

如果刷完发现 **没有 WiFi / 没有声音 / 指纹失效**，说明原厂模块虽然能加载但存在兼容问题。彻底解法是用**同源编译的模块**替换掉原厂的：

1. 先确认 Root 可用（ReSukiSU 需要装 **ReSukiSU 管理器 App**）
2. 用 CI 产出的 `modules/*.ko` 做一个 **Magisk 模块**，把 `/vendor/lib/modules` 里的对应文件覆盖掉：

```
your-magisk-module/
├── module.prop
└── system/vendor/lib/modules/      ← 放 CI 产出的 .ko
```

3. 手机重启后，内核与模块完全同源，不存在任何兼容性问题

> 顺序很重要：**必须先有 Root 才能覆盖 `/vendor`**，而 Root 又要先刷内核。所以第一步先用补丁版内核把系统跑起来。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 校验闸门报 `[FAIL]` | 片段没合并成功 | 检查 `ci/config/docker-kernel.fragment` 路径是否对上 |
| 校验闸门报 `[FAIL]`，但片段看着没问题 | 片段是 CRLF 换行，被 Kconfig 当成非法值 | 仓库已带 `.gitattributes` 强制 LF；确认 push 前没被本地 `core.autocrlf` 转回 CRLF |
| 日志里出现 `$'\r': command not found` | 同上，工作流脚本行尾带了 CR | 同上 |
| 第 5 步报「未找到预期原文」 | 内核树 commit 与补丁预期不符 | 检查 `kernel_ref` 是否被改过；留空即用已验证 commit |
| 第 5 步报「补丁使以下静态函数失去全部调用者」 | 补丁改法退化成了清空函数体 | 不该发生；若出现请把日志发我 |
| 编译报 `defined but not used` / `unused-function` 且 `-Werror` | 同上，补丁方式退回旧版 | 同上 |
| 编译报 `unused variable` / `unreachable code` | 补丁没生效 | 看第 5 步日志有没有打印改动区域 |
| `clang: command not found` | 软链没建上 | 检查第 3 步日志里 `clang --version` 的输出 |
| 找不到 `Image` | 编译失败 | 往上翻找到第一个 `Error` 行 |
| 刷入后卡 Logo | 内核与系统不匹配 | `fastboot set_active b` 回滚 |
| 能开机但没 WiFi | 模块兼容问题 | 按第 6 节做策略一 |
| 重打包工作流报 `找不到 artifact` | `run_id` 填错，或那次 Run 失败/产物已过期（30 天） | 用最近一次成功的 `Build alioth kernel` 的 run_id |
| 重打包工作流报 `Image not found` | 那次 Run 没产出 Image | 换一个成功的 run_id |

---

## 8. 文件清单

```
ci-build/
├── .github/workflows/
│   ├── build-alioth-kernel.yml                 编译内核（12 步，含校验闸门）
│   └── repack-bootimg.yml                      复用已有产物重打包 boot.img（8 步，约 2 分钟）
├── .gitattributes                              强制 LF，防止 CRLF 污染 shell / Kconfig
├── config/docker-kernel.fragment               要补的 Docker 内核配置
├── scripts/patch-module-compat.py              模块兼容补丁（已本地验证幂等 + 括号配对）
└── README.md                                   本文件
```

可选：`stock_boot.img`（原厂 boot 镜像，放进仓库根目录以启用重打包）
