# KernelSU DIY：自定义管理器包名

基于上游 [tiann/KernelSU](https://github.com/tiann/KernelSU) `v3.3.1`，管理器包名从 `me.weishu.kernelsu` 改为 **`com.cioputlxxcc.knrudvpib`**，并换成自己的签名密钥。

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
| `manager/app/build.gradle.kts` | `applicationId` 默认值；新增 `-PKSU_UPDATE_REPO`（检查更新用哪个仓库的 Release） |
| `manager/.../ui/util/Downloader.kt` | 检查更新不再指向官方仓库（官方 APK 包名/签名不同，装上也没法用）；未配置仓库时不检查 |
| `.github/workflows/*.yml` | PR 包名；LKM 容器里写死的 `/__w/KernelSU/KernelSU` 改为信任任意路径（否则 fork 后内核版本号退化为 16）；构建后自动校验 APK 签名与内核期望一致；Release 任务加 `contents: write` 权限 |
| `kernel/setup.sh` | 支持 `KSU_REPO_URL` 指向自己的仓库，并固定克隆到 `KernelSU/` 目录 |
| `diy/ksu_diy.py` | 一键改包名 / 换证书 / 一致性校验（纯标准库） |
| `diy/manager-cert.pem` | 管理器签名的**公钥证书**（可以公开） |
| `.github/workflows/archive-build.yml`、`BUILDS.md` | 每次构建的产物永久归档到 Release，并登记版本信息 |

Kotlin 源码的命名空间（类名 `me.weishu.kernelsu.*`）保持不变，只改了 Android 识别的包名 `applicationId`，这样能和上游干净合并。若想连应用名也改，构建时加 `-PKSU_NAME=xxx`。

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
4. 构建成功后 **Archive Build** 自动把产物永久归档到 Release，见下一节。管理器里的「检查更新」读的就是这个仓库最新的 Release。

## 构建产物归档

Actions 页面的 Artifacts 90 天后会被 GitHub 删除，所以每次 `main` 构建成功后，`.github/workflows/archive-build.yml` 会：

1. 下载这次构建的全部产物，用构建时那个提交的配置再校验一遍 APK 签名；
2. 创建 Release，tag 为 `v<上游版本>-diy.<构建号>`（例如 `v3.3.1-diy.32686`），附上管理器 APK、全部 LKM、ksud、ksuinit、混淆映射、`SHA256SUMS.txt` 和 `BUILD_INFO.md`（版本信息）；
3. 在 [BUILDS.md](BUILDS.md) 的表格里登记一行（构建号、时间、提交、上游基线、包名、证书、Release 链接），并由 CI 提交到 `main`。

所以本地再提交前要先 `git pull`。归档 tag 打在 DIY 提交上，之后的构建版本名会变成 `v3.3.1-diy.32686-2-gxxxxxxx` 这种形式，`setup.sh` 不传参数时也会检出最新的归档 tag（包名正确）。

- **补归档 / 重新归档**：Actions → *Archive Build* → *Run workflow*，填 Build Manager 那次运行的 ID（运行页面 URL 末尾的数字），产物需未过期。同一构建重复归档会覆盖同一个 Release 和同一行记录。
- **下载到本地**：
  ```bash
  gh release download v3.3.1-diy.32686 -R ajk-john/zpnyccg -D builds/v3.3.1-diy.32686
  ```

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
