/* SoC smoke firmware: console, rdcycle, accelerator ID/CONFIG, START/DONE handshake. */
#include <stdint.h>

#define CON_PUTCHAR (*(volatile uint32_t *)0x10000000u)
#define CON_CYCLE_LO (*(volatile uint32_t *)0x10000008u)
#define ACC(off)    (*(volatile uint32_t *)(0x30000000u + (off)))
#define ACC_ID      ACC(0x00)
#define ACC_CONFIG  ACC(0x04)
#define ACC_CTRL    ACC(0x08)
#define ACC_STATUS  ACC(0x0C)
#define ACC_DESC    ACC(0x10)

static inline uint32_t rdcycle(void) {
  uint32_t c;
  __asm__ volatile("rdcycle %0" : "=r"(c));
  return c;
}

int main(void) {
  const char *s = "OK\n";
  for (; *s; s++) CON_PUTCHAR = (uint32_t)*s;

  uint32_t c0 = rdcycle();
  uint32_t c1 = rdcycle();
  if (!(c1 > c0)) return 1;
  uint32_t k0 = CON_CYCLE_LO;
  uint32_t k1 = CON_CYCLE_LO;
  uint32_t c2 = rdcycle();
  if (!(k1 > k0)) return 7;
  if (!(k0 + 200u > c0 && k0 < c2 && k1 < c2 + 8u)) return 8; /* console counter tracks rdcycle */

  if (ACC_ID != 0x4D434131u) return 2;
  if ((ACC_CONFIG & 0xFFu) != 8u) return 3;

  ACC_DESC = 0x80001000u;
  if (ACC_DESC != 0x80001000u) return 4;
  ACC_CTRL = 1u; /* START */
  uint32_t st = 0;
  for (int i = 0; i < 1000 && !(st & 2u); i++) st = ACC_STATUS;
  if (!(st & 2u)) return 5; /* never DONE */
  if (st & 4u) return 6;    /* ERR */
  return 0;
}
