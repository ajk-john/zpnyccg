# KernelSU DIY：自定义管理器身份（包名 / 命名空间 / 库名 / 签名）

基于上游 [tiann/KernelSU](https://github.com/tiann/KernelSU) `v3.3.1`：管理器包名从 `me.weishu.kernelsu` 改为 **`com.cioputlxxcc.knrudvpib`**，Kotlin 命名空间与 Manifest 组件类名改为 **`io.qzxnbqm.vktr`**（JNI 符号、ksud `am start` 组件名同步），打包库名改为随机名（JNI `libfshqpk.so`、守护进程 `libnseqsv.so`），并换成自己的签名密钥。

## 原理：为什么不能只改 APK 包名

内核（`kernel/manager/throne_tracker.c`、`apk_sign.c`）只把同时满足下面两个条件的 App 认作管理器：

1. 包名等于编译时的 `KSU_PACKAGE_NAME`；
2. APK **v2 签名**里的第一张证书，DER 长度等于 `KSU_EXPECTED_SIZE`，SHA-256 等于 `KSU_EXPECTED_HASH`。

所以包名、签名证书必须在 **内核/LKM、ksud、管理器** 三端同时一致，任何一端用了官方值，管理器都会显示「未安装 / 不支持」。

## 改动清单

| 位置 | 改动 |
|---|---|
| `kernel/Kbuild` | 默认包名 `com.cioputlxxcc.knrudvpib`（PR 构建 `.pr`），证书 size/hash 换成自己的 |
| `userspace/ksud/build.rs` | ksud 默认包名（`boot-patch` 备份目录、`late-load` 重启管理器等用到），并在环境变量变化时重新生成 |
| `manager/app/build.gradle.kts` | `applicationId` 默认值；`namespace` 改为 `io.qzxnbqm.vktr`；应用名默认 `Toolbox`；新增 `-PKSU_UPDATE_REPO`（检查更新用哪个仓库的 Release，CI 不传，即关闭） |
| `manager/app/src/main/AndroidManifest.xml` | 组件名映射（`diy/ksu_diy.py` 里固化）；移除 `ksu://` 深链过滤器、`zygotePreloadName` / `useAppZygote` / `isolatedProcess`（jailbreak 自举）、`hasFragileUserData`，避免被按包信息/Intent 探测 |
| `manager/app/src/main/res/drawable/ic_launcher_*` | 换成中性的工具箱图标（不再使用官方 logo） |
| `manager/.../ui/util/Downloader.kt` | 检查更新不再指向官方仓库（官方 APK 包名/签名不同，装上也没法用）；未配置仓库时不检查。目前关闭，APK 里不含仓库地址 |
| `.github/workflows/*.yml` | PR 包名；LKM 容器里写死的 `/__w/KernelSU/KernelSU` 改为信任任意路径（否则 fork 后内核版本号退化为 16）；构建后自动校验 APK 签名与内核期望一致；Release 任务加 `contents: write` 权限 |
| `kernel/setup.sh` | 支持 `KSU_REPO_URL` 指向自己的仓库，并固定克隆到 `KernelSU/` 目录 |
| `diy/ksu_diy.py` | 一键改包名 / 换证书 / 改命名空间与库名（`set-namespace`，含组件类名映射）/ 一致性校验（`check` 还会扫描 APK dex 里是否残留官方命名空间）（纯标准库） |
| `diy/manager-cert.pem` | 管理器签名的**公钥证书**（可以公开） |
| `diy/fetch_builds.py` | 把 CI 构建产物保存到本地 `builds/` 并生成版本说明 |
| `AGENTS.md`（开头一节）、`CLAUDE.md`、`GEMINI.md`、`.github/copilot-instructions.md`、`diy/HANDOFF.md`、`.githooks/` | 多 agent 协作环境，见「换 agent 继续开发」 |

Kotlin 命名空间、Manifest 组件类名和打包库名也已改成非官方值（见上表）。合并上游后跑 `python diy/ksu_diy.py set-namespace io.qzxnbqm.vktr` 重新套用改名（命名空间、组件类名、JNI 符号、库名的映射都固化在该脚本里）；`set-package` 不影响命名空间，需要改命名空间时另跑 `set-namespace`。应用显示名默认 `Toolbox`，构建时加 `-PKSU_NAME=xxx` 可改。

管理器的隐藏特征（`ksu://` 过滤器、jailbreak 自举、`hasFragileUserData`）是手工删除的，`python3 diy/ksu_diy.py check` 会拦截它们被重新引入；合并上游后若这些文件/属性重新出现，需要再删一次。

## 签名密钥（重要）

密钥在 `.diy-secrets/`，已加入 `.gitignore`，**不会进仓库**：

| 文件 | 用途 |
|---|---|
| `manager.p12` | 签名密钥库（PKCS12，RSA 2048，别名 `ksu-diy`） |
| `signing.properties` | 密钥库密码 / 别名 |
| `KEYSTORE.base64.txt` | 密钥库的 base64，直接粘贴到 GitHub Secret |
| `manager-cert.pem` / `.der` | 公钥证书 |

**请立刻把 `.diy-secrets/` 备份到安全的地方。** 丢了密钥就签不出内核认可的管理器，只能换证书后重新编译、刷写内核。

> 内核限制：只认 v2 签名（APK 里不能有 v3/v3.1/source stamp 等签名块），证书 DER 不能超过 1024 字节，所以不要用 RSA 4096。

## 用 GitHub Actions 构建（推荐）

本机没有 JDK / Android SDK / Rust 时用这种方式。LKM（`kernelsu.ko`）需要 DDK 容器，只能在 CI 里编。

1. 在 GitHub 新建仓库（建议私有），推送 `main`：
   ```bash
   git remote add origin https://github.com/<你>/<仓库>.git
   git push -u origin main
   ```
   不要 `git push --tags`：上游的 `v*` tag 会触发大量 Release 构建，而且 `setup.sh` 默认检出「最新 tag」，上游 tag 用的是官方包名。
2. 仓库 **Settings → Secrets and variables → Actions** 新建 4 个 Secret：

   | Secret | 值 |
   |---|---|
   | `KEYSTORE` | `.diy-secrets/KEYSTORE.base64.txt` 的全部内容 |
   | `KEYSTORE_PASSWORD` | `signing.properties` 里的 `KEYSTORE_PASSWORD` |
   | `KEY_ALIAS` | `ksu-diy` |
   | `KEY_PASSWORD` | `signing.properties` 里的 `KEY_PASSWORD` |

3. 推送到 `main` 会自动运行 **Build Manager**（也可以在 Actions 页面手动 *Run workflow*）：先为各 KMI 编 LKM → 编 ksud（内嵌 LKM）→ 编管理器 → 用你的密钥重签。
   最后的 *Verify manager matches kernel expectations* 步骤会检查 APK 签名，Secret 填错会直接失败。
4. 构建成功后运行 `python diy/fetch_builds.py` 把产物保存到本地，见下一节。

## 构建产物保存在本地

GitHub 上不放产物：不建 Release、不推 tag（*Release* 工作流已在 GitHub 上停用）。Actions 页面的 Artifacts 只是临时中转，90 天后会被 GitHub 删除，所以每次构建成功后运行：

```bash
python diy/fetch_builds.py
```

它会把所有还没保存过的成功构建下载到 `builds/<构建号>-<提交>/`（已 gitignore，每次约 90 MB）：

- 管理器 APK、16 个 LKM、8 个 ksud、3 个 ksuinit、混淆映射 zip；
- `BUILD_INFO.md`：构建号、版本名、上游基线、提交、构建时间、包名、证书、KMI 列表、每个文件的用途 / 大小 / SHA-256；
- `SHA256SUMS.txt`、`build.json`（同样信息的机器可读版本）。

`builds/README.md` 是全部本地构建的总表。保存前会按构建时那次提交的配置校验 APK 签名，不一致会直接报错。

- 重新保存某一次：`python diy/fetch_builds.py --run <Build Manager 运行 ID>`
- 只重建总表：`python diy/fetch_builds.py --index-only`

## 换 agent 继续开发

本仓库按「所有 agent 共用一份说明」组织，换成 Codex、Cursor、Gemini CLI、Copilot 等都能接着做：

| 文件 | 作用 |
|---|---|
| `AGENTS.md` | 总说明。开头的「DIY fork」一节是硬性规则，其后是上游开发指南。Codex、Cursor、Copilot、Windsurf 等直接读取 |
| `CLAUDE.md` / `GEMINI.md` | Claude Code / Gemini CLI 的入口，只引用 `AGENTS.md` |
| `.github/copilot-instructions.md` | Copilot 入口，指向上面两个文件 |
| `diy/HANDOFF.md` | 交接文档：当前状态、已定决策、本机环境、常用命令、待办、工作日志。**每个 agent 结束前更新** |
| `.githooks/pre-commit` | 提交前拒绝签名材料、检查包名 / 证书一致性，对所有 agent 生效 |

切换时让新 agent 先读 `AGENTS.md` 和 `diy/HANDOFF.md` 即可。在别的位置重新克隆时，按 `diy/HANDOFF.md`「本机环境」里的命令恢复 git 本地配置（钩子、凭据），并把 `.diy-secrets/` 复制过去。

## 刷入与使用

先**卸载/还原官方 KernelSU**（官方内核/LKM 只认官方管理器），再安装本仓库构建的管理器。

- **LKM 模式（GKI 2.0，android12-5.10 及以上）**：打开管理器 → 安装 → 选择 `boot.img` / `init_boot.img` → 修补 → `fastboot flash` 刷入。管理器内嵌的 LKM 就是按你的包名和证书编的。
- **把 KernelSU 编进内核（GKI 1.0 / 非 GKI）**：在内核源码根目录执行
  ```bash
  curl -LSs "https://raw.githubusercontent.com/<你>/<仓库>/main/kernel/setup.sh" | KSU_REPO_URL=https://github.com/<你>/<仓库> bash -s main
  ```
  末尾的 `main`（或你自己的 tag）不能省。私有仓库请先手动 `git clone` 到内核根目录的 `KernelSU/` 再执行 `setup.sh`。也可以不改源码，在 make 时传 `KSU_PACKAGE_NAME=… KSU_EXPECTED_SIZE=… KSU_EXPECTED_HASH=…`。
- **验证**：`adb shell dmesg | grep -i kernelsu` 应看到 `package name com.cioputlxxcc.knrudvpib` 和 `Crowning manager`；管理器首页显示「工作中」。

## 改包名 / 换密钥

```bash
python diy/ksu_diy.py set-package com.example.newname   # 同步改内核、ksud、管理器、CI
python diy/ksu_diy.py set-cert path/to/cert.pem         # 也可以直接传一个已签名的 APK
python diy/ksu_diy.py check                             # 校验所有位置一致
python diy/ksu_diy.py check --apk 某个管理器.apk          # 校验这个 APK 能否被内核认可
```

改完后内核/LKM 和管理器都要重新构建，设备上要重新刷内核或重新修补 boot。

## 本地构建（可选）

需要 JDK 21、Android SDK（含 NDK r29）、Rust + `cargo-ndk`，步骤见 `AGENTS.md`。签名参数加到 `~/.gradle/gradle.properties`（`KEYSTORE_FILE` 写 `manager.p12` 的绝对路径）。Gradle 直接产出的 APK 不保证只有 v2 签名，要用 `python repack_apk.py repack -b release -t release -a arm64-v8a -K .diy-secrets/manager.p12 -A ksu-diy -P <密码> -S <密码>` 重签（输出到 `dist/`），再用 `check --apk` 验证。

## 同步上游

```bash
git fetch upstream --tags
git merge v3.3.2            # 或 upstream/main
python diy/ksu_diy.py check
```

冲突一般只会出现在上面「改动清单」里的几个文件。
