# Vendored: corv32

- Source: /Users/anirudh/corv32 (local repository, no remote)
- Commit: e1d0bc05a031794a36647f964eaece47dbb3d619 (e1d0bc0; corv32 HEAD has since moved on, this is the pinned tree via git archive)
- Date vendored: 2026-10-10
- Copied: `rtl/`, `docs/`, `sw/common/`, `sw/tools/`, `LICENSE`, `CLAUDE.md`, `PLAN.md`
- Not copied: tools/, third_party/, .venv, build, .git

Why: `docs/DESIGN.md` section 3 is the bus protocol spec (req/gnt/rvalid) the accelerator speaks;
`rtl/` is the reference for that bus and the drop-in `corv32_core` later (see D1); `sw/common/`
holds crt0.S and link.ld for firmware; `sw/tools/elf2hex.py` converts ELF to the hex the
testbenches load. Do not edit; re-vendor from a new commit instead.
