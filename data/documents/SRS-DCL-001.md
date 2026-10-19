# SRS-DCL-001 — Driver Digital Cluster Software Requirements

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional.

## 1. Display requirements

- **SRS-DCL-010** The software shall render primary gauges at no less than 30 frames per second
  under all operating conditions.
- **SRS-DCL-011** The software shall display critical telltales within 2 s of power-on.

## 2. Brake warning requirements

- **SRS-DCL-020** The software shall illuminate the amber ABS telltale within 200 ms of
  receiving an EBC1 frame requesting the amber warning lamp.
- **SRS-DCL-021** The software shall treat anti-lock braking as unavailable and illuminate the
  ABS telltale if no EBC1 frame is received for 300 ms.
- **SRS-DCL-022** The software shall display brake ECU DM1 trouble codes on the maintenance page.

## 3. Night operations requirements

- **SRS-DCL-030** The software shall provide an NVG-compatible display mode.
- **SRS-DCL-031** In blackout mode the software shall suppress all graphics except critical
  warnings.
