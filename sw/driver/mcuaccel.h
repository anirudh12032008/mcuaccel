/* mcuaccel driver (DESIGN 6.1, 6.5). Pure C99, no libc. The CPU only polls while a chain runs. */
#ifndef MCUACCEL_H
#define MCUACCEL_H
#include <stdint.h>

#define MCUACCEL_BASE        0x30000000u

/* register offsets (DESIGN 6.1) */
#define MCUACCEL_ID          0x00u
#define MCUACCEL_CONFIG      0x04u
#define MCUACCEL_CTRL        0x08u
#define MCUACCEL_STATUS      0x0Cu
#define MCUACCEL_DESC_ADDR   0x10u
#define MCUACCEL_CYCLES      0x14u
#define MCUACCEL_NCMD        0x18u
#define MCUACCEL_ERR_ADDR    0x1Cu

#define MCUACCEL_CTRL_START  0x1u
#define MCUACCEL_CTRL_CLEAR  0x2u
#define MCUACCEL_STATUS_BUSY 0x1u
#define MCUACCEL_STATUS_DONE 0x2u
#define MCUACCEL_STATUS_ERR  0x4u

/* simulation console TOHOST (DESIGN 4.2) and the failure code written on an accelerator error */
#define MCUACCEL_CONSOLE_TOHOST 0x10000004u
#define MCUACCEL_FAIL_CODE      0xACCE1u

/* descriptor word indices (DESIGN 6.2) that the generated code patches per call */
#define MCUACCEL_W_X_ORIGIN  6u
#define MCUACCEL_W_Y_BASE    11u

/* Descriptor address fields in generated code go through this (a host test build overrides it). */
#ifndef MCUACCEL_ADDR
#define MCUACCEL_ADDR(p) ((uint32_t)(uintptr_t)(p))
#endif

typedef struct { uint32_t w[18]; } mcuaccel_desc_t;   /* 72 B, 4-byte aligned by the caller */

/* Start the chain at `first` and block until it ends. On ERR: writes MCUACCEL_FAIL_CODE to the
 * console TOHOST and never returns. */
void mcuaccel_run(volatile mcuaccel_desc_t *first);

/* Compare `p` with the P field (CONFIG[7:0]); on mismatch write MCUACCEL_FAIL_CODE to TOHOST and spin. */
void mcuaccel_check_p(uint32_t p);

/* CYCLES register: cycles BUSY was high during the last chain. */
uint32_t mcuaccel_read_cycles(void);
/* NCMD register: commands completed in the last chain. */
uint32_t mcuaccel_ncmd(void);

#endif
