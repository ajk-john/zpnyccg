#!/usr/bin/env python3
"""KernelSU DIY helper: keep manager package name, signing certificate,
Kotlin namespace and packaged library names in sync.

The kernel only accepts a manager whose package name equals KSU_PACKAGE_NAME and
whose APK v2 signing certificate matches KSU_EXPECTED_SIZE / KSU_EXPECTED_HASH
(see kernel/manager/apk_sign.c). These values are duplicated across the kernel,
ksud, the manager build and CI, so this tool rewrites and verifies all of them.

The official Kotlin namespace (me.weishu.kernelsu) and the packaged library
names (libkernelsu.so / libksud.so) are additionally renamed so that the
manager APK carries no upstream KernelSU identifiers. `set-namespace` performs
that rename and can be re-run after merging upstream code:

  python3 diy/ksu_diy.py check [--apk app.apk ...]
  python3 diy/ksu_diy.py cert-info <cert.pem|cert.der|app.apk> ...
  python3 diy/ksu_diy.py set-package com.example.manager
  python3 diy/ksu_diy.py set-cert path/to/cert.pem
  python3 diy/ksu_diy.py set-namespace io.example.abcde
"""

import argparse
import base64
import hashlib
import re
import shutil
import struct
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_FILE = ROOT / "diy" / "manager-cert.pem"

KBUILD = ROOT / "kernel" / "Kbuild"
KSUD_BUILD_RS = ROOT / "userspace" / "ksud" / "build.rs"
MANAGER_GRADLE = ROOT / "manager" / "app" / "build.gradle.kts"
KSUD_WORKFLOW = ROOT / ".github" / "workflows" / "ksud.yml"

# --- namespace / class / library rename (DIY hiding) -------------------------

OFFICIAL_NAMESPACE = "me.weishu.kernelsu"

MANAGER_JAVA = ROOT / "manager" / "app" / "src" / "main" / "java"
MANAGER_TEST = ROOT / "manager" / "app" / "src" / "test" / "java"
MANAGER_AIDL = ROOT / "manager" / "app" / "src" / "main" / "aidl"
MANAGER_CPP = ROOT / "manager" / "app" / "src" / "main" / "cpp"
MANAGER_MANIFEST = ROOT / "manager" / "app" / "src" / "main" / "AndroidManifest.xml"
JNI_CC = MANAGER_CPP / "jni.cc"
CMAKE = MANAGER_CPP / "CMakeLists.txt"
LATE_LOAD_RS = ROOT / "userspace" / "ksud" / "src" / "late_load.rs"
PROGUARD = ROOT / "manager" / "app" / "proguard-rules.pro"
REPACK = ROOT / "repack_apk.py"
JUSTFILE = ROOT / "justfile"
REPACK_CONFIG_EXAMPLE = ROOT / "repack-config.example.json"

# Packaged library names (upstream: libkernelsu.so JNI glue, libksud.so daemon).
JNI_LIB = "libfshqpk.so"      # was libkernelsu.so
JNI_LIB_NAME = JNI_LIB[3:-3]  # CMake target and System.loadLibrary argument
DAEMON_LIB = "libnseqsv.so"   # was libksud.so

# Identifier renames applied to manager sources after the namespace swap.
# Order matters: longer / more specific names first.
IDENTIFIER_RENAMES = [
    ("KernelSUMagica", "AppBoot"),
    ("KernelSUApplication", "App"),
    ("BootCompletedReceiver", "BootReceiver"),
    ("AppZygotePreload", "AppPreload"),
    ("MagicaService", "ZygoteService"),
    ("WebUIActivity", "WebActivity"),
    ("DownloadService", "FetchService"),
    ("MainActivity", "HomeActivity"),
    ("IKsuInterface", "IAppInterface"),
    ("fork_dont_care_and_exec_ksud", "fork_dont_care_and_exec_daemon"),
    ("forkDontCareAndExecKsud", "forkDontCareAndExecDaemon"),
]

# Brand string literals scrubbed from Java/Kotlin/C++ sources only (dex/.so
# strings). Resources (app label etc.) are left untouched on purpose.
LITERAL_RENAMES = [
    ('"KernelSU', '"Manager'),
    ('"KsuCli"', '"AppCli"'),
    ('"KsuService"', '"AppService"'),
    ("KsuService disconnected", "App service disconnected"),
    ("ksu_size.txt", "app_size.txt"),
    ('"ksu failed: "', '"daemon failed: "'),
]

RENAME_ROOTS = [
    ROOT / "manager",
    LATE_LOAD_RS,
    REPACK,
    JUSTFILE,
    REPACK_CONFIG_EXAMPLE,
]
LEFTOVER_ROOTS = [
    ROOT / "manager",
    ROOT / "userspace",
    ROOT / "kernel",
    ROOT / "scripts",
    ROOT / ".github",
    REPACK,
    JUSTFILE,
]
LEFTOVER_TOKENS = [
    "me.weishu.kernelsu",
    "me/weishu/kernelsu",
    "me_weishu_kernelsu",
    "libkernelsu.so",
    "libksud.so",
]

TEXT_SUFFIXES = {
    ".kt", ".java", ".kts", ".xml", ".aidl", ".pro", ".json", ".toml",
    ".md", ".yml", ".yaml", ".gradle", ".sh", ".py", ".rs", ".c", ".h",
    ".cc", ".cpp",
}
TEXT_NAMES = {"CMakeLists.txt"}

NS_GRADLE_RE = re.compile(r'(namespace = ")([^"]+)(")')
NS_ZYGOTE_RE = re.compile(r'(zygotePreloadName=")([A-Za-z0-9_.]+)(\.boot\.AppPreload")')
NS_ACTION_RE = re.compile(r'(android:name=")([A-Za-z0-9_.]+)(\.boot\.LAUNCH" )')
NS_LATELOAD_RE = re.compile(r'(\{package_name\}/)([A-Za-z0-9_.]+)(\.ui\.HomeActivity)')
NS_JNI_RE = re.compile(r"Java_([A-Za-z0-9_]+)_Natives_")
NS_FINDCLASS_RE = re.compile(r'FindClass\("([A-Za-z0-9_/]+)/Natives\$Profile"\)')

MANIFEST_COMPONENTS = [
    ".App",
    ".ui.HomeActivity",
    ".ui.webui.WebActivity",
    ".boot.ZygoteService",
    ".boot.BootReceiver",
    ".ui.util.FetchService",
]

# Must mirror kernel/manager/apk_sign.c
CERT_MAX_LENGTH = 1024
KSU_MAX_PACKAGE_NAME = 256
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"
V2_BLOCK_ID = 0x7109871A
VERITY_PADDING_ID = 0x42726577
KNOWN_BLOCK_IDS = {
    0xF05368C0: "v3 signature",
    0x1B93AD61: "v3.1 signature",
    0x6DFF800D: "source stamp",
    0x504B4453: "dependency info",
}

PR_SUFFIX = ".pr"
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")

# (file, regex, group -> meaning); each regex must match exactly once
LOCATIONS = [
    (
        KBUILD,
        re.compile(
            r"(ifeq \(\$\(KSU_IS_PR_BUILD\),0\)\nKSU_PACKAGE_NAME \?= )(\S+)(\nelse\nKSU_PACKAGE_NAME \?= )(\S+)(\nendif)"
        ),
        {2: "package", 4: "pr_package"},
    ),
    (KBUILD, re.compile(r"(\nKSU_EXPECTED_SIZE \?= )(\S+)()"), {2: "size"}),
    (KBUILD, re.compile(r"(\nKSU_EXPECTED_HASH \?= )(\S+)()"), {2: "hash"}),
    (KSUD_BUILD_RS, re.compile(r'(cargo:rustc-env=KSU_PACKAGE_NAME=)([^"]+)(")'), {2: "package"}),
    (
        MANAGER_GRADLE,
        re.compile(r'(val defaultManagerPackageName = if \(isPrBuild\) ")([^"]+)(" else ")([^"]+)(")'),
        {2: "pr_package", 4: "package"},
    ),
    (KSUD_WORKFLOW, re.compile(r'(export KSU_PACKAGE_NAME=")([^"]+)(")'), {2: "pr_package"}),
]


def read_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def write_text(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def is_text(path: Path) -> bool:
    return path.suffix in TEXT_SUFFIXES or path.name in TEXT_NAMES


def iter_scope_files(roots):
    seen = set()
    for root in roots:
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file() and is_text(path) and path not in seen:
                    seen.add(path)
                    yield path
        elif root.is_file():
            if root not in seen:
                seen.add(root)
                yield root


def read_one(path: Path, regex, group: int, what: str) -> str:
    matches = list(regex.finditer(read_text(path)))
    if len(matches) != 1:
        raise SystemExit(
            f"error: expected one {what} match in {path.relative_to(ROOT)}, found {len(matches)}"
        )
    return matches[0].group(group)


def read_unique(path: Path, regex, group: int, what: str) -> str:
    values = {m.group(group) for m in regex.finditer(read_text(path))}
    if len(values) != 1:
        raise SystemExit(
            f"error: expected one unique {what} value in {path.relative_to(ROOT)}, found {len(values)}"
        )
    return next(iter(values))


def apply_renames(text: str, ns: str, path: Path) -> str:
    ns_slash = ns.replace(".", "/")
    ns_under = ns.replace(".", "_")
    text = text.replace(OFFICIAL_NAMESPACE, ns)
    text = text.replace(OFFICIAL_NAMESPACE.replace(".", "/"), ns_slash)
    text = text.replace(OFFICIAL_NAMESPACE.replace(".", "_") + "_", ns_under + "_")
    # magica package segment -> boot (Java package, JNI symbol, manifest process)
    text = text.replace(f"{ns}.magica", f"{ns}.boot")
    text = text.replace(".magica.", ".boot.")
    text = text.replace("_magica_", "_boot_")
    text = text.replace(":magica_boot", ":boot")
    for old, new in IDENTIFIER_RENAMES:
        text = text.replace(old, new)
    in_sources = (
        path.is_relative_to(MANAGER_JAVA)
        or path.is_relative_to(MANAGER_TEST)
        or path.is_relative_to(MANAGER_CPP)
    )
    if in_sources:
        for old, new in LITERAL_RENAMES:
            text = text.replace(old, new)
    text = text.replace(
        'System.loadLibrary("kernelsu")', f'System.loadLibrary("{JNI_LIB_NAME}")'
    )
    text = text.replace("libksud.so", DAEMON_LIB)
    if path == CMAKE:
        text = re.sub(r"\bkernelsu\b", JNI_LIB_NAME, text)
    return text


class ApkError(Exception):
    pass


def load_cert(path: Path) -> bytes:
    data = path.read_bytes()
    if b"-----BEGIN CERTIFICATE-----" in data:
        pem = data.split(b"-----BEGIN CERTIFICATE-----", 1)[1].split(b"-----END CERTIFICATE-----", 1)[0]
        return base64.b64decode(b"".join(pem.split()))
    return data


def describe_cert(der: bytes) -> tuple:
    return len(der), hashlib.sha256(der).hexdigest()


def read_apk_cert(path: Path) -> tuple:
    """Return (certificate DER, list of reasons the kernel would reject the APK)."""
    data = path.read_bytes()
    size = len(data)

    def u16(off):
        return struct.unpack_from("<H", data, off)[0]

    def u32(off):
        return struct.unpack_from("<I", data, off)[0]

    def u64(off):
        return struct.unpack_from("<Q", data, off)[0]

    eocd = None
    for i in range(0x10000):
        pos = size - i - 2
        if pos - 20 < 0:
            break
        if u16(pos) == i and u32(pos - 20) == 0x06054B50:
            eocd = pos - 20
            break
    if eocd is None:
        raise ApkError("cannot find ZIP end of central directory")
    if eocd >= 20 and u32(eocd - 20) == 0x07064B50:
        raise ApkError("ZIP64 APKs are rejected by the kernel")

    cd_size, cd_offset = u32(eocd + 12), u32(eocd + 16)
    if cd_offset > eocd or cd_size != eocd - cd_offset or cd_offset < 0x20:
        raise ApkError("malformed central directory")

    pairs_end = cd_offset - 0x18
    block_size = u64(pairs_end)
    if data[pairs_end + 8 : pairs_end + 24] != APK_SIG_BLOCK_MAGIC:
        raise ApkError("no APK Signing Block (v1-only or unsigned APK)")
    if block_size < 0x18 or block_size > cd_offset - 8:
        raise ApkError("malformed APK Signing Block size")
    pos = cd_offset - block_size - 8
    if u64(pos) != block_size:
        raise ApkError("APK Signing Block size mismatch")
    pos += 8

    problems = []
    certs = []
    while pos < pairs_end:
        pair_size = u64(pos)
        pos += 8
        if pair_size < 4 or pair_size > pairs_end - pos:
            raise ApkError("malformed signing block pair")
        pair_end = pos + pair_size
        block_id = u32(pos)
        if block_id == V2_BLOCK_ID:
            certs.append(parse_v2_block(data, pos + 4, pair_end))
        elif block_id != VERITY_PADDING_ID:
            name = KNOWN_BLOCK_IDS.get(block_id, "unknown block")
            problems.append(f"contains {name} (0x{block_id:08x}); sign with v2 only")
        pos = pair_end

    if len(certs) != 1:
        raise ApkError(f"expected exactly one v2 signature block, found {len(certs)}")
    cert = certs[0]
    if len(cert) > CERT_MAX_LENGTH:
        problems.append(f"certificate is {len(cert)} bytes, kernel limit is {CERT_MAX_LENGTH}")
    return cert, problems


def parse_v2_block(data: bytes, pos: int, end: int) -> bytes:
    def length_prefixed(pos, end):
        if pos + 4 > end:
            raise ApkError("truncated v2 block")
        length = struct.unpack_from("<I", data, pos)[0]
        if length > end - pos - 4:
            raise ApkError("truncated v2 block")
        return pos + 4, pos + 4 + length

    # signers -> first signer -> signed data -> digests, then certificates -> first certificate
    pos, signers_end = length_prefixed(pos, end)
    pos, signer_end = length_prefixed(pos, signers_end)
    pos, signed_data_end = length_prefixed(pos, signer_end)
    _, digests_end = length_prefixed(pos, signed_data_end)
    pos, certs_end = length_prefixed(digests_end, signed_data_end)
    pos, cert_end = length_prefixed(pos, certs_end)
    return data[pos:cert_end]


def read_locations() -> dict:
    """Collect every configured value as {meaning: {value: [files]}}."""
    found = {}
    for path, regex, groups in LOCATIONS:
        matches = list(regex.finditer(read_text(path)))
        if len(matches) != 1:
            raise SystemExit(f"error: expected one match of {regex.pattern!r} in {path.relative_to(ROOT)}")
        for group, meaning in groups.items():
            found.setdefault(meaning, {}).setdefault(matches[0].group(group), []).append(
                str(path.relative_to(ROOT))
            )
    return found


def rewrite(updates: dict) -> None:
    """Replace values by meaning, e.g. {"package": "x", "pr_package": "x.pr"}."""
    for path in dict.fromkeys(loc[0] for loc in LOCATIONS):
        text = original = read_text(path)
        for _, regex, groups in (loc for loc in LOCATIONS if loc[0] == path):
            wanted = {g: updates[m] for g, m in groups.items() if m in updates}
            if not wanted:
                continue

            def repl(match, regex=regex, wanted=wanted):
                return "".join(wanted.get(g, match.group(g)) for g in range(1, regex.groups + 1))

            text, count = regex.subn(repl, text)
            if count != 1:
                raise SystemExit(f"error: expected one match in {path.relative_to(ROOT)}, found {count}")
        if text != original:
            write_text(path, text)
            print(f"updated {path.relative_to(ROOT)}")


def verify_namespace(ns: str) -> list:
    """Cross-check the rename across every file that must agree. Returns errors."""
    errors = []
    ns_slash = ns.replace(".", "/")
    ns_under = ns.replace(".", "_")

    def expect_equal(label, value):
        if value != ns:
            errors.append(f"{label} uses {value!r}, expected namespace {ns!r}")

    expect_equal("manager/app/build.gradle.kts", read_one(MANAGER_GRADLE, NS_GRADLE_RE, 2, "namespace"))

    jni_prefix = read_unique(JNI_CC, NS_JNI_RE, 1, "JNI symbol namespace")
    if jni_prefix != ns_under:
        errors.append(f"manager jni.cc Java_ prefix uses {jni_prefix!r}, expected {ns_under!r}")

    findclass = read_unique(JNI_CC, NS_FINDCLASS_RE, 1, "FindClass namespace")
    if findclass != ns_slash:
        errors.append(f"manager jni.cc FindClass uses {findclass!r}, expected {ns_slash!r}")

    expect_equal(
        "ksud late_load.rs am-start component",
        read_one(LATE_LOAD_RS, NS_LATELOAD_RE, 2, "late-load component"),
    )
    expect_equal(
        "manager AndroidManifest.xml zygotePreloadName",
        read_one(MANAGER_MANIFEST, NS_ZYGOTE_RE, 2, "zygote preload class"),
    )
    expect_equal(
        "manager AndroidManifest.xml boot action",
        read_one(MANAGER_MANIFEST, NS_ACTION_RE, 2, "boot action"),
    )

    manifest = read_text(MANAGER_MANIFEST)
    for component in MANIFEST_COMPONENTS:
        if f'android:name="{component}"' not in manifest:
            errors.append(f"manifest is missing component {component}")
    if 'android:process=":boot"' not in manifest:
        errors.append('manifest is missing process ":boot"')

    jni_cc = read_text(JNI_CC)
    if f"Java_{ns_under}_boot_AppPreload_forkDontCareAndExecDaemon" not in jni_cc:
        errors.append("jni.cc is missing the renamed AppPreload JNI symbol")

    aidl = MANAGER_AIDL.joinpath(*ns.split("."), "IAppInterface.aidl")
    if not aidl.is_file():
        errors.append(f"missing {aidl.relative_to(ROOT)}")
    elif not re.search(rf"^package {re.escape(ns)};$", read_text(aidl), re.MULTILINE):
        errors.append(f"{aidl.relative_to(ROOT)} does not declare package {ns}")

    for base, label in ((MANAGER_JAVA, "main"), (MANAGER_TEST, "test"), (MANAGER_AIDL, "aidl")):
        old = base.joinpath(*OFFICIAL_NAMESPACE.split("."))
        new = base.joinpath(*ns.split("."))
        if old.exists():
            errors.append(f"official source dir still exists: {old.relative_to(ROOT)}")
        if not new.is_dir():
            errors.append(f"renamed source dir missing: {new.relative_to(ROOT)}")

    proguard = read_text(PROGUARD)
    for line in (
        f"-keep class {ns}.Natives {{ *; }}",
        f"-keep class {ns}.Natives$Profile {{ *; }}",
        f"-keep class {ns}.boot.AppPreload {{ *; }}",
    ):
        if line not in proguard:
            errors.append(f"proguard-rules.pro is missing: {line}")

    cmake = read_text(CMAKE)
    if f"add_library({JNI_LIB_NAME}" not in cmake:
        errors.append(f"CMakeLists.txt does not build library {JNI_LIB_NAME}")
    if "kernelsu" in cmake:
        errors.append("CMakeLists.txt still references the old library name")

    natives_kt = MANAGER_JAVA.joinpath(*ns.split("."), "Natives.kt")
    if f'System.loadLibrary("{JNI_LIB_NAME}")' not in read_text(natives_kt):
        errors.append(f"{natives_kt.relative_to(ROOT)} does not load {JNI_LIB_NAME}")

    preload = MANAGER_JAVA.joinpath(*ns.split("."), "boot", "AppPreload.java")
    preload_text = read_text(preload)
    if f'System.loadLibrary("{JNI_LIB_NAME}")' not in preload_text:
        errors.append(f"{preload.relative_to(ROOT)} does not load {JNI_LIB_NAME}")
    if DAEMON_LIB not in preload_text:
        errors.append(f"{preload.relative_to(ROOT)} does not reference {DAEMON_LIB}")

    ksucli = MANAGER_JAVA.joinpath(*ns.split("."), "ui", "util", "KsuCli.kt")
    if DAEMON_LIB not in read_text(ksucli):
        errors.append(f"{ksucli.relative_to(ROOT)} does not reference {DAEMON_LIB}")

    for path, label in ((REPACK, "repack_apk.py"), (JUSTFILE, "justfile")):
        text = read_text(path)
        if DAEMON_LIB not in text:
            errors.append(f"{label} does not reference {DAEMON_LIB}")
        if "libksud.so" in text:
            errors.append(f"{label} still references the old daemon library name")

    for path in iter_scope_files(LEFTOVER_ROOTS):
        text = read_text(path)
        for token in LEFTOVER_TOKENS:
            if token in text:
                errors.append(f"{path.relative_to(ROOT)} still contains {token!r}")
    return errors


def check_apk_contents(path: Path, errors: list, notes: list) -> None:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for arch in ("arm64-v8a", "x86_64", "riscv64"):
            for lib in (JNI_LIB, DAEMON_LIB):
                if f"lib/{arch}/{lib}" not in names:
                    errors.append(f"{path}: missing lib/{arch}/{lib}")
        for name in names:
            if name.endswith("libkernelsu.so") or name.endswith("libksud.so"):
                errors.append(f"{path}: still contains {name}")
        for member in names:
            if not re.fullmatch(r"classes\d*\.dex", member):
                continue
            data = z.read(member)
            for token in (b"me/weishu", b"me.weishu", b"me_weishu_kernelsu"):
                count = data.count(token)
                if count:
                    errors.append(f"{path}: {member} still contains {token.decode()} ({count}x)")
            for token in (b"kernelsu", b"KernelSU", b"magica", b"ksud", b"Ksu"):
                count = data.count(token)
                if count:
                    notes.append(f"note: {member} contains {token.decode()} ({count}x)")


def cmd_cert_info(args) -> int:
    status = 0
    for name in args.files:
        path = Path(name)
        problems = []
        if path.read_bytes()[:2] == b"PK":
            try:
                der, problems = read_apk_cert(path)
            except ApkError as e:
                print(f"{name}: REJECTED by kernel: {e}")
                status = 1
                continue
        else:
            der = load_cert(path)
        size, digest = describe_cert(der)
        print(f"{name}:\n  KSU_EXPECTED_SIZE=0x{size:04x} ({size} bytes)\n  KSU_EXPECTED_HASH={digest}")
        for problem in problems:
            print(f"  REJECTED by kernel: {problem}")
            status = 1
    return status


def cmd_check(args) -> int:
    errors = []
    found = read_locations()
    for meaning, values in found.items():
        if len(values) != 1:
            detail = "; ".join(f"{v!r} in {', '.join(files)}" for v, files in values.items())
            errors.append(f"inconsistent {meaning}: {detail}")

    package = next(iter(found["package"]))
    pr_package = next(iter(found["pr_package"]))
    if pr_package != package + PR_SUFFIX:
        errors.append(f"PR package {pr_package!r} should be {package + PR_SUFFIX!r}")
    if package.startswith(OFFICIAL_NAMESPACE):
        errors.append("package name is still the official one")

    size = int(next(iter(found["size"])), 0)
    digest = next(iter(found["hash"]))
    if digest != digest.lower() or len(digest) != 64:
        errors.append("KSU_EXPECTED_HASH must be 64 lowercase hex characters")

    cert_size, cert_digest = describe_cert(load_cert(CERT_FILE))
    if (cert_size, cert_digest) != (size, digest):
        errors.append(f"kernel/Kbuild expects size 0x{size:04x} hash {digest}, but {CERT_FILE.relative_to(ROOT)} is 0x{cert_size:04x} {cert_digest}")

    namespace = read_one(MANAGER_GRADLE, NS_GRADLE_RE, 2, "namespace")
    errors.extend(verify_namespace(namespace))

    notes = []
    for apk in args.apk or []:
        try:
            der, problems = read_apk_cert(Path(apk))
        except ApkError as e:
            errors.append(f"{apk}: {e}")
            continue
        errors.extend(f"{apk}: {p}" for p in problems)
        apk_size, apk_digest = describe_cert(der)
        if (apk_size, apk_digest) != (size, digest):
            errors.append(f"{apk}: signed with 0x{apk_size:04x} {apk_digest}, kernel expects 0x{size:04x} {digest} (wrong keystore?)")
        check_apk_contents(Path(apk), errors, notes)

    print(f"package name : {package} (PR: {pr_package})")
    print(f"certificate  : size=0x{size:04x} hash={digest}")
    print(f"namespace    : {namespace}")
    print(f"libraries    : {JNI_LIB} (JNI), {DAEMON_LIB} (daemon)")
    for apk in args.apk or []:
        print(f"checked apk  : {apk}")
    for note in notes:
        print(note)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("OK")
    return 0


def cmd_set_package(args) -> int:
    name = args.package
    if not PACKAGE_RE.match(name):
        raise SystemExit(f"error: {name!r} is not a valid Android package name")
    if len(name + PR_SUFFIX) >= KSU_MAX_PACKAGE_NAME - 1:
        raise SystemExit("error: package name too long")
    rewrite({"package": name, "pr_package": name + PR_SUFFIX})
    return 0


def cmd_set_cert(args) -> int:
    src = Path(args.cert)
    if src.read_bytes()[:2] == b"PK":
        try:
            der, problems = read_apk_cert(src)
        except ApkError as e:
            raise SystemExit(f"error: {src}: {e}")
        if problems:
            raise SystemExit(f"error: {src}: " + "; ".join(problems))
    else:
        der = load_cert(src)
    size, digest = describe_cert(der)
    if size > CERT_MAX_LENGTH:
        raise SystemExit(f"error: certificate is {size} bytes, kernel limit is {CERT_MAX_LENGTH} (use RSA 2048 or EC)")
    if src.resolve() != CERT_FILE.resolve():
        if b"-----BEGIN CERTIFICATE-----" in src.read_bytes():
            shutil.copyfile(src, CERT_FILE)
        else:
            b64 = base64.encodebytes(der).decode().replace("\n", "")
            lines = [b64[i : i + 64] for i in range(0, len(b64), 64)]
            pem = "-----BEGIN CERTIFICATE-----\n" + "\n".join(lines) + "\n-----END CERTIFICATE-----\n"
            CERT_FILE.write_bytes(pem.encode())
        print(f"updated {CERT_FILE.relative_to(ROOT)}")
    rewrite({"size": f"0x{size:04x}", "hash": digest})
    return 0


def cmd_set_namespace(args) -> int:
    ns = args.namespace
    if not PACKAGE_RE.match(ns):
        raise SystemExit(f"error: {ns!r} is not a valid namespace")
    if ns.startswith(OFFICIAL_NAMESPACE):
        raise SystemExit("error: the new namespace must not keep the official prefix")

    moved = 0
    # 1. Move the Java/test/AIDL source trees onto the new namespace path.
    for base in (MANAGER_JAVA, MANAGER_TEST, MANAGER_AIDL):
        old = base.joinpath(*OFFICIAL_NAMESPACE.split("."))
        new = base.joinpath(*ns.split("."))
        if old.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(new))
            moved += 1
        elif not new.exists():
            raise SystemExit(f"error: neither {old.relative_to(ROOT)} nor {new.relative_to(ROOT)} exists")

    # 2. Rename the magica package segment to boot.
    for base in (MANAGER_JAVA, MANAGER_TEST):
        magica = base.joinpath(*ns.split("."), "magica")
        if magica.is_dir():
            shutil.move(str(magica), str(magica.with_name("boot")))
            moved += 1

    # 3. Rename files whose basename carries a renamed identifier.
    for base in (MANAGER_JAVA, MANAGER_TEST, MANAGER_AIDL):
        for path in sorted(base.rglob("*"), reverse=True):
            if not path.is_file():
                continue
            new_name = path.name
            for old, new in IDENTIFIER_RENAMES:
                new_name = new_name.replace(old, new)
            if new_name != path.name:
                shutil.move(str(path), str(path.with_name(new_name)))
                moved += 1

    # 4. Rewrite file contents.
    for path in iter_scope_files(RENAME_ROOTS):
        text = read_text(path)
        updated = apply_renames(text, ns, path)
        if updated != text:
            write_text(path, updated)
            print(f"updated {path.relative_to(ROOT)}")

    # 5. Drop empty official namespace directories.
    for base in (MANAGER_JAVA, MANAGER_TEST, MANAGER_AIDL):
        candidates = sorted(
            base.joinpath(*OFFICIAL_NAMESPACE.split(".")).parents,
            key=lambda p: len(p.parts),
            reverse=True,
        )
        for p in candidates:
            if p != base and p.is_relative_to(base) and p.exists() and not any(p.iterdir()):
                p.rmdir()

    print(f"moved {moved} path(s), rewrote sources to namespace {ns}")
    errors = verify_namespace(ns)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    print("namespace rename verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="verify package name, certificate, namespace and library names are consistent everywhere")
    p.add_argument("--apk", nargs="+", help="also verify these manager APKs would be accepted by the kernel and carry no official identifiers")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("cert-info", help="print KSU_EXPECTED_SIZE/HASH of a certificate or APK")
    p.add_argument("files", nargs="+")
    p.set_defaults(func=cmd_cert_info)

    p = sub.add_parser("set-package", help="change the manager package name everywhere")
    p.add_argument("package")
    p.set_defaults(func=cmd_set_package)

    p = sub.add_parser("set-cert", help="trust a new signing certificate (PEM or DER)")
    p.add_argument("cert")
    p.set_defaults(func=cmd_set_cert)

    p = sub.add_parser(
        "set-namespace",
        help="rename the Kotlin namespace, manifest component classes and packaged library names",
    )
    p.add_argument("namespace")
    p.set_defaults(func=cmd_set_namespace)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
