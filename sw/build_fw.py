#!/usr/bin/env python3
"""Build a benchmark firmware image for the PicoRV32 SoC.
    build_fw.py <model> [--images 10] [--runs 3] [--profile] [--model-dir DIR] [--out DIR]
model.c/model.h come unchanged from third_party/mcugrad/firmware/generated/<model> (the baseline,
D6) unless --profile (model.c patched by patch_profile.py) or --model-dir (backend output).
Writes <out>/{fw.elf,fw.hex,fw.map}; prints the hex path."""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_testset  # noqa: E402
import patch_profile  # noqa: E402

MG = os.path.join(ROOT, "third_party", "mcugrad", "firmware")
RAM_BASE, RAM_BYTES, STACK_MIN = 0x80000000, 1 << 20, 16 * 1024 + 1024
CC = "riscv-none-elf-gcc"
FLAGS = ["-O2", "-march=rv32im_zicsr", "-mabi=ilp32", "-nostdlib", "-ffreestanding", "-fno-builtin",
         "-Wall", "-Wextra", "-Wl,--no-warn-rwx-segments", "-Wl,--gc-sections"]
BOARD = "picorv32-soc"


def compiler_version() -> str:
    return subprocess.run([CC, "--version"], capture_output=True, text=True).stdout.splitlines()[0]


def build(model: str, images: int, runs: int, profile: bool = False, model_dir: str | None = None,
          out: str | None = None, stub: bool = False) -> dict:
    src = model_dir or os.path.join(MG, "generated", model)
    out = out or os.path.join(ROOT, "build", "fw", f"{model}{'_prof' if profile else ''}{'_stub' if stub else ''}_i{images}_r{runs}")
    os.makedirs(out, exist_ok=True)
    gen_testset.gen(model, images, out)
    shutil.copy(os.path.join(src, "model.h"), out)
    mc = open(os.path.join(src, "model.c")).read()
    with open(os.path.join(out, "model.c"), "w") as f:
        f.write('#include "model.h"\nvoid model_run(const int8_t *i, int8_t *o) { (void)i; (void)o; }\n' if stub
                else patch_profile.patch(mc) if profile else mc)
    common = os.path.join(ROOT, "sw", "common")
    srcs = [os.path.join(out, "model.c"), os.path.join(out, "testset.c"),
            os.path.join(MG, "common", "bench_main.c"), os.path.join(common, "platform.c"),
            os.path.join(common, "main_bench.c"), os.path.join(common, "crt0.S")]
    defs = [f'-DBENCH_BOARD="{BOARD}"', f"-DBENCH_RUNS={runs}"] + (["-DMCUACCEL_PROFILE"] if profile else [])
    elf, hexf, mapf = (os.path.join(out, "fw" + e) for e in (".elf", ".hex", ".map"))
    cmd = [CC, *FLAGS, *defs, f"-I{out}", f"-I{os.path.join(MG, 'common')}", f"-I{common}",
           "-T", os.path.join(common, "link.ld"), f"-Wl,-Map={mapf}", "-o", elf, *srcs, "-lgcc"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"firmware build failed:\n{' '.join(cmd)}\n{r.stderr}")
    if r.stderr.strip():
        print(r.stderr, file=sys.stderr)
    from elftools.elf.elffile import ELFFile
    with open(elf, "rb") as f:
        end = max(s["p_paddr"] + s["p_memsz"] for s in ELFFile(f).iter_segments() if s["p_type"] == "PT_LOAD")
    used = end - RAM_BASE
    if used + STACK_MIN > RAM_BYTES:
        sys.exit(f"image does not fit: {used} B image + {STACK_MIN} B stack > {RAM_BYTES} B RAM")
    e2h = os.path.join(ROOT, "sw", "tools", "elf2hex.py")
    if not os.path.exists(e2h):
        e2h = os.path.join(ROOT, "third_party", "corv32", "sw", "tools", "elf2hex.py")
    subprocess.run([sys.executable, e2h, elf, hexf, "--size", str(RAM_BYTES)], check=True)
    import re
    m = re.search(r"^\s+0x([0-9a-f]+)\s+testset_images\b", open(mapf).read(), re.M)
    x_align = int(m.group(1), 16) % 4 if m else None
    return {"x_align": x_align, "hex": hexf, "elf": elf, "map": mapf, "dir": out, "image_bytes": used,
            "flags": " ".join(FLAGS + defs), "compiler": compiler_version()}


def build_app(srcs: list[str], out: str, defs: list[str] | None = None) -> dict:
    """Bare-metal app (no bench_main): crt0 + link.ld + `srcs`, the same CC/FLAGS/link/elf2hex as
    build(). Used by sw/apps/*.c (e.g. drv_timing). Returns {elf, hex, map, dir, flags, compiler}."""
    os.makedirs(out, exist_ok=True)
    common = os.path.join(ROOT, "sw", "common")
    elf, hexf, mapf = (os.path.join(out, "fw" + e) for e in (".elf", ".hex", ".map"))
    defs = defs or []
    cmd = [CC, *FLAGS, *defs, f"-I{os.path.join(ROOT, 'sw', 'driver')}", "-T", os.path.join(common, "link.ld"),
           f"-Wl,-Map={mapf}", "-o", elf, *srcs, os.path.join(common, "crt0.S"), "-lgcc"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode or r.stderr.strip():
        sys.exit(f"app build failed or warned:\n{' '.join(cmd)}\n{r.stderr}")
    subprocess.run([sys.executable, os.path.join(ROOT, "sw", "tools", "elf2hex.py"), elf, hexf,
                    "--size", str(RAM_BYTES)], check=True)
    return {"elf": elf, "hex": hexf, "map": mapf, "dir": out, "flags": " ".join(FLAGS + defs),
            "compiler": compiler_version()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", choices=list(gen_testset.SPEC))
    ap.add_argument("--images", type=int, default=10)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--model-dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    print(build(a.model, a.images, a.runs, a.profile, a.model_dir, a.out)["hex"])
