/* SoC console map (DESIGN 4.2) and helpers shared by platform.c / main_bench.c. */
#ifndef PLATFORM_SOC_H
#define PLATFORM_SOC_H
#include <stdint.h>

#define SOC_PUTCHAR (*(volatile uint32_t *)0x10000000u)
#define SOC_TOHOST  (*(volatile uint32_t *)0x10000004u)

#define SOC_CLOCK_HZ 50000000u   /* nominal; cycles are the claimed unit */

/* Stack-paint window used for bench_stack_high_water_bytes() (set by main). */
extern void *plat_stack_lo, *plat_stack_hi;

#endif
