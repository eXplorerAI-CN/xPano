from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections import deque
from pathlib import Path


IMPORT_PATTERN = re.compile(r"^\s*DLL Name:\s*([^\s]+\.dll)\s*$", re.MULTILINE)

WINDOWS_INBOX_DLLS = {
    "aclui.dll", "advapi32.dll", "amsi.dll", "apphelp.dll", "authz.dll", "avrt.dll",
    "avicap32.dll", "bcrypt.dll", "bcryptprimitives.dll", "cabinet.dll",
    "cfgmgr32.dll", "combase.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll",
    "cryptbase.dll", "cryptnet.dll", "cryptsp.dll", "d2d1.dll", "d3d9.dll", "d3d10.dll",
    "d3d10_1.dll", "d3d11.dll", "d3d12.dll", "d3dcompiler_47.dll", "davhlpr.dll",
    "dbghelp.dll", "dcomp.dll", "dhcpcsvc.dll", "dhcpcsvc6.dll", "dinput8.dll",
    "dnsapi.dll", "drvstore.dll", "dsound.dll", "dwmapi.dll", "dwrite.dll", "dxcore.dll",
    "dxgi.dll", "dxguid.dll", "esent.dll", "fwpuclnt.dll", "gdi32.dll", "gdi32full.dll",
    "gpapi.dll", "glu32.dll", "hid.dll",
    "imagehlp.dll", "imm32.dll", "iphlpapi.dll", "kernel32.dll", "kernelbase.dll",
    "mf.dll", "mfcore.dll", "mfplat.dll", "mfreadwrite.dll", "mpr.dll", "msi.dll", "msvcrt.dll",
    "mswsock.dll", "ncrypt.dll", "ndfapi.dll", "netapi32.dll", "netutils.dll", "normaliz.dll",
    "nsi.dll", "ntasn1.dll", "ntdll.dll", "ole32.dll",
    "oleaut32.dll", "opengl32.dll", "powrprof.dll", "propsys.dll", "psapi.dll", "rpcrt4.dll",
    "pdh.dll", "profapi.dll", "rasadhlp.dll", "rasapi32.dll", "rasman.dll", "rstrtmgr.dll",
    "samcli.dll", "schedcli.dll", "sechost.dll", "secur32.dll", "setupapi.dll", "shell32.dll",
    "shlwapi.dll", "srvcli.dll", "sspicli.dll", "tdh.dll", "ucrtbase.dll", "urlmon.dll",
    "user32.dll", "userenv.dll", "usp10.dll", "uxtheme.dll", "vaultcli.dll", "version.dll",
    "wbemcomn.dll", "wbemprox.dll", "wbemsvc.dll", "wevtapi.dll", "windowscodecs.dll",
    "winhttp.dll", "wininet.dll", "winmm.dll", "winsta.dll", "wintrust.dll", "winusb.dll",
    "wkscli.dll", "wldap32.dll", "ws2_32.dll", "wsock32.dll", "wtsapi32.dll", "xmllite.dll",
}
WINDOWS_DRIVER_DLLS = {"nvcuda.dll"}


def is_windows_inbox_dll(name):
    lower = str(name).lower()
    return lower.startswith(("api-ms-", "ext-ms-")) or lower in WINDOWS_INBOX_DLLS


def is_windows_system_dll(name):
    return is_windows_inbox_dll(name) or str(name).lower() in WINDOWS_DRIVER_DLLS


def resolve_dependency_closure(entry, app_local, read_imports, is_system_dll):
    entry = Path(entry)
    app_local_by_name = {}
    for path in map(Path, app_local):
        key = path.name.lower()
        app_local_by_name.setdefault(key, []).append(path)

    checked = []
    queued = {entry.name.lower()}
    queue = deque([entry])
    unresolved = []
    while queue:
        binary = queue.popleft()
        checked.append(binary)
        for imported_name in read_imports(binary):
            key = imported_name.lower()
            if any(separator in imported_name for separator in ("/", "\\")) or ":" in imported_name:
                unresolved.append(f"{binary.name} -> {imported_name}")
                continue
            if is_system_dll(imported_name):
                continue
            candidates = app_local_by_name.get(key, [])
            sibling = [candidate for candidate in candidates if candidate.parent == binary.parent]
            if len(sibling) == 1:
                dependency = sibling[0]
            elif len(candidates) == 1:
                dependency = candidates[0]
            else:
                dependency = None
            if dependency is None:
                unresolved.append(f"{binary.name} -> {imported_name}")
                continue
            if key not in queued:
                queued.add(key)
                queue.append(dependency)
    if unresolved:
        raise RuntimeError("Unresolved Windows DLL imports: " + ", ".join(sorted(unresolved)))
    return checked


def _find_objdump(explicit=None):
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            return candidate
        raise RuntimeError(f"PE import reader was not found: {candidate}")
    for name in ("llvm-objdump.exe", "objdump.exe"):
        candidate = shutil.which(name)
        if candidate:
            return Path(candidate)
    raise RuntimeError("llvm-objdump.exe or objdump.exe is required for release validation")


def _read_imports(objdump, binary):
    binary = Path(binary)
    if not binary.is_file():
        raise RuntimeError(f"Release binary is missing: {binary}")
    completed = subprocess.run(
        [str(objdump), "-p", str(binary)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Failed to inspect PE imports for {binary}: {detail}")
    return IMPORT_PATTERN.findall(completed.stdout)


def _audit_tree(tree_root, objdump):
    tree_root = Path(tree_root)
    if not tree_root.is_dir():
        raise RuntimeError(f"Release tree is missing: {tree_root}")
    binaries = sorted(
        path for path in tree_root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"}
    )
    if not binaries:
        raise RuntimeError(f"No PE binaries were found under: {tree_root}")
    app_local = [path for path in binaries if path.suffix.lower() in {".dll", ".pyd"}]
    imports_cache = {}

    def read_imports(path):
        path = Path(path)
        if path not in imports_cache:
            imports_cache[path] = _read_imports(objdump, path)
        return imports_cache[path]

    for entry in binaries:
        resolve_dependency_closure(
            entry,
            app_local,
            read_imports,
            is_windows_system_dll,
        )
    return binaries


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fail when a Windows release binary imports an unresolved non-system DLL."
    )
    parser.add_argument("--entry")
    parser.add_argument("--tree-root", action="append", default=[])
    parser.add_argument("--app-local", action="append", default=[])
    parser.add_argument("--objdump")
    args = parser.parse_args(argv)
    if not args.entry and not args.tree_root:
        parser.error("at least one --entry or --tree-root is required")
    objdump = _find_objdump(args.objdump)
    checked = []
    if args.entry:
        checked.extend(resolve_dependency_closure(
            Path(args.entry),
            [Path(path) for path in args.app_local],
            lambda path: _read_imports(objdump, path),
            is_windows_system_dll,
        ))
    for tree_root in args.tree_root:
        checked.extend(_audit_tree(tree_root, objdump))
    print(f"DLL closure verified for {len(checked)} PE files", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
