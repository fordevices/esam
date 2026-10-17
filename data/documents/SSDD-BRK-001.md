# SSDD-BRK-001 — Brake Control ECU Software Design

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional.

## 1. Why this unit runs an RTOS

Wheel-slip regulation is a closed control loop: read four wheel-speed sensors, estimate slip,
command the hydraulic modulator valves. If the loop runs late, a wheel can lock before pressure
is released, and the vehicle loses steering under braking. A late answer is a wrong answer.
That is a hard real-time requirement, so the software runs on a deterministic real-time
operating system with fixed-priority preemptive scheduling, not on a general-purpose OS whose
worst-case latency cannot be bounded.

## 2. Task set

| Task | Period | Priority | Worst-case execution |
|---|---|---|---|
| Slip control loop | 5 ms | highest | 1.2 ms |
| Valve driver watchdog | 5 ms | high | 0.2 ms |
| Sensor plausibility | 10 ms | medium | 0.8 ms |
| J1939 transmit (EBC1, DM1) | 100 ms | low | 0.3 ms |
| Diagnostics and logging | 1000 ms | lowest | 4.0 ms |

Rate-monotonic analysis shows total utilisation below 45 %, leaving margin for growth.
Communication tasks run at low priority by design: a busy CAN bus must never delay the slip
loop.

## 3. Memory and partitioning

All memory is statically allocated at start-up. No heap allocation occurs after initialisation.
The slip control loop and valve drivers run in a protected partition; communication and
diagnostic code cannot write into it. A hardware watchdog resets the unit if the slip loop
misses two consecutive deadlines, after which the hydraulic system reverts to conventional
(non-ABS) braking.

## 4. Fail-safe behaviour

On any internal fault the ECU drops to conventional braking, sets "ABS fully operational" to
false in EBC1, requests the amber warning lamp, and logs a trouble code in DM1. The driver keeps
full service brakes; only anti-lock modulation is lost.

## 5. Power and environment

The unit operates from the 28 V vehicle bus per MIL-STD-1275 and must ride through cranking
dips and load-dump transients without resetting the control loop. EMI qualification follows
MIL-STD-461.
