# SSR-BRK-001 — Brake Control ECU Software Requirements

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional.

## 1. Control requirements

- **SRS-BRK-010** The software shall execute the wheel-slip control loop every 5 ms with jitter
  not exceeding 0.5 ms.
- **SRS-BRK-011** The software shall release brake pressure on a wheel within 15 ms of
  detecting incipient lock-up on that wheel.
- **SRS-BRK-012** The software shall support a crew-selectable off-road ABS mode that permits
  limited wheel lock to build a soil wedge on loose terrain.

## 2. Safety requirements

- **SRS-BRK-020** The software is assigned Software Criticality Index 1 under MIL-STD-882E and
  shall be developed to the corresponding level of rigor.
- **SRS-BRK-021** On detection of an internal fault, the software shall revert the hydraulic
  unit to conventional braking within 20 ms.
- **SRS-BRK-022** The software shall not allocate memory dynamically after initialisation.

## 3. Interface requirements

- **SRS-BRK-030** The software shall transmit EBC1 (PGN 61441) every 100 ms ± 10 ms as defined
  in ICD-BRK-001.
- **SRS-BRK-031** The software shall set the amber warning lamp request in EBC1 whenever
  anti-lock modulation is unavailable.
- **SRS-BRK-032** The software shall transmit DM1 within 1 s of any new active fault.
