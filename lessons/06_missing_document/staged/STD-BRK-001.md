# STD-BRK-001 — Brake Control ECU Software Test Description

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional.

| Document | Type | Revision | Subsystem |
|---|---|---|---|
| STD-BRK-001 | STD | A | SUB-BRK-ECU |

## 1. Test environment

All tests run on the brake hardware-in-the-loop (HIL) rig: the production brake ECU and
hydraulic modulator, a four-channel wheel-speed simulator, a pressure transducer on each brake
channel sampled at 10 kHz, and a J1939 bus analyser on the backbone. Road surface and vehicle
speed are simulated.

## TC-BRK-010 — Wheel-slip control loop period and jitter

Verifies: SRS-BRK-010.
Method: Test.
Steps: 1. Run the vehicle model at 60 km/h on a simulated 0.3 friction surface. 2. Apply 70 %
brake pedal for 20 s. 3. Record the control-loop start timestamps from the ECU debug output.
Pass criteria: loop period 5.0 ms with jitter of 0.5 ms or less over at least 10,000 consecutive
cycles.

## TC-BRK-011 — Pressure release time on incipient wheel lock-up

Verifies: SRS-BRK-011.
Method: Test.
Steps: 1. Run the vehicle model at 40 km/h on a simulated 0.2 friction (ice) surface.
2. Apply 80 % brake pedal. 3. Inject incipient lock-up on one wheel: wheel deceleration
greater than 1.5 g while the other three wheels track vehicle speed. 4. Measure the time from
the ECU's lock-up detection flag to a 20 % drop in that wheel's brake pressure.
5. Repeat 50 times on each of the four wheels.
Pass criteria: every one of the 200 releases completes within 15 ms; pressure on the three
unaffected wheels does not drop by more than 5 % during the release.

## TC-BRK-021 — Fault fallback to conventional braking

Verifies: SRS-BRK-021.
Method: Test.
Steps: 1. Brake at 50 % pedal at 30 km/h on dry surface. 2. Inject an internal fault (corrupt
the wheel-speed sensor 2 signal). 3. Measure the time from fault injection to the modulator
valves returning to their conventional-braking position.
Pass criteria: fallback within 20 ms; EBC1 "ABS fully operational" goes false and the amber
warning lamp request is set in the next EBC1 frame.

## TC-BRK-030 — EBC1 transmission period

Verifies: SRS-BRK-030.
Method: Test.
Steps: 1. Power the ECU with no braking for 10 minutes. 2. Capture all EBC1 frames (PGN 61441,
source address 11) with the bus analyser.
Pass criteria: every interval between consecutive EBC1 frames is 100 ms ± 10 ms.
