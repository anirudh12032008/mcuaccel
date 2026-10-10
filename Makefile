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

.PHONY: bootstrap lint test synth macros paper reproduce clean

RTL := $(shell find rtl -name '*.sv' 2>/dev/null | sort)

bootstrap:
	scripts/bootstrap.sh

# Verilator -Wall with every module as top, then a Yosys read/hierarchy check.
lint:
	@if [ -z "$(RTL)" ]; then echo "no RTL yet"; exit 0; fi; set -e; \
	for f in $(RTL); do m=$$(basename $$f .sv); \
	  verilator --lint-only -Wall --top-module $$m $(RTL); done; \
	bash syn/lint_yosys.sh $(RTL)

# pytest exit 5 = no tests collected: not a failure.
test:
	$(PY) -m pytest -q tb backend/tests || [ $$? -eq 5 ]

synth:
	@if [ -x syn/synth.sh ]; then bash syn/synth.sh; else echo "syn/synth.sh not present yet"; fi

macros:
	$(PY) experiments/make_macros.py

paper:
	mkdir -p paper/build && cd paper && tectonic -X compile main.tex --outdir build
	cp paper/build/main.pdf paper/main.pdf

reproduce: synth macros paper

clean:
	rm -rf build sim_build obj_dir paper/build
