"""Pure-Python checks of experiments/run_soc.py parsing/comparison helpers (no simulation)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "experiments"))
import run_soc  # noqa: E402


def test_mismatch_counts_bytes_and_missing():
    assert run_soc.mismatch([[1, 2], None, [3, 4]], [[1, 2], [5, 6], [3, 5]]) == (3, [1, 2])
    assert run_soc.mismatch([[1, 2]], [[1, 2]]) == (0, [])


def test_parse_profile_shapes():
    out = ("MCUGRAD_PROF op=0 name=a kernel=mg_x cycles=30\nMCUGRAD_PROF op=1 name=chain_0_1_1 kernel=mcuaccel_run cycles=9\n"
           "MCUGRAD_PROFH inf=0 10 3\nMCUGRAD_PROFH inf=1 10 3\nMCUGRAD_PROFH inf=2 10 3\n"
           "MCUGRAD_PROFC n=8 100 0 0 150 200 0 0 260\nMCUGRAD_PROF_DONE\n")
    p = run_soc.parse_profile(out, images=2, runs=1)
    assert p["intervals"] == [50, 60] and p["ops"][0]["per_inference"] == [10, 10, 10]
