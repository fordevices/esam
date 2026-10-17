# ICD-BRK-001 — Brake Control ECU J1939 Interface Control Document

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional. The J1939 message names and
> PGNs are real; the bit layout below is simplified for teaching. Verify against SAE J1939-71
> before any real use.

## 1. Scope

This ICD defines the data the Brake Control ECU (WBS 1.1.1.5, subsystem SUB-BRK-ECU) exchanges
with other electronic units over the J1939 Vehicle Data Backbone (WBS 1.1.1.6, SUB-J1939-BB).
The brake ECU is the sole producer of brake-system status on the backbone.

## 2. Physical layer

The backbone is a single 250 kbit/s CAN segment using 29-bit extended identifiers, per SAE
J1939-11. The brake ECU claims source address 11 (Brakes – System Controller). Connector,
termination and harness routing are defined in the vehicle wiring ICD and are not repeated here.

## 3. Messages transmitted by the brake ECU

### 3.1 EBC1 — Electronic Brake Controller 1 (PGN 61441)

EBC1 is broadcast every 100 ms at priority 6. It is the primary message other units use to learn
whether anti-lock braking is active, whether the brake system is healthy, and where the brake
pedal is.

| Byte | Signal | Notes |
|---|---|---|
| 1 | ASR engine control active, ASR brake control active, ABS active | 2-bit state fields |
| 2 | Brake pedal position | 0–100 %, 0.4 %/bit |
| 3 | ABS off-road switch, ASR off-road switch | crew-selectable terrain modes |
| 6 | ABS fully operational, amber warning lamp request | drives the driver's ABS telltale |

Consumers must treat a 2-bit value of 3 ("not available") as unknown, not as false.

### 3.2 DM1 — Active Diagnostic Trouble Codes (PGN 65226)

DM1 is sent once per second while any fault is active, and on change. It carries the
lamp status (malfunction, red stop, amber warning, protect) and the list of active trouble codes.

## 4. Timing and loss of communication

Receivers shall declare EBC1 lost if no frame arrives within 300 ms (three missed periods).
On loss, receivers must assume the anti-lock function is NOT available and inform the crew.
The brake ECU itself continues to brake normally; loss of the message affects only what other
units know about it.

## 5. Change control

Any change to EBC1 content, period or source address is a Class I interface change and requires
review by every consuming subsystem listed in the vehicle baseline.
