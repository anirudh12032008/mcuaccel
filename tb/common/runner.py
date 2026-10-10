"""Build a design with Verilator and run the cocotb tests of one module (cocotb 2.1 runner)."""
from pathlib import Path

from cocotb_tools.check_results import get_results
from cocotb_tools.runner import get_runner

ROOT = Path(__file__).resolve().parents[2]
PICO = ROOT / "third_party/picorv32/picorv32.v"
VLT = ROOT / "syn/lint.vlt"


def rtl(*names):
    return [ROOT / "rtl" / n for n in names]


def run_cocotb(top, sources, test_module, test_dir, build_name, parameters=None, plusargs=None):
    build = ROOT / "build" / build_name
    runner = get_runner("verilator")
    runner.build(sources=[str(s) for s in sources], hdl_toplevel=top, build_dir=build / "sim",
                 build_args=["-Wno-fatal", "--timing", str(VLT)], parameters=parameters or {},
                 always=True)
    xml = runner.test(hdl_toplevel=top, test_module=test_module, test_dir=test_dir,
                      build_dir=build / "sim", results_xml=str(build / "results.xml"),
                      plusargs=plusargs or [])
    total, failed = get_results(Path(xml))
    assert total >= 1 and failed == 0, f"cocotb {test_module}: {failed}/{total} failed"
