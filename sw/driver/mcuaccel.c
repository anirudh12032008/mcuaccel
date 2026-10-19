/* mcuaccel driver implementation (DESIGN 6.5). */
#include "mcuaccel.h"
#ifdef MCUACCEL_STAMPS
#include "platform.h"
#endif

#define REG(off)    (*(volatile uint32_t *)(uintptr_t)(MCUACCEL_BASE + (off)))
#define TOHOST      (*(volatile uint32_t *)(uintptr_t)MCUACCEL_CONSOLE_TOHOST)
#define BARRIER()   __asm__ volatile("" ::: "memory")

static void fail(void)
{
    TOHOST = MCUACCEL_FAIL_CODE;
    for (;;) {
    }
}

void mcuaccel_check_p(uint32_t p)
{
    if ((REG(MCUACCEL_CONFIG) & 0xFFu) != p) {
        fail();
    }
}

#ifdef MCUACCEL_STAMPS
/* C4(a)/C5 instrumentation (build with -DMCUACCEL_STAMPS; never in a timed build): per chain call,
 * the CYCLES register and the STAMP word of every descriptor of the chain (walked via NEXT). */
#define SL_CHAINS 512u
#define SL_STAMPS 4096u
static uint32_t sl_cycles[SL_CHAINS], sl_ncmd[SL_CHAINS], sl_stamp[SL_STAMPS];
static uint32_t sl_n, sl_s;

static void stamps_record(volatile mcuaccel_desc_t *d)
{
    uint32_t k = 0;
    if (sl_n >= SL_CHAINS) return;
    for (; d && sl_s + k < SL_STAMPS; d = (volatile mcuaccel_desc_t *)(uintptr_t)d->w[0]) sl_stamp[sl_s + k++] = d->w[17];
    sl_cycles[sl_n] = REG(MCUACCEL_CYCLES);
    sl_ncmd[sl_n++] = k;
    sl_s += k;
}

void mcuaccel_stamps_dump(void)
{
    uint32_t j = 0;
    for (uint32_t c = 0; c < sl_n; c++) {
        bench_print("MCUACCEL_STAMPS chain=%u cycles=%u ncmd=%u :", (unsigned)c, (unsigned)sl_cycles[c], (unsigned)sl_ncmd[c]);
        for (uint32_t k = 0; k < sl_ncmd[c]; k++) bench_print(" %u", (unsigned)sl_stamp[j++]);
        bench_print("\n");
    }
    bench_print("MCUACCEL_STAMPS_DONE n=%u\n", (unsigned)sl_n);
}
#endif

void mcuaccel_run(volatile mcuaccel_desc_t *first)
{
    uint32_t st;

    BARRIER();   /* descriptor patches are plain stores: nothing may move across the start */
    REG(MCUACCEL_DESC_ADDR) = (uint32_t)(uintptr_t)first;
    REG(MCUACCEL_CTRL) = MCUACCEL_CTRL_START;
    do {
        st = REG(MCUACCEL_STATUS);
    } while (st & MCUACCEL_STATUS_BUSY);
    BARRIER();   /* no tensor value cached across the chain */
    if (st & MCUACCEL_STATUS_ERR) {
        fail();
    }
#ifdef MCUACCEL_STAMPS
    stamps_record(first);
#endif
}

uint32_t mcuaccel_read_cycles(void) { return REG(MCUACCEL_CYCLES); }

uint32_t mcuaccel_ncmd(void) { return REG(MCUACCEL_NCMD); }
