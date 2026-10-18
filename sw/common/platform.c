/* mcugrad platform.h hooks for the PicoRV32 SoC + the few libc functions bench_main/model.c need
 * (no libc is linked). No malloc, no float. */
#include <stddef.h>
#include <stdarg.h>
#include <stdint.h>
#include "platform.h"
#include "platform_soc.h"

extern char _sdata[], _ebss[];

void *plat_stack_lo, *plat_stack_hi;

#ifdef MCUACCEL_PROFILE
#include "profile.h"
uint32_t prof_clog[PROF_CLOG_MAX];
uint32_t prof_clog_n;
#endif

uint32_t bench_cycles(void)
{
    uint32_t c;
    __asm__ volatile("rdcycle %0" : "=r"(c));
#ifdef MCUACCEL_PROFILE
    if (prof_clog_n < PROF_CLOG_MAX) prof_clog[prof_clog_n++] = c;
#endif
    return c;
}

__attribute__((unused)) static uint64_t rdcycle64(void)
{
    uint32_t hi, lo, hi2;
    do {
        __asm__ volatile("rdcycleh %0" : "=r"(hi));
        __asm__ volatile("rdcycle %0" : "=r"(lo));
        __asm__ volatile("rdcycleh %0" : "=r"(hi2));
    } while (hi != hi2);
    return ((uint64_t)hi << 32) | lo;
}

uint32_t bench_clock_hz(void) { return SOC_CLOCK_HZ; }
/* 32-bit only (no 64-bit divide in the timed window): low cycle word / 50. The result wraps every
 * 2^32 cycles (85.9 s at 50 MHz); a us delta that straddles a wrap is wrong for that one image
 * (us_* only; cycles_* use bench_cycles() and are exact mod 2^32). */
uint64_t bench_micros(void)
{
    return bench_cycles() / (SOC_CLOCK_HZ / 1000000u);
}

size_t bench_static_ram_bytes(void) { return (size_t)(_ebss - _sdata); }
size_t bench_flash_bytes(void) { return 0; }   /* RAM-resident image; reported as SEE_IDF_SIZE by bench_main */

size_t bench_stack_high_water_bytes(void)
{
    if (!plat_stack_lo) return 0;
    size_t total = (size_t)((char *)plat_stack_hi - (char *)plat_stack_lo);
    return total - bench_stack_untouched(plat_stack_lo, plat_stack_hi);
}

static void putc_(char c) { SOC_PUTCHAR = (uint8_t)c; }

static void put_num(unsigned long v, unsigned base, int width, char pad, int neg)
{
    char buf[24];
    int n = 0;
    do { buf[n++] = "0123456789abcdef"[v % base]; v /= base; } while (v);
    if (neg) { if (pad == '0') { putc_('-'); width--; } else buf[n++] = '-'; }
    while (width-- > n) putc_(pad);
    while (n) putc_(buf[--n]);
}

static void vprint(const char *f, va_list ap)
{
    for (; *f; f++) {
        if (*f != '%') { putc_(*f); continue; }
        char pad = ' ';
        int width = 0, lng = 0;
        f++;
        if (*f == '0') { pad = '0'; f++; }
        while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
        while (*f == 'l') { lng = 1; f++; }
        switch (*f) {
        case 's': { const char *s = va_arg(ap, const char *); while (*s) putc_(*s++); break; }
        case 'c': putc_((char)va_arg(ap, int)); break;
        case '%': putc_('%'); break;
        case 'd': { long v = lng ? va_arg(ap, long) : va_arg(ap, int);
                    put_num(v < 0 ? 0ul - (unsigned long)v : (unsigned long)v, 10, width, pad, v < 0); break; }
        case 'u': put_num(va_arg(ap, unsigned long), 10, width, pad, 0); break;   /* long == int on ilp32 */
        case 'x': put_num(va_arg(ap, unsigned long), 16, width, pad, 0); break;
        default: putc_('?'); break;
        }
    }
}

void bench_print(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vprint(fmt, ap);
    va_end(ap);
}

/* --- minimal libc (freestanding; -fno-builtin so these are not recursed into) --- */
void *memcpy(void *d, const void *s, size_t n)
{
    char *dp = d; const char *sp = s;
    while (n--) *dp++ = *sp++;
    return d;
}

void *memmove(void *d, const void *s, size_t n)
{
    char *dp = d; const char *sp = s;
    if (dp < sp) while (n--) *dp++ = *sp++;
    else { dp += n; sp += n; while (n--) *--dp = *--sp; }
    return d;
}

void *memset(void *d, int c, size_t n)
{
    char *dp = d;
    while (n--) *dp++ = (char)c;
    return d;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *x = a, *y = b;
    for (; n; n--, x++, y++) if (*x != *y) return *x - *y;
    return 0;
}

/* insertion sort: n <= TESTSET_N (tens), stable, no recursion */
void qsort(void *base, size_t n, size_t sz, int (*cmp)(const void *, const void *))
{
    char *b = base, tmp[16];   /* element size <= 16 (bench_main sorts uint32_t) */
    for (size_t i = 1; i < n && sz <= sizeof tmp; i++) {
        size_t j = i;
        memcpy(tmp, b + i * sz, sz);
        while (j > 0 && cmp(b + (j - 1) * sz, tmp) > 0) { memcpy(b + j * sz, b + (j - 1) * sz, sz); j--; }
        memcpy(b + j * sz, tmp, sz);
    }
}
