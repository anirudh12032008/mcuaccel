/* Per-op cycle profiling (build with -DMCUACCEL_PROFILE and a model.c patched by sw/patch_profile.py).
 * The patched model.c defines prof_cycles[] (one slot per op call) and the name tables. */
#ifndef MCUACCEL_PROFILE_H
#define MCUACCEL_PROFILE_H
#include <stdint.h>
#include "platform.h"

extern uint64_t prof_cycles[];
extern const int prof_n;
extern const int prof_op[];
extern const char *const prof_name[];
extern const char *const prof_kernel[];

static inline uint32_t prof_rdcycle(void)
{
    uint32_t c;
    __asm__ volatile("rdcycle %0" : "=r"(c));
    return c;
}
#define MCUACCEL_PROF_BEGIN(k) uint32_t prof_t##k = prof_rdcycle()
#define MCUACCEL_PROF_END(k)   (prof_cycles[k] += (uint32_t)(prof_rdcycle() - prof_t##k))

/* total over warm-up + all timed inferences; the Python side divides */
static inline void prof_dump(void)
{
    for (int k = 0; k < prof_n; k++) {
        uint64_t v = prof_cycles[k];
        uint32_t q = (uint32_t)(v / 1000000000u), r = (uint32_t)(v % 1000000000u);
        bench_print("MCUGRAD_PROF op=%d name=%s kernel=%s cycles=", prof_op[k], prof_name[k], prof_kernel[k]);
        if (q) bench_print("%lu%09lu\n", (unsigned long)q, (unsigned long)r);
        else   bench_print("%lu\n", (unsigned long)r);
    }
    bench_print("MCUGRAD_PROF_DONE\n");
}
#endif
