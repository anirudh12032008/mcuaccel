#!/usr/bin/env python3
"""ELF -> $readmemh hex (one 32-bit little-endian word per line) for a RAM at BASE of SIZE bytes."""
import argparse
import sys

from elftools.elf.elffile import ELFFile


def elf_to_words(path, base, size):
    img = bytearray(size)
    with open(path, "rb") as f:
        for seg in ELFFile(f).iter_segments():
            if seg["p_type"] != "PT_LOAD":
                continue
            data, addr = seg.data(), seg["p_paddr"]
            if addr < base or addr + len(data) > base + size:
                sys.exit(f"{path}: segment 0x{addr:x}+{len(data)} outside RAM 0x{base:x}+{size}")
            img[addr - base:addr - base + len(data)] = data
    return [int.from_bytes(img[i:i + 4], "little") for i in range(0, size, 4)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("elf")
    ap.add_argument("hex")
    ap.add_argument("--base", type=lambda s: int(s, 0), default=0x80000000)
    ap.add_argument("--size", type=lambda s: int(s, 0), default=65536)
    a = ap.parse_args()
    with open(a.hex, "w") as f:
        f.writelines(f"{w:08x}\n" for w in elf_to_words(a.elf, a.base, a.size))


if __name__ == "__main__":
    main()
