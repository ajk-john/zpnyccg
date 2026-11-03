#!/usr/bin/env python3
"""Save CI builds locally (builds/ is git-ignored; nothing is published on GitHub).

GitHub deletes Actions artifacts after 90 days, so run this after every CI build.
Each successful Build Manager run on main is saved to builds/<versionCode>-<sha8>/
with renamed artifacts, SHA256SUMS.txt, build.json and BUILD_INFO.md, and
builds/README.md indexes every local build.

  python diy/fetch_builds.py              # save all builds that are not local yet
  python diy/fetch_builds.py --run 123    # (re)save one Build Manager run
  python diy/fetch_builds.py --index-only # only regenerate builds/README.md

Requires an authenticated GitHub CLI (`gh`); set GH=/path/to/gh if it is not on PATH.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ksu_diy  # noqa: E402

ROOT = ksu_diy.ROOT
BUILDS = ROOT / "builds"
WORKFLOW = "build-manager.yml"
UTC8 = timezone(timedelta(hours=8))


def run(*args, cwd=ROOT) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout


def gh(*args) -> str:
    return run(os.environ.get("GH") or shutil.which("gh") or "gh", *args)


def git(*args) -> str:
    return run("git", *args)


def origin_repo() -> str:
    url = git("remote", "get-url", "origin").strip()
    m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
    if not m:
        raise SystemExit(f"error: origin is not a GitHub repo: {url}")
    return m.group(1)


def utc8(ts: str) -> str:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC8).strftime("%Y-%m-%d %H:%M")


def config_at(sha: str) -> dict:
    """Package name and certificate as configured in the build commit (kernel/Kbuild wins)."""
    found = {}
    for path, regex, groups in ksu_diy.LOCATIONS:
        try:
            text = git("show", f"{sha}:{path.relative_to(ROOT).as_posix()}")
        except RuntimeError:
            continue
        m = regex.search(text)
        if m:
            for group, meaning in groups.items():
                found.setdefault(meaning, m.group(group))
    return found


def upstream_base(sha: str) -> str:
    tags = git("tag", "--merged", sha, "--list", "v*", "--sort=-v:refname").split()
    tags = [t for t in tags if "diy" not in t]
    return tags[0] if tags else "unknown"


def describe(name: str) -> str:
    if name.endswith(".apk"):
        return "管理器 APK（DIY 密钥签名，内嵌 ksud 和全部 LKM）"
    m = re.fullmatch(r"lkm-(aarch64|x86_64)-(.+)_kernelsu\.ko", name)
    if m:
        arch = "arm64 真机" if m.group(1) == "aarch64" else "x86_64 模拟器"
        return f"内核模块 LKM，KMI {m.group(2)}（{arch}）"
    if name.startswith("ksud-"):
        if "linux-android" in name:
            return "设备端 ksud（APK 已内嵌）"
        if "windows" in name:
            return "电脑端 ksud（Windows），可在电脑上修补 boot"
        if "apple-darwin" in name:
            return "电脑端 ksud（macOS）"
        return "电脑端 ksud（Linux）"
    if name.startswith("ksuinit-"):
        return "ksuinit（ksud 已内嵌）"
    if name.startswith("mappings-"):
        return "R8 混淆映射，用于还原管理器崩溃堆栈"
    return ""


def file_order(name: str):
    for i, prefix in enumerate(("KernelSU", "lkm-aarch64", "lkm-x86_64", "ksud-", "ksuinit-", "mappings-")):
        if name.startswith(prefix):
            return (i, name)
    return (9, name)


def human_size(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.0f} KB"


def stage(artifacts: Path, out: Path, code: str) -> None:
    """Copy CI artifacts into out/ with release-style names."""
    for d in sorted(p for p in artifacts.iterdir() if p.is_dir()):
        name = d.name
        if name == "manager":
            for apk in d.glob("*.apk"):
                shutil.copy2(apk, out / apk.name)
        elif name.endswith("-lkm"):
            for ko in d.glob("*_kernelsu.ko"):
                shutil.copy2(ko, out / f"lkm-{name[:-4]}_kernelsu.ko")
        elif name.startswith(("ksud-", "ksuinit-")):
            # ksud-extra also uploads cargo's ksud.d dep-info file; keep only the binaries
            for binary in ("ksud", "ksud.exe", "ksuinit"):
                src = d / binary
                if src.is_file():
                    shutil.copy2(src, out / (name + (".exe" if binary.endswith(".exe") else "")))
        elif name == "mappings":
            with zipfile.ZipFile(out / f"mappings-{code}.zip", "w", zipfile.ZIP_DEFLATED) as z:
                for f in sorted(d.rglob("*")):
                    if f.is_file():
                        z.write(f, f.relative_to(d).as_posix())
        # manager-gradle is the unsigned intermediate APK; not kept


def write_info(out: Path, meta: dict) -> None:
    sums = {}
    for f in sorted(out.iterdir(), key=lambda p: file_order(p.name)):
        if f.is_file() and f.name not in ("SHA256SUMS.txt", "BUILD_INFO.md", "build.json"):
            sums[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
    (out / "SHA256SUMS.txt").write_bytes("".join(f"{h}  {n}\n" for n, h in sums.items()).encode())
    meta["files"] = {n: {"sha256": h, "size": (out / n).stat().st_size} for n, h in sums.items()}
    (out / "build.json").write_bytes(json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))

    event = {"push": "推送到 main", "workflow_dispatch": "手动触发"}.get(meta["event"], meta["event"])
    lines = [
        f"# 构建 {meta['version_code']}",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 构建号（KernelSU 版本号） | `{meta['version_code']}` |",
        f"| 管理器版本名 | `{meta['version_name']}` |",
        f"| 上游基线 | tiann/KernelSU `{meta['upstream']}` |",
        f"| 提交 | `{meta['commit'][:8]}` {meta['subject']} |",
        f"| 提交时间 | {utc8(meta['committed'])} (UTC+8) |",
        f"| 构建时间 | {utc8(meta['built'])} (UTC+8) |",
        f"| 构建来源 | {event}，Actions run `{meta['run_id']}` |",
        f"| 管理器包名 | `{meta['package']}` |",
        f"| 签名证书 | 大小 `{meta['cert_size']}`，SHA-256 `{meta['cert_hash']}` |",
        f"| LKM 覆盖的 KMI | {', '.join(meta['kmis'])} |",
        "",
        "## 文件",
        "",
        "| 文件 | 说明 | 大小 | SHA-256 |",
        "|---|---|---|---|",
    ]
    for name, f in meta["files"].items():
        lines.append(f"| `{name}` | {describe(name)} | {human_size(f['size'])} | `{f['sha256']}` |")
    lines += [
        "",
        "校验：在本目录执行 `sha256sum -c SHA256SUMS.txt`。",
        "",
        f"内核 / LKM 只认包名为 `{meta['package']}` 且用上述证书签名的管理器；管理器和内核要用同一次构建的产物。",
        "",
    ]
    (out / "BUILD_INFO.md").write_bytes("\n".join(lines).encode("utf-8"))


def write_index() -> None:
    builds = []
    for meta_file in BUILDS.glob("*/build.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta["dir"] = meta_file.parent.name
        builds.append(meta)
    builds.sort(key=lambda m: (int(m["version_code"]), m["built"]), reverse=True)

    lines = [
        "# 本地构建产物",
        "",
        "由 `python diy/fetch_builds.py` 生成，请勿手动编辑。本目录被 git 忽略，只保存在本机。",
        "",
        "- **构建号**：KernelSU 版本号（30000 + 提交数），管理器首页和内核日志里显示的就是它。",
        "- 每个目录里的 `BUILD_INFO.md` 有完整版本信息和每个文件的 SHA-256。",
        "- **管理器和内核要配套**：内核 / LKM 只认同一行的包名和签名证书。",
        "",
        "| 构建号 | 构建时间 (UTC+8) | 提交 | 上游基线 | 管理器包名 | 签名证书 | 目录 |",
        "|---|---|---|---|---|---|---|",
    ]
    for m in builds:
        subject = m["subject"].replace("|", "\\|")
        lines.append(
            f"| {m['version_code']} | {utc8(m['built'])} | `{m['commit'][:8]}` {subject} | {m['upstream']} "
            f"| `{m['package']}` | `{m['cert_size']}` `{m['cert_hash'][:12]}…` | [{m['dir']}]({m['dir']}/BUILD_INFO.md) |"
        )
    if not builds:
        lines.append("| （暂无） | | | | | | |")
    BUILDS.mkdir(exist_ok=True)
    (BUILDS / "README.md").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def save_run(repo: str, run_id: str, force: bool) -> bool:
    info = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if info["name"] != "Build Manager" or info["conclusion"] != "success":
        print(f"skip run {run_id}: {info['name']} ({info['conclusion']})")
        return False
    sha = info["head_sha"]

    arts = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"))["artifacts"]
    if not arts or any(a["expired"] for a in arts):
        print(f"skip run {run_id}: artifacts expired")
        return False

    try:
        git("cat-file", "-e", f"{sha}^{{commit}}")
    except RuntimeError:
        git("fetch", "--quiet", "origin", sha)

    with tempfile.TemporaryDirectory(dir=BUILDS) as tmp:
        tmp = Path(tmp)
        gh("run", "download", run_id, "-R", repo, "-D", str(tmp / "artifacts"))
        apks = list((tmp / "artifacts" / "manager").glob("*.apk"))
        if len(apks) != 1:
            raise SystemExit(f"error: run {run_id}: expected one manager APK, found {len(apks)}")
        # Manager APK is named <app>_<versionName>_<versionCode>-release.apk
        stem = apks[0].name[: -len("-release.apk")]
        version_name, code = stem.split("_", 1)[1].rsplit("_", 1)

        out = BUILDS / f"{code}-{sha[:8]}"
        if out.exists() and not force:
            print(f"skip run {run_id}: {out.name} already saved")
            return False

        cfg = config_at(sha)
        der, problems = ksu_diy.read_apk_cert(apks[0])
        cert_size, cert_hash = ksu_diy.describe_cert(der)
        expected = (int(cfg["size"], 0), cfg["hash"])
        if problems or (cert_size, cert_hash) != expected:
            raise SystemExit(f"error: run {run_id}: APK signature does not match kernel config {problems}")

        staged = tmp / "staged"
        staged.mkdir()
        stage(tmp / "artifacts", staged, code)
        kmis = sorted(m.group(1) for f in staged.iterdir() if (m := re.fullmatch(r"lkm-aarch64-(.+)_kernelsu\.ko", f.name)))
        meta = {
            "version_code": code,
            "version_name": version_name,
            "upstream": upstream_base(sha),
            "commit": sha,
            "subject": git("show", "-s", "--format=%s", sha).strip(),
            "committed": git("show", "-s", "--format=%cI", sha).strip(),
            "built": info["created_at"],
            "event": info["event"],
            "run_id": str(run_id),
            "package": cfg["package"],
            "cert_size": f"0x{cert_size:04x}",
            "cert_hash": cert_hash,
            "kmis": kmis,
        }
        write_info(staged, meta)
        if out.exists():
            shutil.rmtree(out)
        shutil.move(str(staged), str(out))
    print(f"saved run {run_id} -> builds/{out.name} ({cfg['package']})")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", help="Build Manager run ID to (re)save")
    parser.add_argument("--index-only", action="store_true", help="only regenerate builds/README.md")
    args = parser.parse_args()

    BUILDS.mkdir(exist_ok=True)
    if not args.index_only:
        repo = origin_repo()
        if args.run:
            save_run(repo, args.run, force=True)
        else:
            saved_runs = {
                json.loads(p.read_text(encoding="utf-8"))["run_id"] for p in BUILDS.glob("*/build.json")
            }
            runs = json.loads(
                gh("api", f"repos/{repo}/actions/workflows/{WORKFLOW}/runs?branch=main&status=success&per_page=100")
            )["workflow_runs"]
            for r in sorted(runs, key=lambda r: r["created_at"]):
                if r["event"] != "pull_request" and str(r["id"]) not in saved_runs:
                    save_run(repo, str(r["id"]), force=False)
    write_index()
    print(f"index: {(BUILDS / 'README.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
