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
/* Per-inference history (index 0 = the untimed warm-up) for the C4(b) per-image scoring and the
 * baseline-vs-accelerated fairness check; prof_inf is bumped at the top of model_run. */
#ifndef PROF_MAX_INF
#define PROF_MAX_INF 32
#endif
#define PROF_NMAX 48
extern uint32_t prof_hist[PROF_MAX_INF][PROF_NMAX];
extern uint32_t prof_inf;
/* every bench_cycles() call (bench_main: c0, micros, micros, c1 per image), see platform.c */
#define PROF_CLOG_MAX 512
extern uint32_t prof_clog[PROF_CLOG_MAX];
extern uint32_t prof_clog_n;

#define MCUACCEL_PROF_INF_BEGIN() (prof_inf++)
#define MCUACCEL_PROF_BEGIN(k) uint32_t prof_t##k = prof_rdcycle()
#define MCUACCEL_PROF_END(k)                                                     \
    do {                                                                         \
        uint32_t prof_d##k = (uint32_t)(prof_rdcycle() - prof_t##k);             \
        prof_cycles[k] += prof_d##k;                                             \
        if (prof_inf - 1u < PROF_MAX_INF) prof_hist[prof_inf - 1u][k] = prof_d##k; \
    } while (0)

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
    for (uint32_t j = 0; j < prof_inf && j < PROF_MAX_INF; j++) {
        bench_print("MCUGRAD_PROFH inf=%u", (unsigned)j);
        for (int k = 0; k < prof_n; k++) bench_print(" %u", (unsigned)prof_hist[j][k]);
        bench_print("\n");
    }
    bench_print("MCUGRAD_PROFC n=%u", (unsigned)prof_clog_n);
    for (uint32_t j = 0; j < prof_clog_n && j < PROF_CLOG_MAX; j++) bench_print(" %u", (unsigned)prof_clog[j]);
    bench_print("\n");
    bench_print("MCUGRAD_PROF_DONE\n");
}
#endif
