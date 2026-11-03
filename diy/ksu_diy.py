#!/usr/bin/env python3
"""KernelSU DIY helper: keep manager package name and signing certificate in sync.

The kernel only accepts a manager whose package name equals KSU_PACKAGE_NAME and
whose APK v2 signing certificate matches KSU_EXPECTED_SIZE / KSU_EXPECTED_HASH
(see kernel/manager/apk_sign.c). These values are duplicated across the kernel,
ksud, the manager build and CI, so this tool rewrites and verifies all of them.

  python3 diy/ksu_diy.py check [--apk app.apk ...]
  python3 diy/ksu_diy.py cert-info <cert.pem|cert.der|app.apk> ...
  python3 diy/ksu_diy.py set-package com.example.manager
  python3 diy/ksu_diy.py set-cert path/to/cert.pem
"""

import argparse
import base64
import hashlib
import re
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CERT_FILE = ROOT / "diy" / "manager-cert.pem"

KBUILD = ROOT / "kernel" / "Kbuild"
KSUD_BUILD_RS = ROOT / "userspace" / "ksud" / "build.rs"
MANAGER_GRADLE = ROOT / "manager" / "app" / "build.gradle.kts"
KSUD_WORKFLOW = ROOT / ".github" / "workflows" / "ksud.yml"

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
        matches = list(regex.finditer(path.read_text(encoding="utf-8")))
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
        text = original = path.read_text(encoding="utf-8")
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
            path.write_bytes(text.encode("utf-8"))
            print(f"updated {path.relative_to(ROOT)}")


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
    if package.startswith("me.weishu.kernelsu"):
        errors.append("package name is still the official one")

    size = int(next(iter(found["size"])), 0)
    digest = next(iter(found["hash"]))
    if digest != digest.lower() or len(digest) != 64:
        errors.append("KSU_EXPECTED_HASH must be 64 lowercase hex characters")

    cert_size, cert_digest = describe_cert(load_cert(CERT_FILE))
    if (cert_size, cert_digest) != (size, digest):
        errors.append(f"kernel/Kbuild expects size 0x{size:04x} hash {digest}, but {CERT_FILE.relative_to(ROOT)} is 0x{cert_size:04x} {cert_digest}")

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

    print(f"package name : {package} (PR: {pr_package})")
    print(f"certificate  : size=0x{size:04x} hash={digest}")
    for apk in args.apk or []:
        print(f"checked apk  : {apk}")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="verify package name and certificate are consistent everywhere")
    p.add_argument("--apk", nargs="+", help="also verify these manager APKs would be accepted by the kernel")
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

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
