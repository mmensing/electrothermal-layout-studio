# Observed operating scenarios

Every E1–E40 default remains the [original reference](nominal-reference.md). The [complete comparison](operating-scenarios.json) joins all forty originals with their unchanged defaults and the seventeen optional calculated scenarios. Values below use the complete run peak, including retained hot states before pulse cooling.

| Example / scenario | Current at hot state (A) | Electrical power at hot state (W) | Peak (K) | Final (K) | Engineering result | Triangles |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| E11:joule_only | 0.02 | 0.0210534 | 305.74940 | 305.74940 | no chosen limit | 10856 |
| E11:thermal_demo | 0.03 | 0.0487081 | 313.31080 | 313.31080 | passed chosen limit | 10856 |
| E15:joule_only | 0.0042176 | 0.00144356 | 303.58227 | 303.58227 | no chosen limit | 9124 |
| E15:thermal_demo | 0.00835755 | 0.00571933 | 314.18789 | 314.18789 | passed chosen limit | 9124 |
| E16:joule_only | 0.000145469 | 1.45469e-05 | 300.00358 | 300.00358 | no chosen limit | 9798 |
| E18:thermal_limit_failure | 0.25 | 0.00573677 | 323.59226 | 301.43529 | expected failure | 608 |
| E18:thermal_stop | 0.25 | 0.00549405 | 311.82842 | 311.82842 | expected failure | 608 |
| E26:thermal_demo | 1.25 | 0.0568087 | 314.97126 | 314.97126 | passed chosen limit | 15868 |
| E26:thermal_limit_failure | 1.25 | 0.0568087 | 314.97126 | 314.97126 | expected failure | 15868 |
| E29:thermal_demo | 8 | 0.392201 | 310.60464 | 310.60464 | passed chosen limit | 14502 |
| E30:thermal_demo | 0.6 | 0.0444283 | 320.33797 | 320.33797 | passed chosen limit | 37252 |
| E33:thermal_demo | 0.0009 | 5.17366e-05 | 315.16579 | 315.16579 | passed chosen limit | 21384 |
| E34:thermal_demo | 0.01501 | 0.0495002 | 315.26819 | 315.26819 | passed chosen limit | 13798 |
| E35:thermal_demo | 0.5 | 0.245107 | 314.40042 | 314.40042 | passed chosen limit | 3830 |
| E36:thermal_demo | 2.5 | 0.126406 | 314.74642 | 301.57397 | passed chosen limit | 3052 |
| E36:thermal_limit_failure | 2.5 | 0.126407 | 314.74699 | 301.57387 | expected failure | 3054 |
| E36:thermal_stop | 2.5 | 0.125054 | 310.47855 | 310.47855 | expected failure | 3054 |

The failed-limit and early-stop rows are diagnostic failures, not accepted designs. A stop occurs at the first accepted violating state; it does not interpolate an exact crossing. Passive cooling after the pulse remains present in the full-duration rows.

## Three spatial levels

All electrical/material/thermal inputs stay fixed. All global, minimum, contact and explicit local refinement sizes are multiplied by 1, 0.8 and 0.65. The last-two differences below use temperature **rise relative to the named 300 K reservoir**, not percent of absolute temperature. These observations support the selected integral/thermal metrics; sharp-corner raw current maxima remain unverified.

| Example | Triangles (coarse / medium / fine) | Fine peak (K) | Last-two power / pulse-energy difference | Last-two rise difference |
| --- | ---: | ---: | ---: | ---: |
| E11 | 10856 / 17022 / 25776 | 313.31191 | 0.082% | 0.003% |
| E15 | 9122 / 14710 / 23106 | 314.12825 | 0.116% | 0.171% |
| E26 | 15866 / 24514 / 36530 | 314.99745 | 0.041% | 0.072% |
| E29 | 14500 / 23164 / 34700 | 310.61018 | 0.023% | 0.016% |
| E30 | 37254 / 58590 / 92158 | 320.35101 | 0.021% | 0.018% |
| E33 | 21390 / 34028 / 51362 | 315.19112 | 0.048% | 0.084% |
| E34 | 13798 / 22452 / 34222 | 315.46719 | 0.057% | 0.673% |
| E35 | 3830 / 5808 / 8266 | 315.02750 | 0.282% | 1.674% |
| E36 | 3054 / 4796 / 7094 | 314.79922 | 0.069% | 0.110% |

## One exact mesh, three time steps

E36 uses identical point/connectivity arrays at 10, 5 and 2.5 µs; each starts physically at 300 K and reaches 400 µs. This serial study took 79.50 s on the shared host. Independently remeshed preliminary time runs were excluded because they confounded spatial and temporal error.

| Step (µs) | Accepted states | Peak (K) | Peak time (µs) | Input energy (µJ) | Stored energy (µJ) | Absolute energy residual (nJ) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 40 | 314.74699 | 320 | 21.9242 | 19.6846 | 0.0923424 |
| 5 | 80 | 314.78183 | 320 | 21.6294 | 19.4715 | 0.202039 |
| 2.5 | 160 | 314.80592 | 320 | 21.5558 | 19.431 | 0.364903 |

The final two steps differ by about 0.16% in temperature rise and 0.34% in input energy. Numerical convergence, engineering-limit acceptance, mesh/time sensitivity and physical calibration are separate statements.
