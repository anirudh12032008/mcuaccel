# Verilator harness for soc_top -> build/sim/Vsoc_top. Run via `make sim` at the repo root.
# `make sim P=4 OUT=$(pwd)/build/sim_P4` builds the accelerator at width P into its own dir (experiments/run_soc.py).
ROOT  := $(abspath ..)
P     ?= 8
OUT   ?= $(ROOT)/build/sim
SRCS  := $(shell find $(ROOT)/rtl/soc $(ROOT)/rtl/accel -name '*.sv' 2>/dev/null | grep -v soc_smoke_counter | sort)
TRACE ?= 0
VFLAGS := --cc --exe --build -O3 --x-assign fast --x-initial fast -Wno-fatal -Wno-lint -Wno-style \
          --top-module soc_top -GRAM_BYTES=1048576 -GP=$(P) -j 4 --Mdir $(OUT) -o Vsoc_top \
          $(if $(filter 1,$(TRACE)),--trace -CFLAGS -DVM_TRACE=1,) \
          -CFLAGS -O2

$(OUT)/Vsoc_top: sim_main.cpp $(SRCS) $(ROOT)/third_party/picorv32/picorv32.v
	@test -f $(ROOT)/rtl/soc/soc_top.sv || { echo "rtl/soc/soc_top.sv missing"; exit 1; }
	verilator $(VFLAGS) $(SRCS) $(ROOT)/third_party/picorv32/picorv32.v sim_main.cpp
