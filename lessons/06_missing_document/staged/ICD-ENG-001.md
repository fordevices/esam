# ICD-ENG-001 — Engine Control Module J1939 Interface Control Document

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional. The J1939 message names and
> PGNs are real; the behaviour below is simplified for teaching.

| Document | Type | Revision | Subsystem |
|---|---|---|---|
| ICD-ENG-001 | ICD | A | SUB-ENG-ECM |

## 1. Scope

This ICD defines the J1939 data the Engine Control Module (WBS 1.1.1.7, subsystem SUB-ENG-ECM,
source address 0) exchanges over the J1939 Vehicle Data Backbone (SUB-J1939-BB).

## 2. Messages received by the engine ECM

### 2.1 EBC1 — Electronic Brake Controller 1 (PGN 61441), from the brake ECU

The engine ECM consumes EBC1 from the Brake Control ECU (source address 11). It uses two
signals from byte 1:

- **ASR engine control active.** While this is 01 (active), the ECM reduces engine torque to the
  level requested for traction control, so driven wheels stop spinning on loose or slippery
  ground. This is the engine side of traction control.
- **ABS active.** While this is 01 (active), the ECM disables the engine compression brake, so
  engine braking cannot lock the driven wheels during an anti-lock stop.

If no EBC1 frame arrives within 300 ms, the ECM treats both signals as inactive (no torque
reduction, engine brake allowed) and logs a lost-communication trouble code in DM1.

## 3. Messages transmitted by the engine ECM

### 3.1 EEC1 — Electronic Engine Controller 1 (PGN 61444)

Broadcast every 10 ms: actual engine torque, engine speed and the torque mode currently
limiting the engine (including "ASR control" while traction control is reducing torque).
