"""C2a: seeded random layers (DESIGN 10.2) through mcuaccel_top, bit-exact vs int8ops.

One simulation per P; all seeds run inside one cocotb test with a reset between seeds.
Per seed: layer from backend/tests/helpers.gen_layer (x/y alignment 0..3, stalls in 30 % of
seeds), image with >= 8 guard bytes, RTL run, y compared byte for byte with int8ops and every
byte outside y (except the STAMP words) required unchanged. Rows go to the CSV (harness.csv_path).
"""
import os
import sys
from pathlib import Path

import cocotb
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from accel import harness as H  # noqa: E402
from accel.gen_layers import Item, build_image, exec_stats, gen_layer  # noqa: E402

pytestmark = H.PYTESTMARK


def seed_list():
    if os.environ.get("MCUACCEL_SMOKE"):
        return [0, 1, 2]
    s0 = H.env_int("MCUACCEL_SEED0", 0)
    return list(range(s0, s0 + H.env_int("MCUACCEL_SEEDS", 40)))


def stalls_for(seed):
    if os.environ.get("MCUACCEL_SMOKE"):
        return seed % 2 == 1
    return bool(np.random.default_rng(seed + 777).random() < .3)


@cocotb.test()
async def random_layers(dut):
    b = await H.Bench.create(dut)
    csv = H.ResultsCsv()
    do_exec = os.environ.get("MCUACCEL_EXEC_STATS", "1") == "1"
    tot_mm = tot_hang = 0
    for seed in seed_list():
        L, _, xa, ya, x = gen_layer(seed)        # gen_layer's P is ignored: the DUT's P is fixed
        items = [Item(L, x, xa, ya)]
        img = build_image(items, b.P, seed)
        stalls = stalls_for(seed)
        res = await b.run_image(img, stalls=stalls, seed=seed)
        csv.add(seed, L, xa, ya, stalls, res, b.P)
        tot_mm += res.mismatches
        tot_hang += res.timed_out
        line = (f"seed {seed} {L.mode} H{L.H}W{L.W}C{L.C}O{L.O} K{L.KH}x{L.KW} s{L.SH}x{L.SW} "
                f"p{L.PH}x{L.PW} xa{xa} ya{ya} stall={int(stalls)}: cycles={res.cycles} "
                f"stamps={res.stamps} mismatches={res.mismatches} (y {res.y_mismatch}, "
                f"outside {res.outside}) status={res.status:#x} ncmd={res.ncmd}/{img.ncmds}")
        if do_exec:
            try:
                st = exec_stats(items, b.P, seed)
                line += " | executor words/cmd=" + ";".join(
                    str(s["x_words"] + s["w_words"] + s["p_words"] + s["d_words"] + s["y_words"])
                    for s in st)
            except Exception as e:                  # information only
                line += f" | executor stats unavailable: {e!r}"
        dut._log.info(line)
        b.check_normal(res, img, f"seed {seed}")
        b.chk_s.check_drained()
        b.chk_m.check_drained()
    dut._log.info("TOTAL mismatching bytes: %d, hangs: %d, csv: %s", tot_mm, tot_hang, csv.path)
    b.finish("random_layers_smoke" if os.environ.get("MCUACCEL_SMOKE") else "random_layers")
    # extra summary for the pytest side
    import json
    (H.OUT / "random_layers_totals.json").write_text(json.dumps(
        {"mismatches": tot_mm, "hangs": int(tot_hang), "seeds": len(seed_list()),
         "csv": str(csv.path), "stub": H.STUB_MODE}))


def _run(smoke):
    import json
    env = {"MCUACCEL_SMOKE": "1"} if smoke else {}
    for P in H.ps_list():
        if P == H.ps_list()[0]:
            H.reset_csv()
        H.run_sim("test_random_layers", P=P, env=env, testcase="random_layers")
        tot = json.loads((H.OUT / "random_layers_totals.json").read_text())
        n = sum(1 for _ in open(tot["csv"])) - 1
        print(f"RANDOM_LAYERS P={P}: seeds={tot['seeds']} mismatching_bytes={tot['mismatches']} "
              f"hangs={tot['hangs']} csv_rows={n} csv={tot['csv']}")
        assert tot["hangs"] == 0, "DUT never reached DONE/!BUSY"
        assert n >= tot["seeds"]
        if not tot["stub"]:
            assert tot["mismatches"] == 0


def test_smoke(monkeypatch):
    """Three seeds (one stalling): plumbing check; against the stub it reports mismatches."""
    monkeypatch.setenv("MCUACCEL_CSV", str(H.OUT / "m3_bitexact_smoke.csv"))
    _run(smoke=True)


def test_random_layers():
    _run(smoke=False)
