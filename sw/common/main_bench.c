/* main(): run mcugrad's benchmark loop; TOHOST = 1 (pass) is written by crt0 from main's return. */
#include <stdint.h>
#include "platform.h"
#include "platform_soc.h"

#define STACK_WINDOW 16384u

#ifdef MCUACCEL_PROFILE
#include "profile.h"
#endif

int main(void)
{
    /* paint the stack below this frame so bench_stack_high_water_bytes() can measure depth */
    char *hi = (char *)__builtin_frame_address(0) - 128;
    plat_stack_hi = (void *)((uintptr_t)hi & ~3u);
    plat_stack_lo = (char *)plat_stack_hi - STACK_WINDOW;
    bench_stack_paint(plat_stack_lo, plat_stack_hi);

    mcugrad_bench_run();
#ifdef MCUACCEL_PROFILE
    prof_dump();
#endif
    return 0;
}
