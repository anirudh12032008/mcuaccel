# Minimal C startup: set sp, call main, report main's return value via tohost (1 = pass), spin.
  .section .text.init
  .globl _start
_start:
  la   sp, _stack_top
  call main
  # tohost protocol: (code << 1) | 1 ; main()==0 -> pass (tohost == 1)
  slli a0, a0, 1
  ori  a0, a0, 1
  la   t0, tohost
  sw   a0, 0(t0)
1: j    1b

  .section .tohost, "aw", @progbits
  .align 3
  .globl tohost
tohost: .dword 0
  .globl fromhost
fromhost: .dword 0
