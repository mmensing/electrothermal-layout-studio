/* SPDX-License-Identifier: MPL-2.0
 * Output-only adapter for locally built FastCap2; no solver modification.
 * Prints original unaveraged charge-row/voltage-column coefficients in SI. */
#include "mulGlobal.h"
extern void __real_mksCapDump(double**, int, double, Name**);
void __wrap_mksCapDump(double **capmat, int n, double relperm, Name **names) {
 int i,j;
 printf("\nETLAYOUT_FASTCAP_EPSILON0_F_PER_M %.17g\n",E_0);
 printf("ETLAYOUT_RAW_MAXWELL_BEGIN %d F\n",n);
 for(i=1;i<=n;i++) {
  printf("%s",getConductorName(i,names));
  for(j=1;j<=n;j++) printf(" %.17g",capmat[i][j]*FPIEPS*relperm);
  printf("\n");
 }
 printf("ETLAYOUT_RAW_MAXWELL_END\n"); fflush(stdout);
 __real_mksCapDump(capmat,n,relperm,names);
}
