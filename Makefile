# mcuaccel top-level Makefile. Toolchains are project-local (scripts/bootstrap.sh).
SHELL := /bin/bash
ROOT  := $(abspath .)
export MCUACCEL_ROOT := $(ROOT)
export XDG_CONFIG_HOME := $(ROOT)/.cache/config
export XDG_CACHE_HOME := $(ROOT)/.cache
export PYTHONPATH := $(ROOT)/third_party/mcugrad:$(ROOT)/backend:$(ROOT)/model:$(PYTHONPATH)
PY := $(ROOT)/.venv/bin/python
ifeq ($(shell uname -s),Darwin)
CLT := /Library/Developer/CommandLineTools
ifneq ($(wildcard $(CLT)/usr/bin/clang++),)
export PATH := $(CLT)/usr/bin:$(PATH)
export SDKROOT := $(CLT)/SDKs/MacOSX.sdk
export CC := $(CLT)/usr/bin/clang
export CXX := $(CLT)/usr/bin/clang++
endif
endif
export PATH := $(ROOT)/.venv/bin:$(ROOT)/tools/xpack-riscv-none-elf-gcc-15.2.0-1/bin:$(ROOT)/tools/oss-cad-suite/bin:$(PATH)

.PHONY: bootstrap lint test test-slow synth macros paper reproduce clean-clone-check readme clean

RTL := $(shell find rtl -name '*.sv' 2>/dev/null | sort)
PICO := third_party/picorv32/picorv32.v

bootstrap:
	scripts/bootstrap.sh

# Verilator -Wall with every module as top, then a Yosys read/hierarchy check.
lint:
	@if [ -z "$(RTL)" ]; then echo "no RTL yet"; exit 0; fi; set -e; \
	for f in $(RTL); do m=$$(basename $$f .sv); \
	  verilator --lint-only -Wall --top-module $$m syn/lint.vlt $(PICO) $(RTL); done; \
	bash syn/lint_yosys.sh $(RTL) $(PICO)

# pytest exit 5 = no tests collected: not a failure.
test:
	$(PY) -m pytest -q -m "not slow" tb backend/tests || [ $$? -eq 5 ]

# Slow tests: pure-Python graph calibration (~40 min/model on an idle machine), model.c reproduction.
test-slow:
	$(PY) -m pytest -q -m slow backend/tests

synth:
	bash syn/synth.sh $(or $(DESIGN),accel) $(or $(P),8) $(or $(SEED),1)

macros:
	$(PY) experiments/make_deviations.py && $(PY) experiments/plot_m6.py && $(PY) experiments/make_macros.py

paper:
	mkdir -p paper/build && cd paper && tectonic -X compile main.tex --outdir build
	cp paper/build/main.pdf paper/main.pdf

# Full pipeline (hours; runtimes in README). REPRODUCE_QUICK=1 for a smoke run that restores results/ afterwards.
reproduce:
	scripts/reproduce.sh

clean-clone-check:
	scripts/clean_clone_check.sh

readme:
	$(PY) experiments/make_readme_tables.py && $(PY) experiments/make_final_report.py

clean:
	rm -rf build sim_build obj_dir paper/build

.PHONY: sim
sim:
	$(MAKE) -C sim
