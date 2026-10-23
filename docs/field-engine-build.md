# Optional field engines: reproducible local builds

ETLayout does not bundle FastCap2 or FastHenry. They are optional, explicitly
selected local programs. Source-only and frozen ETLayout releases must not imply
that an independently downloaded field engine has been reviewed for redistribution.
The application runs generated inputs in a disposable directory with a deadline,
output limit, one simultaneous field solve and a 1 GiB memory limit. On Windows,
a kill-on-close Job Object also imposes a hard process-tree commit limit.

## FastCap2 raw capacitance output

The tested source is WRCad `fastcap-2.0wr-010524`, pinned in
[xictools](https://github.com/wrcad/xictools/tree/c1244c23a99118843db48cee9860218befe98753/fastcap).
Download the [exact upstream source archive](https://raw.githubusercontent.com/wrcad/xictools/c1244c23a99118843db48cee9860218befe98753/fastcap/fastcap-2.0wr-010524.tar.gz).
Its SHA-256 is:

```text
4789942cfdc5e7b5d1e5e28fee55a2fe54adcebd725cba42ddf7a60e8d60df2b
```

Inspect the archive before extracting it into a new private build directory.
The reviewed archive contains 139 entries, approximately 16.8 MB expanded, with
no path traversal or links. Preserve its README, README.mit and every source
notice. The current source includes MIT's 2003 permission grant with its actual
conditions and disclaimer; old mirrors may contain an earlier restrictive grant.
Do not substitute a generic license text or assume every mirror has this grant.
Upstream README SHA-256 is
`cb0badbd1b05140be9435e20abf9df168b57425141709eb3367ee4933a784cbd`;
README.mit is
`5b39f839e813402359992a4a4372ff933566d752b31e3b1dfd020242e3f292c5`.

FastCap2's ordinary `mksCapDump` display averages its off-diagonal matrix pairs.
That table cannot establish whether the original calculation was reciprocal.
ETLayout therefore requires the original output-only
[`fastcap_raw_output.c`](../tools/fastcap_raw_output.c) link wrapper. It prints
unmodified native charge-row/voltage-column coefficients in farads at 17
significant digits **before** calling the normal display routine. It does not
change assembly, meshing, GMRES, convergence criteria or the raw matrix. The
wrapper is original MPL-2.0 glue; FastCap2 retains its own upstream terms.

A GNU C compiler and GNU-compatible linker supporting `--wrap` are required.
The tested native Windows build used x64 MinGW-w64 GCC with the upstream MinGW
configuration flags below. MSVC does not implement this linker option. From the
unmodified source `src/` directory, compile the 24 objects listed by `OBJS` in
`Makefile`, then the wrapper, into a separate build directory:

```text
gcc -std=gnu99 -O2 -DFOUR -DNO_RUSAGE -DNO_GETHOSTNAME -I<source/src> -c <source.c> -o <build/source.o>
gcc -std=gnu99 -O2 -DFOUR -DNO_RUSAGE -DNO_GETHOSTNAME -I<source/src> -c <etlayout/tools/fastcap_raw_output.c> -o <build/raw_output.o>
gcc -o <build/fastcap-etlayout.exe> <all 24 engine objects> <build/raw_output.o> -Wl,--wrap=mksCapDump -lm
```

Record the downloaded archive, every original source, compiler, wrapper and final
executable SHA-256, exact compile/link argument arrays, compiler version and
license notices in the local build receipt. Keep these beside the local tool.
Do not add the archive, toolchain, object files or executable to ETLayout's tree.
Independent builds can have different executable hashes; reports retain the hash
actually executed.

The generated run uses SI coordinates, `-p<relative_permittivity> -o6 -t1e-10`.
The explicit direct-interaction option adds `-d0` and is limited to 2,000 generated
panels. It is considerably slower; it changes the algorithm, not acceptance
criteria. Mesh/panel refinement is controlled by ETLayout's actual closed
surfaces, not a claim that a layout file alone is a verified simulation.

The native source constant is `epsilon0 = 8.854187818e-12 F/m`, approximately
5.9e-10 relative from ETLayout's documented reference constant. The wrapper
reports it and the accepted operator records the coefficient actually used.
The protocol is:

```text
ETLAYOUT_FASTCAP_EPSILON0_F_PER_M 8.854187818e-12
ETLAYOUT_RAW_MAXWELL_BEGIN N F
c0000%GROUP1 <N original SI coefficients>
...
ETLAYOUT_RAW_MAXWELL_END
```

A stock engine, missing rows, nonconvergence, changed axes or invalid constants
are rejected. A completed raw matrix, per-entry mesh sensitivity and strict
passive-operator acceptance are separate states. In particular, multipole
approximation or collocation error can leave a reciprocal-operator check
unestablished even when all linear systems converge. ETLayout preserves that
raw result and its measured defect; it never silently averages it into a pass.

Run the maintained independent checks using the actual selected executable:

```powershell
python scripts/benchmark_field_capacitance.py --executable C:/tools/fastcap-etlayout.exe --output C:/results/capacitance-check
```

This opt-in command executes real source-to-field calculations and writes its
inputs, raw logs, executable/source identities, resource measurements and checks
to a new directory. Ordinary CI covers geometry, parsing, acceptance, budgets
and failed jobs without substituting a successful fake engine.

## FastHenry

The matching reviewed source is WRCad `fasthenry-3.0wr-031424` at the same pinned
xictools revision. Its exact archive SHA-256 is
`6da40d0e31425bca85be46434b33ecc194205d705b47f4459d91568c9f4301ef`.
Follow its own source instructions and retain its MIT and Sparse notices.
Use a separate build directory and record equivalent source/compiler/executable
receipts. ETLayout requires explicit finite closed returns and bounded conductor
filaments; a DC resistance port list is not an inductance loop definition.
See [parasitic extraction](parasitic-extraction.md) for the supported model and
verification limits. No local engine build authorizes public binary publication.
