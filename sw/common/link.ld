/* mcuaccel bare-metal link script: 1 MiB RAM at 0x8000_0000 (soc_top default), stack at the end.
   Accelerator guard (DESIGN 6.4): data sections stay >= 16 bytes from both RAM edges (the
   accelerator may read up to 6 bytes outside any x tensor). Code must start at the origin
   (PROGADDR_RESET) so .text is exempt: it never holds tensors. */
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 1M }
SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text      : { *(.text .text.*) } > RAM
  .rodata    : { _data_start = .; *(.rodata .rodata.*) } > RAM
  .data      : { _sdata = .; *(.data .data.*) *(.sdata .sdata.*) } > RAM
  .bss       : { *(.sbss .sbss.*) *(.bss .bss.*) *(COMMON) } > RAM
  _ebss = .;   /* bench_static_ram_bytes(): _sdata.._ebss (platform.c) */
  . = ALIGN(16);
  _data_end = .;
  _stack_top = ORIGIN(RAM) + LENGTH(RAM) - 16;
}
ASSERT(_data_start >= ORIGIN(RAM) + 16, "data section within 16 bytes of RAM start")
ASSERT(_data_end + 16 <= ORIGIN(RAM) + LENGTH(RAM) - 16, "data/bss too close to RAM end")
