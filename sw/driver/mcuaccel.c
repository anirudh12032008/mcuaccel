/* mcuaccel driver implementation (DESIGN 6.5). */
#include "mcuaccel.h"

#define REG(off)    (*(volatile uint32_t *)(uintptr_t)(MCUACCEL_BASE + (off)))
#define TOHOST      (*(volatile uint32_t *)(uintptr_t)MCUACCEL_CONSOLE_TOHOST)
#define BARRIER()   __asm__ volatile("" ::: "memory")

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
        TOHOST = MCUACCEL_FAIL_CODE;
        for (;;) {
        }
    }
}

uint32_t mcuaccel_read_cycles(void) { return REG(MCUACCEL_CYCLES); }

uint32_t mcuaccel_ncmd(void) { return REG(MCUACCEL_NCMD); }
