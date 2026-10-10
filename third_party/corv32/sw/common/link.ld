/* corv32 bare-metal link script: RAM at 0x8000_0000, tohost in its own 8-byte-aligned section. */
ENTRY(_start)
MEMORY { RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 64K }
SECTIONS
{
  .text.init : { *(.text.init) } > RAM
  .text      : { *(.text .text.*) } > RAM
  .tohost    : ALIGN(8) { KEEP(*(.tohost)) } > RAM
  .rodata    : { *(.rodata .rodata.*) } > RAM
  .data      : { *(.data .data.*) *(.sdata .sdata.*) } > RAM
  .bss       : { *(.sbss .sbss.*) *(.bss .bss.*) *(COMMON) } > RAM
  . = ALIGN(16);
  _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
