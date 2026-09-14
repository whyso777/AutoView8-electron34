#!/usr/bin/env python3
"""Build View8 v8dasm for Electron 32 / V8 12.8.374.38 on Windows (CI + local)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

V8_VERSION = "12.8.374.38"
MONOLITH_MIN_BYTES = 50_000_000
VS_CANDIDATES = [
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


def workspace_dir() -> Path:
    env = os.environ.get("GITHUB_WORKSPACE")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2]


def find_vs_install() -> Path:
    env = os.environ.get("vs2022_install")
    if env:
        return Path(env)
    for candidate in VS_CANDIDATES:
        if (candidate / "VC" / "Auxiliary" / "Build" / "vcvars64.bat").exists():
            return candidate
    return VS_CANDIDATES[0]


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    # On Windows, bare tool names like "gclient"/"fetch"/"gn" are .bat files;
    # subprocess won't resolve them via PATHEXT, so resolve explicitly.
    exe = cmd[0]
    search_path = (env or os.environ).get("PATH")
    resolved = shutil.which(exe, path=search_path)
    if resolved:
        exe = resolved
    full = [exe, *cmd[1:]]
    log(f">>> {' '.join(full)}")
    result = subprocess.run(full, cwd=str(cwd) if cwd else None, env=env, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _find_gclient(base: Path) -> Path | None:
    """Locate gclient.bat at the top of `base` or one level deeper (nested unzip)."""
    candidates = [base / "gclient.bat", *base.glob("*/gclient.bat")]
    for c in candidates:
        if c.exists():
            return c
    return None


def ensure_depot_tools(userprofile: Path, env: dict[str, str]) -> None:
    depot = userprofile / "depot_tools"
    gclient = _find_gclient(depot)
    if gclient is None:
        log("===== Getting depot_tools =====")
        if depot.exists():
            shutil.rmtree(depot, ignore_errors=True)
        depot.mkdir(parents=True, exist_ok=True)
        zip_path = userprofile / "depot_tools.zip"
        run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Invoke-WebRequest -Uri https://storage.googleapis.com/chrome-infra/depot_tools.zip "
                f"-OutFile '{zip_path}'",
            ],
            env=env,
        )
        run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Expand-Archive -Path '{zip_path}' -DestinationPath '{depot}' -Force",
            ],
            env=env,
        )
        zip_path.unlink(missing_ok=True)
        gclient = _find_gclient(depot)
    if gclient is None:
        log(f"ERROR: depot_tools incomplete under {depot}")
        raise SystemExit(1)
    depot_real = gclient.parent
    log(f"depot_tools at: {depot_real}")
    env["PATH"] = str(depot_real) + os.pathsep + env.get("PATH", "")
    run(["gclient"], cwd=userprofile, env=env)


def ensure_v8_checkout(userprofile: Path, env: dict[str, str]) -> Path:
    root = userprofile / "v8"
    root.mkdir(parents=True, exist_ok=True)
    if not (root / "v8" / ".git").exists():
        log("===== fetch v8 (no history) =====")
        run(["fetch", "--no-history", "v8"], cwd=root, env=env)
        gclient = root / ".gclient"
        if gclient.exists():
            text = gclient.read_text(encoding="utf-8")
            if "target_os" not in text:
                gclient.write_text(text + "target_os = ['win']\n", encoding="utf-8")
        else:
            gclient.write_text("target_os = ['win']\n", encoding="utf-8")
    v8_dir = root / "v8"
    run(["git", "fetch", "--tags", "--force"], cwd=v8_dir, env=env)
    run(["git", "checkout", V8_VERSION], cwd=v8_dir, env=env)
    run(["gclient", "sync", "--no-history", "-D"], cwd=root, env=env)
    run(["gclient", "runhooks"], cwd=root, env=env)
    return v8_dir


def apply_patches(v8_dir: Path, ws: Path) -> None:
    log("===== Applying patches =====")
    patch_py = ws / "scripts" / "v8dasm-builders" / "patch-utils" / "apply-patch-12_8.py"
    patch_log = ws / "patch-state.log"
    run([sys.executable, str(patch_py), str(v8_dir), str(patch_log)], env=os.environ.copy())


def configure_and_build(v8_dir: Path, ws: Path, env: dict[str, str]) -> Path:
    out_dir = v8_dir / "out.gn" / "x64.release"
    monolith = out_dir / "obj" / "v8_monolith.lib"
    if out_dir.exists():
        size = monolith.stat().st_size if monolith.exists() else 0
        if size < MONOLITH_MIN_BYTES:
            log(f"Removing stale out.gn (monolith={size} bytes)")
            shutil.rmtree(v8_dir / "out.gn", ignore_errors=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    args_src = ws / "configs" / "electron32-args.gn"
    shutil.copy2(args_src, out_dir / "args.gn")
    run(["gn", "gen", str(out_dir)], cwd=v8_dir, env=env)
    run(["ninja", "-C", str(out_dir), "-j2", "v8_monolith"], cwd=v8_dir, env=env)

    if not monolith.exists():
        log(f"ERROR: missing {monolith}")
        raise SystemExit(1)
    size = monolith.stat().st_size
    if size < MONOLITH_MIN_BYTES:
        log(f"ERROR: v8_monolith.lib too small ({size} bytes)")
        raise SystemExit(1)
    log(f"v8_monolith.lib size: {size} bytes")
    return monolith


def link_v8dasm(v8_dir: Path, ws: Path, env: dict[str, str]) -> Path:
    log("===== link v8dasm =====")
    obj_dir = v8_dir / "out.gn" / "x64.release" / "obj"
    llvm_bin = v8_dir / "third_party" / "llvm-build" / "Release+Asserts" / "bin"
    clang_cl = llvm_bin / "clang-cl.exe"
    if not clang_cl.exists():
        log(f"ERROR: clang-cl not found: {clang_cl}")
        raise SystemExit(1)

    out_name = v8_dir / f"v8dasm-{V8_VERSION}.exe"
    dasm = ws / "Disassembler" / "v8dasm.cpp"
    link_env = env.copy()
    link_env["PATH"] = str(llvm_bin) + os.pathsep + link_env.get("PATH", "")

    cmd = [
        str(clang_cl),
        str(dasm),
        "/nologo",
        "/std:c++20",
        "/O2",
        "/EHsc",
        f"/I{v8_dir}",
        f"/I{v8_dir / 'include'}",
        f"/I{v8_dir / 'gen'}",
        "/DV8_COMPRESS_POINTERS",
        "/DV8_ENABLE_SANDBOX",
        f"/Fe:{out_name}",
        "/link",
        f"/LIBPATH:{obj_dir}",
        "v8_libbase.lib",
        "v8_libplatform.lib",
        "v8_monolith.lib",
        "winmm.lib",
        "Dbghelp.lib",
    ]
    run(cmd, cwd=v8_dir, env=link_env)
    if not out_name.exists():
        log(f"ERROR: linker produced no output: {out_name}")
        raise SystemExit(1)
    return out_name


def stage_binary(ws: Path, built_exe: Path) -> Path:
    bin_dir = ws / "Bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    staged = bin_dir / f"{V8_VERSION}.exe"
    shutil.copy2(built_exe, staged)
    shutil.copy2(built_exe, ws / f"v8dasm-{V8_VERSION}.exe")
    log(f"SUCCESS: {staged} ({staged.stat().st_size} bytes)")
    return staged


def main() -> int:
    ws = workspace_dir()
    userprofile = Path(os.environ["USERPROFILE"])
    env = os.environ.copy()
    env["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
    env["vs2022_install"] = str(find_vs_install())
    env["_CL_"] = "/D_SILENCE_ALL_CXX20_DEPRECATION_WARNINGS"

    log("==========================================")
    log(f"Electron 32 v8dasm build (Windows x64)")
    log(f"V8 {V8_VERSION}")
    log(f"Workspace: {ws}")
    log(f"vs2022_install: {env['vs2022_install']}")
    log("==========================================")

    ensure_depot_tools(userprofile, env)
    v8_dir = ensure_v8_checkout(userprofile, env)
    apply_patches(v8_dir, ws)
    configure_and_build(v8_dir, ws, env)
    built = link_v8dasm(v8_dir, ws, env)
    stage_binary(ws, built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
