#!/usr/bin/env python3
"""Copy a mcugrad model.c, wrapping every op_<i>_<name>(...) call in model_run() with
MCUACCEL_PROF_BEGIN/END and appending the prof_* tables. Usage: patch_profile.py model.c out.c"""
import re
import sys

CALL = re.compile(r"^(\s*)(op_(\d+)_(\w+))\(([^;]*)\);\s*$")


def patch(src: str) -> str:
    head, sep, body = src.partition("void model_run(")
    assert sep, "no model_run"
    fn_end = body.index("\n}")
    run, tail = body[:fn_end], body[fn_end:]
    ops, out = [], []
    for line in run.split("\n"):
        m = CALL.match(line)
        if not m:
            out.append(line)
            continue
        ind, fn, idx, name, _ = m.groups()
        k = len(ops)
        # kernel = first mg_* call inside the op function body
        fb = re.search(r"static void %s\(.*?\n}\n" % fn, head, re.S)
        kern = re.search(r"\b(mg_\w+)\(", fb.group(0) if fb else "")
        ops.append((int(idx), name, kern.group(1) if kern else "none"))
        out.append(f"{ind}{{ MCUACCEL_PROF_BEGIN({k}); {line.strip()} MCUACCEL_PROF_END({k}); }}")
    n = len(ops)
    tbl = (f'\n#include "profile.h"\nuint64_t prof_cycles[{n}];\nconst int prof_n = {n};\n'
           f'const int prof_op[{n}] = {{{", ".join(str(o[0]) for o in ops)}}};\n'
           f'const char *const prof_name[{n}] = {{{", ".join(chr(34) + o[1] + chr(34) for o in ops)}}};\n'
           f'const char *const prof_kernel[{n}] = {{{", ".join(chr(34) + o[2] + chr(34) for o in ops)}}};\n\n')
    return head + tbl + "void model_run(" + "\n".join(out) + tail


if __name__ == "__main__":
    open(sys.argv[2], "w").write(patch(open(sys.argv[1]).read()))
