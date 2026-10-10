/* Driver fixed-cost measurement (T1.0). Times with rdcycle, REPS repetitions each, medians:
 *   rd      empty rdcycle pair
 *   chain   mcuaccel_run of a 1-descriptor chain
 *   pN      N descriptor-field patches (volatile stores, uintptr_t arithmetic), then mcuaccel_run
 * On the walking-skeleton stub (START -> DONE next cycle, no descriptor fetch) the accelerator
 * time is ~1 cycle, so chain - rd is the pure driver cost. Output (console):
 *   MCUGRAD_RESULT drv_rdcycle=.. drv_chain=.. drv_patch=.. plus the raw medians and spreads. */
#include <stdint.h>
#include "mcuaccel.h"

#define REPS 100
#define PUTCHAR (*(volatile uint32_t *)0x10000000u)

static volatile mcuaccel_desc_t ad __attribute__((aligned(4)));
static const volatile int8_t *volatile g_in;    /* opaque to the optimiser, like a model_run arg */
static volatile int8_t *volatile g_out;
static int8_t buf_in[16], buf_out[16];

static inline uint32_t rdcycle(void)
{
    uint32_t c;
    __asm__ volatile("rdcycle %0" : "=r"(c));
    return c;
}

/* patch one descriptor word: field address by uintptr_t arithmetic, volatile store */
#define FIELD(k) (*(volatile uint32_t *)((uintptr_t)&ad + 4u * (k)))

static void put_str(const char *s)
{
    for (; *s; s++) PUTCHAR = (uint32_t)(unsigned char)*s;
}

static void put_num(uint32_t v)
{
    char b[12];
    int n = 0;
    do { b[n++] = (char)('0' + v % 10u); v /= 10u; } while (v);
    while (n) PUTCHAR = (uint32_t)(unsigned char)b[--n];
}

static void sort(uint32_t *a, int n)   /* insertion sort */
{
    for (int i = 1; i < n; i++) {
        uint32_t t = a[i];
        int j = i;
        while (j > 0 && a[j - 1] > t) { a[j] = a[j - 1]; j--; }
        a[j] = t;
    }
}

static void report(const char *name, uint32_t *a)
{
    sort(a, REPS);
    put_str("MCUGRAD_RESULT drv_"); put_str(name); put_str("_med="); put_num(a[REPS / 2]);
    put_str("\nMCUGRAD_RESULT drv_"); put_str(name); put_str("_min="); put_num(a[0]);
    put_str("\nMCUGRAD_RESULT drv_"); put_str(name); put_str("_max="); put_num(a[REPS - 1]);
    put_str("\n");
}

int main(void)
{
    static uint32_t t_rd[REPS], t_ch[REPS], t_p1[REPS], t_p2[REPS], t_p5[REPS];
    uint32_t c0, c1;
    int i;

    g_in = (const volatile int8_t *)buf_in;
    g_out = (volatile int8_t *)buf_out;
    for (i = 0; i < 18; i++) ad.w[i] = 0;   /* NEXT = 0: one-descriptor chain */

    for (i = 0; i < REPS; i++) {
        c0 = rdcycle(); c1 = rdcycle();
        t_rd[i] = c1 - c0;
    }
    for (i = 0; i < REPS; i++) {
        c0 = rdcycle();
        mcuaccel_run(&ad);
        c1 = rdcycle();
        t_ch[i] = c1 - c0;
    }
    for (i = 0; i < REPS; i++) {
        const volatile int8_t *in = g_in;
        volatile int8_t *out = g_out;
        c0 = rdcycle();
        FIELD(MCUACCEL_W_X_ORIGIN) = (uint32_t)((uintptr_t)in - 99u);
        mcuaccel_run(&ad);
        c1 = rdcycle();
        t_p1[i] = c1 - c0;
        c0 = rdcycle();
        FIELD(MCUACCEL_W_X_ORIGIN) = (uint32_t)((uintptr_t)in - 99u);
        FIELD(MCUACCEL_W_Y_BASE) = (uint32_t)((uintptr_t)out + 3u);
        mcuaccel_run(&ad);
        c1 = rdcycle();
        t_p2[i] = c1 - c0;
        c0 = rdcycle();
        FIELD(MCUACCEL_W_X_ORIGIN) = (uint32_t)((uintptr_t)in - 99u);
        FIELD(MCUACCEL_W_Y_BASE) = (uint32_t)((uintptr_t)out + 3u);
        FIELD(7u) = (uint32_t)((uintptr_t)in + 1u);
        FIELD(8u) = (uint32_t)((uintptr_t)out + 2u);
        FIELD(9u) = (uint32_t)((uintptr_t)in + 5u);
        mcuaccel_run(&ad);
        c1 = rdcycle();
        t_p5[i] = c1 - c0;
    }
    if (mcuaccel_ncmd() > 1u) return 1;   /* the stub never counts real commands */
    report("rdcycle", t_rd); report("chain", t_ch);
    report("p1", t_p1); report("p2", t_p2); report("p5", t_p5);
    /* summary keys (integers): chain net of the rdcycle pair; patch = slope p5 - p1 over 4 fields */
    put_str("MCUGRAD_RESULT drv_rdcycle="); put_num(t_rd[REPS / 2]);
    put_str("\nMCUGRAD_RESULT drv_chain="); put_num(t_ch[REPS / 2] - t_rd[REPS / 2]);
    put_str("\nMCUGRAD_RESULT drv_patch="); put_num((t_p5[REPS / 2] - t_p1[REPS / 2]) / 4u);
    put_str("\nMCUGRAD_DONE\n");
    return 0;
}
