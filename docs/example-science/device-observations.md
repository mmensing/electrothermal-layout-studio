# Calculated device operating cases

All default references remain unchanged. The [combined E1–E40 index](device-observations.json) links original and thermal observations without duplicating their full material/geometry registries. Voltage-biased GaN alternatives deliver different currents; these comparisons do not rank routing improvements at equal service.

| Case | Delivered (A) | Input (W) | Device load (W) | Peak (K) | Triangles |
| --- | ---: | ---: | ---: | ---: | ---: |
| E37:voltage_controlled | 0.02485938 | 0.004971876 | 0 | 300.34474 | 3254 |
| E38:gate_mismatch | 0.4575158 | 0.1143789 | 0 | 300.99387 | 40444 |
| E38:parallel_voltage | 0.4880248 | 0.1220062 | 0 | 301.00815 | 40444 |
| E38:source_asymmetry | 0.4843698 | 0.1210924 | 0 | 301.01541 | 40456 |
| E38:unequal_contact | 0.462185 | 0.1155462 | 0 | 300.99617 | 40444 |
| E39:continuous_reference | 2 | 0.9971258 | 0.9834569 | 302.72392 | 6486 |
| E39:semiconductor_heat | 2 | 0.9967179 | 0.9830521 | 302.58304 | 6486 |
| E39:stress_semiconductor_heat | 10 | 29.61811 | 29.23794 | 376.70931 | 6486 |
| E40:reference | 5 | 0.1593366 | 0 | 306.72707 | 10382 |

## Mesh sensitivity of selected metrics

All global, contact, minimum and explicit local/axis sizes scale together. The averaging support/length is fixed in physical units across levels. Differences below compare the last two levels; temperature means the regularized hotspot **rise above the named 300 K reservoir**. Raw maxima and physical calibration remain separate.

| Case | Triangles (coarse / medium / fine) | Current difference | Input-power difference | Averaged-rise difference |
| --- | ---: | ---: | ---: | ---: |
| E37:voltage_controlled | 3252 / 5018 / 7862 | 0.021% | 0.021% | 0.124% |
| E38:parallel_voltage | 40444 / 61256 / 96304 | 0.026% | 0.026% | 0.018% |
| E39:semiconductor_heat | 6486 / 9500 / 13824 | 0.000% | 0.001% | 0.006% |
| E40:reference | 10388 / 16442 / 25322 | 0.000% | 0.079% | 0.106% |

Saved-study points, exact source/configuration hashes, finite contacts, disjoint loss components, 2DEG versus bulk loading, conserved currents, timing and averaging support remain in the machine-readable observations. Each run is numerically converged; a selected-metric sensitivity observation does not change its independent verification badges.
