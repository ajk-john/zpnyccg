# 开发交接（所有 agent 共用）

> 新会话先读本文件；结束会话前更新「当前状态」「进行中 / 待办」，并在「工作日志」末尾追加一条。
> 只写事实和结论，不写任何密钥或密码。原理和操作手册见 `DIY.md`，硬性规则见 `AGENTS.md` 开头。

## 当前状态（2026-11-03）

| 项目 | 值 |
|---|---|
| 上游基线 | tiann/KernelSU `v3.3.1`（已合并，保留完整历史；remote `upstream`） |
| 管理器包名 | `com.cioputlxxcc.knrudvpib`（PR 构建为 `….pr`） |
| Kotlin 命名空间 | `io.qzxnbqm.vktr`（含组件类名，如 `ui.HomeActivity`；JNI 符号、ksud `am start` 同步） |
| 打包库名 | JNI `libfshqpk.so`（原 libkernelsu.so）、守护进程 `libnseqsv.so`（原 libksud.so）；`libadbroot.so` 未改名（仅 root 可见） |
| 应用名 / 图标 | launcher 显示名 **`Toolbox`**（纯色工具箱矢量图标），原 KernelSU 图标已替换 |
| 已移除的管理器特征 | `zygotePreloadName` / `useAppZygote` / `isolatedProcess`（jailbreak 自举，连同 `boot/` 三个类与 jni 辅助函数一起删除）、`ksu://` Manifest 过滤器（内部分发改走显式 Intent）、`hasFragileUserData` |
| 签名证书 | 大小 `0x0315`，SHA-256 `3a0f8ed23ab431cb22bb0dbbca783b6719a2f644303ab5a6a6c37aa8ecf8311e`，公钥在 `diy/manager-cert.pem` |
| GitHub 仓库 | `ajk-john/zpnyccg`，公开（remote `origin`），4 个签名 Secret 已配置 |
| 最近成功构建 | 32697（`5d473d0d`，反检测版），已存到 `builds/32697-5d473d0d/`；APK 文件名为 `Toolbox_*.apk`；32694/32695 为改名版、32697 为反检测版；全部构建见本地 `builds/README.md` |
| 真机验证 | **未做** |

## 已定决策（不要擅自推翻，需要改先问用户）

1. 包名、Kotlin 命名空间 / 组件类名、打包库名全部改成非官方值（用户 2026-11-03 要求彻底隐藏）：命名空间 `io.qzxnbqm.vktr`，JNI 库 `libfshqpk.so`、守护进程库 `libnseqsv.so`。合并上游后用 `python diy/ksu_diy.py set-namespace io.qzxnbqm.vktr` 重新套用改名（映射固化在脚本里）。
2. 编译只在 GitHub Actions 上进行（本机没有 JDK / Android SDK / Rust）；LKM 需要 DDK 容器，也只能在 CI 编。
3. **产物只保存在本地 `builds/`**，GitHub 上不建 Release、不推 tag（用户 2026-11-03 要求）。*Release* 工作流已在 GitHub 上停用。
4. 仓库公开，名称 `zpnyccg`，仓库名和描述不出现 KernelSU 字样（用户希望只有自己能搜到）。
5. 管理器的「检查更新」关闭（CI 不再传 `KSU_UPDATE_REPO`），APK 里不嵌入仓库地址。
6. 不向 `origin` 推送上游 tag（会触发构建且 `setup.sh` 会检出官方包名的版本）。
7. 为规避春秋检测（`gpi` + `intent-profile`）已做隐藏：应用名 `Toolbox`、中性图标、移除 `ksu://` 过滤器与 magica 自举相关 Manifest 特征（用户 2026-11-03 同意）。`diy/ksu_diy.py check` 会拦截这些特征被重新引入；不要回退。

## 本机环境

- Windows 10；agent 可用 Git Bash 和 PowerShell 5.1；Python 3.13（命令 `python`，`python3` 可能是商店占位程序）。
- GitHub CLI：`C:\Users\13160\bin\gh.exe`（在 PATH 中），已登录 `ajk-john`，令牌在系统钥匙串，作用域含 `repo`、`workflow`。
- 本仓库 git 本地配置（换新克隆需要重做）：
  ```bash
  git config core.autocrlf false
  git config core.eol lf
  git config core.hooksPath .githooks
  git config credential.https://github.com.helper ""
  git config --add credential.https://github.com.helper "!'C:/Users/13160/bin/gh.exe' auth git-credential"
  ```
- 签名私钥在 `.diy-secrets/`（gitignore；含密钥库、密码、base64）。CI 用的是同一把，已写入 GitHub Secrets。
- `.githooks/pre-commit`：拒绝提交签名材料，并运行 `diy/ksu_diy.py check`。

## 常用操作

```bash
python diy/ksu_diy.py check                                # 包名/证书四处一致性
python diy/ksu_diy.py set-namespace io.qzxnbqm.vktr        # 合并上游后重新套用命名空间/类名/库名改名
gh workflow run build-manager.yml -R ajk-john/zpnyccg --ref main   # 手动触发构建
gh run list -R ajk-john/zpnyccg --workflow build-manager.yml --limit 5
gh run watch <run-id> -R ajk-john/zpnyccg --exit-status
python diy/fetch_builds.py                                 # 把所有未保存的成功构建下载到 builds/
python diy/ksu_diy.py check --apk builds/<目录>/Toolbox_*.apk
git fetch upstream --tags && git merge vX.Y.Z              # 同步上游，之后跑 check
```

- 推送到 `main` 且改动了 `manager/`、`kernel/`、`userspace/`、相关 workflow 等路径时会自动触发 *Build Manager*（约 17 分钟）。只改文档不会触发。
- Actions 上的产物 90 天后过期，构建成功后要运行 `fetch_builds.py` 才会留在本地。

## 进行中 / 待办

- [ ] 真机验证：卸载官方 KernelSU → 安装 `builds/` 中最新 APK（32694 及以后的改名版；jailbreak 自举已删除） → 修补 boot / init_boot → 刷入 → `dmesg | grep -i kernelsu` 看到新包名和 `Crowning manager`。重点验证 root 授权、模块安装、模块 WebUI 入口。
- [ ] **春秋复测**：安装最新构建后用春秋再扫一遍，预期不再出现 `[gpi,intent-profile]`。若仍命中，下一步候选：替换 dex/资源里的残留品牌字符串（`Theme.KernelSU` 资源名、About 页链接）、检查包可见性/`<queries>`，或配合应用隐藏模块。
- [ ] Dependabot 在 GitHub 上开了 3 个依赖升级 PR（#1 crates、#2 website npm、#3 maven，其中 #3 的构建失败）。待用户决定是否保留 `.github/dependabot.yml`。
- [ ] 剩余可见特征（按需决定是否继续隐藏）：About 页上游 GitHub/Telegram 链接、官方模块仓库/模板/WebUI URL（`modules.kernelsu.org`、`kernelsu.org/templates`、`mui.kernelsu.org`，功能依赖）、资源名 `Theme.KernelSU`（scripts 可见）、启动页仍是旧 logo 动画、SELinux 域 `u:r:ksu:s0`、`/data/adb/ksud` 路径与 `ksud` CLI（仅 root 可见）、`libadbroot.so`（仅 root 可见）、ksud 二进制内字符串。

## 工作日志

- 2026-11-03 · Claude Code：建仓库；合并上游 v3.3.1；三端包名 / 证书参数化默认值改为 DIY；生成签名密钥；修 fork 后 CI 问题（LKM `safe.directory`、Release 权限）；新增 `diy/ksu_diy.py`；推到 GitHub 并配置 Secrets；构建 32684、32685（`com.kdiy.manager`）、32686（`com.cioputlxxcc.knrudvpib`）均成功并核对签名与包名。
- 2026-11-03 · Claude Code：按用户要求删除 GitHub Release 和 tag、停用 Release 工作流、移除 CI 归档；新增 `diy/fetch_builds.py` 本地保存产物；关闭检查更新；建立多 agent 环境（`AGENTS.md` DIY 节、`CLAUDE.md`、`GEMINI.md`、`.github/copilot-instructions.md`、本文件、`.githooks/pre-commit`）。
- 2026-11-03 · Claude Code：构建 32691（`1ec2330e`）成功并用 `fetch_builds.py` 存到本地；确认 APK 里已不含仓库地址。本地 `builds/` 现有 32684、32685、32686、32691。
- 2026-11-03 · DeepSeek Harness：管理器彻底改名——Kotlin 命名空间 `me.weishu.kernelsu`→`io.qzxnbqm.vktr`；Manifest 组件类名映射（App / HomeActivity / WebActivity / FetchService / boot.ZygoteService / boot.BootReceiver / boot.ZygotePreload / IAppInterface，magica 包→boot）；库名 `libkernelsu.so`→`libfshqpk.so`、`libksud.so`→`libnseqsv.so`（JNI 符号、FindClass、ksud `am start`、repack_apk.py、justfile 同步）；扩展 `diy/ksu_diy.py`（新增 `set-namespace`，`check` 增加命名空间/库名一致性校验与 APK dex 残留扫描）；清洗 dex 字符串特征（日志标签、User-Agent、导出文件名）。`check` 通过。已推 main：首轮 CI 因 `ZygotePreload` 与框架接口同名编译失败，改为 `AppPreload` 后构建 32694、32695（`5e9f42cd`）全绿；`check --apk`（含 dex/库名残留扫描）在 CI 与本地均通过，产物存 `builds/32695-5e9f42cd/`，dex 仅剩功能性 `*.kernelsu.org` URL 与 About 页上游链接。
- 2026-11-03 · DeepSeek Harness（反春秋检测，用户报告 `[gpi,intent-profile]` 命中）：应用名改 `Toolbox`、launcher 图标换成中性工具箱矢量图；删除 Manifest 的 `ksu://` 过滤器、`zygotePreloadName`/`useAppZygote`/`isolatedProcess`（jailbreak 自举整体移除：`boot/` 三个类、jni.cc fork 辅助、首页按钮与设置里 BootReceiver 联动）、`hasFragileUserData`；`check` 新增对二进制 Manifest 指纹与 `ksu` scheme 的扫描（CI 的 repack 校验强制）。构建 32697（`5d473d0d`）全绿，`check --apk` 通过；resources.arsc 的 `app_name`=Toolbox，其余 `KernelSU` 字样为应用内文案/资源名（非包信息可见），如春秋仍命中再处理。待用户真机复测。
