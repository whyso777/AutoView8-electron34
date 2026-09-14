#!/usr/bin/env python3
"""Apply View8 patches for V8 13.2.152.41 (Electron 34.x / Bytenode .jsc)."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def log(msg: str, log_file: Path | None) -> None:
    print(msg)
    if log_file:
        with log_file.open("a", encoding="utf-8") as f:
            f.write(msg + "\n")


def patch_file(path: Path, old: str, new: str, label: str, log_file: Path | None) -> bool:
    text = path.read_text(encoding="utf-8")
    if new in text:
        log(f"[OK] {label}: already patched", log_file)
        return True
    if old not in text:
        log(f"[FAIL] {label}: anchor not found", log_file)
        return False
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    log(f"[OK] {label}: patched", log_file)
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: apply-patch-13_2.py <v8_dir> [log_file]")
        return 1

    v8_dir = Path(sys.argv[1])
    log_file = Path(sys.argv[2]) if len(sys.argv) > 2 else None

    log("=== View8 patch for V8 13.2.152.41 (Electron 34) ===", log_file)
    ok = True

    printer = v8_dir / "src/diagnostics/objects-printer.cc"
    text = printer.read_text(encoding="utf-8")
    if "// PrintSourceCode(os);" in text and "SharedFunctionInfoPrint" in text:
        log("[OK] objects-printer remove PrintSourceCode: already patched", log_file)
    else:
        patched, count = re.subn(
            r"(\n  )PrintSourceCode\(os\);\n(  // Script files are often large[^\n]*)",
            r"\1// PrintSourceCode(os);\n\2",
            text,
            count=1,
        )
        if count == 0:
            log("[FAIL] objects-printer remove PrintSourceCode: anchor not found", log_file)
            ok = False
        else:
            printer.write_text(patched, encoding="utf-8")
            log("[OK] objects-printer remove PrintSourceCode: patched", log_file)
            text = patched

    ok &= patch_file(
        printer,
        '  os << "\\n - age: " << age();\n  os << "\\n";\n}',
        '  os << "\\n - age: " << age();\n'
        '  os << "\\nStart BytecodeArray\\n";\n'
        '  this->GetActiveBytecodeArray(GetIsolateForSandbox(*this))->Disassemble(os);\n'
        '  os << "\\nEnd BytecodeArray\\n";\n'
        '  os << std::flush;\n'
        '  os << "\\n";\n}',
        "objects-printer add BytecodeArray disasm",
        log_file,
    )

    string_cc = v8_dir / "src/objects/string.cc"
    text = string_cc.read_text(encoding="utf-8")
    pat = r"\s*if\s*\(\s*len\s*>\s*kMaxShortPrintLength\s*\)\s*\{[^}]*\}\s*\n?"
    if re.search(pat, text):
        string_cc.write_text(re.sub(pat, "\n", text, count=1, flags=re.DOTALL), encoding="utf-8")
        log("[OK] string.cc: removed truncation", log_file)
    else:
        log("[OK] string.cc: truncation already removed", log_file)

    ok &= patch_file(
        v8_dir / "src/snapshot/deserializer.cc",
        "  CHECK_EQ(magic_number_, SerializedData::kMagicNumber);\n",
        "",
        "deserializer remove magic check",
        log_file,
    )

    ok &= patch_file(
        v8_dir / "src/snapshot/code-serializer.cc",
        """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_ro_snapshot_checksum,
    uint32_t expected_source_hash) const {
  SerializedCodeSanityCheckResult result =
      SanityCheckWithoutSource(expected_ro_snapshot_checksum);
  if (result != SerializedCodeSanityCheckResult::kSuccess) return result;
  return SanityCheckJustSource(expected_source_hash);
}""",
        """SerializedCodeSanityCheckResult SerializedCodeData::SanityCheck(
    uint32_t expected_ro_snapshot_checksum,
    uint32_t expected_source_hash) const {
  return SerializedCodeSanityCheckResult::kSuccess;
}""",
        "code-serializer bypass SanityCheck",
        log_file,
    )

    cs = v8_dir / "src/snapshot/code-serializer.cc"
    cst = cs.read_text(encoding="utf-8")
    anchor = """  DirectHandle<SharedFunctionInfo> result;
  if (!maybe_result.ToHandle(&result)) {
    // Deserializing may fail if the reservations cannot be fulfilled.
    if (v8_flags.profile_deserialization) PrintF("[Deserializing failed]\\n");
    return MaybeDirectHandle<SharedFunctionInfo>();
  }

  // Check whether the newly deserialized data should be merged into an"""
    replacement = """  DirectHandle<SharedFunctionInfo> result;
  if (!maybe_result.ToHandle(&result)) {
    // Deserializing may fail if the reservations cannot be fulfilled.
    if (v8_flags.profile_deserialization) PrintF("[Deserializing failed]\\n");
    return MaybeDirectHandle<SharedFunctionInfo>();
  }

  std::cout << "\\nStart SharedFunctionInfo\\n";
  result->SharedFunctionInfoPrint(std::cout);
  std::cout << "\\nEnd SharedFunctionInfo\\n";
  std::cout << std::flush;

  // Check whether the newly deserialized data should be merged into an"""
    if "Start SharedFunctionInfo" in cst:
        log("[OK] code-serializer: SFI print already present", log_file)
    elif anchor in cst:
        cs.write_text(cst.replace(anchor, replacement, 1), encoding="utf-8")
        log("[OK] code-serializer: added SFI print after deserialize", log_file)
    else:
        log("[FAIL] code-serializer: deserialize anchor not found", log_file)
        ok = False

    log(f"=== done ok={ok} ===", log_file)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
