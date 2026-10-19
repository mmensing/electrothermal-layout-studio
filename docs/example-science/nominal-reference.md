# Exact nominal observations: E1–E40

Source revision: `f592e82c29479b26682444ec5eb3fbc753e4de81`.

All forty configured nominals were calculated without substituting drives, loads, analysis mode or tolerances. These single-grid observations verify function and conservation; mesh/time verification and physical calibration remain unestablished. Values are rounded for reading; the [machine-readable matrix](nominal-reference.json) retains SI values, support, sources, model diagnostics and loss categories.

## Electrical service and disjoint losses

`I` and `V` use the result's declared total-current and resistance-terminal definitions; multiport examples require inspecting individual ports before treating this as a two-terminal device. Device loss is separate from conventional contact/via loss. Thermal-only or cooled pulse-end electrical quantities may be absent or zero.

| Example | I (A) | V (V) | Electrical (W) | Sheet Joule (W) | Device load (W) | Contact/via (W) | Feed (W) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E1 | 0.238095 | 0.02 | 0.0047619 | 0.0047619 | 0 | 0 | 0 |
| E2 | 0.1 | 0.00680432 | 0.000680432 | 0.000680432 | 0 | 0 | 0 |
| E3 | 0.3 | 0.00806815 | 0.00242045 | 0.00242045 | 0 | 0 | 0 |
| E4 | 0.2 | 0.0133165 | 0.0026633 | 0.00264759 | 0 | 1.57139e-05 | 0 |
| E5 | 2 | 0.00626326 | 0.0125265 | 0.0119972 | 0 | 0.000529329 | 0 |
| E6 | 0.001 | 0.431259 | 0.000431259 | 0.000415269 | 0 | 1.59899e-05 | 0 |
| E7 | 0.0125838 | 0.2 | 0.00251677 | 0.00242789 | 0 | 8.88789e-05 | 0 |
| E8 | 0.012507 | 0.2 | 0.0025014 | 0.0024136 | 0 | 8.77942e-05 | 0 |
| E9 | 0.00441233 | 1 | 0.00441233 | 0.00422074 | 0 | 0.000191589 | 0 |
| E10 | 0.4 | 0.070399 | 0.0281596 | 0.0281596 | 0 | 0 | 0 |
| E11 | 0.02 | 1.05484 | 0.0210968 | 0.0210968 | 0 | 0 | 0 |
| E12 | 0.3 | 0.0710065 | 0.0213019 | 0.0212128 | 0 | 8.91479e-05 | 0 |
| E13 | 0.75 | 0.0213654 | 0.0160241 | 0.0160241 | 0 | 0 | 0 |
| E14 | 0.000156188 | 0.1 | 1.56188e-05 | 1.56188e-05 | 0 | 0 | 0 |
| E15 | 0.00421168 | 0.4 | 0.00144145 | 0.00144145 | 0 | 0 | 0 |
| E16 | 0.00014546 | 0.1 | 1.4546e-05 | 1.44536e-05 | 0 | 9.24226e-08 | 0 |
| E17 | — | — | — | — | — | — | — |
| E18 | 0 | — | 0 | 0 | 0 | 0 | 0 |
| E19 | — | — | — | — | — | — | — |
| E20 | 10 | 0.10234 | 1.0234 | 0.795805 | 0 | 0.0249914 | 0.202609 |
| E21 | 5 | 0.744704 | 3.72352 | 3.39739 | 0 | 0.273404 | 0.0527311 |
| E22 | 10 | 0.733511 | 7.33511 | 6.5471 | 0 | 0.56987 | 0.218144 |
| E23 | 10 | 2.46015 | 24.6015 | 0.130621 | 24.1679 | 0.00250387 | 0.300453 |
| E24 | 10 | 2.01992 | 20.1992 | 0.0203981 | 19.8778 | 0.000885668 | 0.300052 |
| E25 | 0.05 | 0.00923431 | 0.000461716 | 0.000460256 | 0 | 0 | 1.45963e-06 |
| E26 | 0.5 | 0.0176745 | 0.00883724 | 0.0078302 | 0 | 0 | 0.00100705 |
| E27 | 0.001 | 0.02 | 3.50139e-05 | 3.25094e-05 | 0 | 2.50046e-06 | 4.0805e-09 |
| E28 | 0.3 | 0.00519374 | 0.00141763 | 0.00113368 | 0 | 0 | 0.000283947 |
| E29 | 1 | 0.00594347 | 0.00594347 | 0.00491813 | 0 | 0 | 0.00102534 |
| E30 | 0.3 | 0.03582 | 0.010746 | 0.0104377 | 0 | 0.000182647 | 0.000125652 |
| E31 | 0.00193798 | 0.4 | 0.000775191 | 0.000474322 | 0 | 0.00030086 | 9.52779e-09 |
| E32 | 0.3 | 0.761965 | 0.22859 | 0.219292 | 0 | 0.00892836 | 0.000369521 |
| E33 | 0.0003 | 0.0190086 | 5.70258e-06 | 5.51919e-06 | 0 | 0 | 1.83386e-07 |
| E34 | 0.00201 | 0.433802 | 0.000867609 | 0.000857983 | 0 | 0 | 9.62599e-06 |
| E35 | 0.1 | 0.0942994 | 0.00942994 | 0.00930722 | 0 | 0 | 0.000122715 |
| E36 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| E37 | 0.02 | 0.160681 | 0.00321362 | 0.00294752 | 0 | 0.000265236 | 8.63342e-07 |
| E38 | 0.5 | 0.256453 | 0.128112 | 0.117342 | 0 | 0.0103765 | 0.000392871 |
| E39 | 10 | 2.99828 | 29.9828 | 0.178702 | 29.5999 | 0.00409286 | 0.200173 |
| E40 | 5 | 0.0318673 | 0.159337 | 0.105395 | 0 | 0.00383123 | 0.0501106 |

## Thermal response and numerical scope

`Tfinal` is the displayed last frame. `Trun peak` uses all accepted transient steps; it is not inferred from a cooled final field. Rise uses the named/declared reservoir. Every entry remains mesh unverified; transient entries also remain time unverified. A dash is unavailable, not zero. Legacy nominals have no typed hard-temperature limit configured.

| Example | Tfinal (K) | Trun peak (K) | Peak rise (K) | Peak time (s) | Elements | Converged | Model observations |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| E1 | — | — | — | — | 606 | True | No observed declared-range violation; uncalibrated |
| E2 | — | — | — | — | 1523 | True | No observed declared-range violation; uncalibrated |
| E3 | — | — | — | — | 2054 | True | No observed declared-range violation; uncalibrated |
| E4 | — | — | — | — | 1643 | True | No observed declared-range violation; uncalibrated |
| E5 | — | — | — | — | 23904 | True | No observed declared-range violation; uncalibrated |
| E6 | — | — | — | — | 726 | True | No observed declared-range violation; uncalibrated |
| E7 | — | — | — | — | 13807 | True | No observed declared-range violation; uncalibrated |
| E8 | — | — | — | — | 13891 | True | No observed declared-range violation; uncalibrated |
| E9 | — | — | — | — | 1788 | True | No observed declared-range violation; uncalibrated |
| E10 | 312.165 | 312.165 | 12.1645 | — | 15456 | True | No observed declared-range violation; uncalibrated |
| E11 | 306.29 | 306.29 | 6.29022 | — | 10856 | True | No observed declared-range violation; uncalibrated |
| E12 | 302.976 | 302.976 | 2.97604 | — | 11858 | True | No observed declared-range violation; uncalibrated |
| E13 | 301.92 | 301.92 | 1.91982 | — | 14190 | True | No observed declared-range violation; uncalibrated |
| E14 | 300.446 | 300.446 | 0.445856 | — | 3274 | True | No observed declared-range violation; uncalibrated |
| E15 | 305.221 | 305.221 | 5.22122 | — | 9122 | True | No observed declared-range violation; uncalibrated |
| E16 | 300.017 | 300.017 | 0.01693 | — | 9798 | True | No observed declared-range violation; uncalibrated |
| E17 | 301.624 | 304.976 | 4.97592 | 0.0017 | 542 | True | No observed declared-range violation; uncalibrated |
| E18 | 301.435 | 323.592 | 23.5923 | 0.000295 | 606 | True | No observed declared-range violation; uncalibrated |
| E19 | 301.36 | 337.692 | 37.6921 | 0.00015 | 1838 | True | No observed declared-range violation; uncalibrated |
| E20 | 332.372 | 332.372 | 32.3718 | — | 25148 | True | No observed declared-range violation; uncalibrated |
| E21 | 313.552 | 313.552 | 13.5521 | — | 143354 | True | No observed declared-range violation; uncalibrated |
| E22 | 329.577 | 329.577 | 29.5768 | — | 373108 | True | No observed declared-range violation; uncalibrated |
| E23 | 364.694 | 364.694 | 64.6939 | — | 15574 | True | No observed declared-range violation; uncalibrated |
| E24 | 350.945 | 350.945 | 50.945 | — | 26542 | True | No observed declared-range violation; uncalibrated |
| E25 | — | — | — | — | 14136 | True | No observed declared-range violation; uncalibrated |
| E26 | 302.301 | 302.301 | 2.3007 | — | 15868 | True | No observed declared-range violation; uncalibrated |
| E27 | — | — | — | — | 13324 | True | No observed declared-range violation; uncalibrated |
| E28 | — | — | — | — | 15572 | True | No observed declared-range violation; uncalibrated |
| E29 | 300.16 | 300.16 | 0.159769 | — | 14504 | True | No observed declared-range violation; uncalibrated |
| E30 | 304.833 | 304.833 | 4.8326 | — | 37238 | True | No observed declared-range violation; uncalibrated |
| E31 | — | — | — | — | 18604 | True | No observed declared-range violation; uncalibrated |
| E32 | 311.703 | 311.703 | 11.7034 | — | 38350 | True | No observed declared-range violation; uncalibrated |
| E33 | 301.666 | 301.666 | 1.66638 | — | 21384 | True | No observed declared-range violation; uncalibrated |
| E34 | 300.268 | 300.268 | 0.267712 | — | 13798 | True | No observed declared-range violation; uncalibrated |
| E35 | 300.553 | 300.553 | 0.552611 | — | 3830 | True | No observed declared-range violation; uncalibrated |
| E36 | 300.245 | 302.272 | 2.27156 | 0.00032 | 3052 | True | No observed declared-range violation; uncalibrated |
| E37 | 300.223 | 300.223 | 0.222726 | — | 3254 | True | No observed declared-range violation; uncalibrated |
| E38 | 301.06 | 301.06 | 1.06009 | — | 40444 | True | No observed declared-range violation; uncalibrated |
| E39 | 381.857 | 381.857 | 81.8568 | — | 6486 | True | No observed declared-range violation; uncalibrated |
| E40 | 306.727 | 306.727 | 6.72705 | — | 10382 | True | No observed declared-range violation; uncalibrated |

The JSON records include ambient/initial temperatures, hotspot coordinates, thermal input/removal and transient energy; fixed support measurements, layer-resolved K/J/E ranges, geometry diagnostics, contact-transfer resolution screens and measured phases. Review those records before comparing raw maxima. Detailed archives and process-memory receipts are CI/local artifacts, not source data.

[Interpretation and reproduction](../example-science.md).
