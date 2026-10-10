# Minimal C startup: set sp, call main, report (main_ret << 1) | 1 to the console TOHOST register
# (0x1000_0004; 1 = pass), spin.
  .section .text.init
  .globl _start
_start:
  la   sp, _stack_top
  call main
  slli a0, a0, 1
  ori  a0, a0, 1
  li   t0, 0x10000004
  sw   a0, 0(t0)
1: j    1b
