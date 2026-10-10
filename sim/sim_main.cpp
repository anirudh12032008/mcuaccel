// Verilator harness for soc_top (DESIGN 4.4).
// Plusargs: +ram=<hex> (read by soc_ram), +max_cycles=<n>, +vcd=<file>.
// max_cycles default 2e10. Exit: 0 tohost==1, 1 tohost!=1, 3 cpu_trap, 4 cpu_bus_err, 5 max_cycles.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include "Vsoc_top.h"
#include "verilated.h"
#if VM_TRACE
#include "verilated_vcd_c.h"
#endif

static uint64_t g_time = 0;
double sc_time_stamp() { return (double)g_time; }

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    uint64_t max_cycles = 20000000000ull;
    std::string vcd;
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "+max_cycles=", 12)) max_cycles = strtoull(argv[i] + 12, nullptr, 0);
        else if (!strncmp(argv[i], "+vcd=", 5)) vcd = argv[i] + 5;
    }
    setvbuf(stdout, nullptr, _IONBF, 0);
    Vsoc_top *top = new Vsoc_top;
#if VM_TRACE
    VerilatedVcdC *tfp = nullptr;
    if (!vcd.empty()) {
        Verilated::traceEverOn(true);
        tfp = new VerilatedVcdC;
        top->trace(tfp, 99);
        tfp->open(vcd.c_str());
    }
#else
    if (!vcd.empty()) fprintf(stderr, "warning: built without tracing (make sim TRACE=1); +vcd ignored\n");
#endif
    auto tick = [&](int clk) {
        top->clk = clk;
        top->eval();
        g_time++;
#if VM_TRACE
        if (tfp) tfp->dump(g_time);
#endif
    };
    top->rst = 1;
    for (int i = 0; i < 8; i++) { tick(0); tick(1); }
    top->rst = 0;

    uint64_t cycles = 0;
    int rc = 5;
    while (cycles < max_cycles) {
        tick(0);
        tick(1);
        cycles++;
        if (top->putc_valid) putchar(top->putc_data);
        if (top->tohost_valid) { rc = (top->tohost_data == 1) ? 0 : 1; break; }
        if (top->cpu_trap) { rc = 3; break; }
        if (top->cpu_bus_err) { rc = 4; break; }
    }
    top->final();
#if VM_TRACE
    if (tfp) { tfp->close(); delete tfp; }
#endif
    printf("\nSIM_CYCLES=%llu\n", (unsigned long long)cycles);
    if (rc == 1) printf("SIM_TOHOST=0x%x\n", (unsigned)top->tohost_data);
    delete top;
    return rc;
}
