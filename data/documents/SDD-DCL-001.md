# SDD-DCL-001 — Driver Digital Cluster Software Design

> SYNTHETIC LEARNING DOCUMENT. Programme SYNTH-LTV is fictional.

## 1. Why this unit runs Linux

The cluster renders gauges, telltales and maintenance pages on a 10-inch display at 60 frames
per second. A frame that is a few milliseconds late is a cosmetic glitch, not a hazard: the
vehicle still stops and steers. That is soft real-time, so a general-purpose Linux system is
acceptable. It brings a mature graphics stack, file systems for maintenance logs, and a large
pool of developers, none of which a small RTOS offers.

The cluster is not in any braking control path. It only reports what other units broadcast.

## 2. Software structure

| Process | Role |
|---|---|
| can-gateway | Reads the J1939 backbone through SocketCAN, decodes messages, publishes signals |
| hmi-renderer | Draws gauges and telltales from published signals |
| telltale-monitor | Watches safety-relevant signals and their freshness timers |
| maint-logger | Writes fault history to the maintenance partition |

The telltale-monitor process runs under the SCHED_FIFO real-time scheduling class so that
warning lamps are not delayed by heavy rendering load.

## 3. Brake-related displays

The cluster decodes EBC1 from the brake ECU (source address 11). It lights the amber ABS
telltale when the brake ECU requests the amber warning lamp, or when "ABS fully operational" is
false. If no EBC1 frame arrives for 300 ms, the cluster treats anti-lock braking as unavailable
and lights the same telltale with a "BRAKE DATA LOST" message. DM1 trouble codes from the brake
ECU are shown on the maintenance page.

## 4. Night operations

The cluster supports NVG-compatible display mode, with dimming and color filtering intended to
meet the night-vision compatibility levels in MIL-STD-3009. In blackout mode all non-essential
graphics are suppressed and only critical warnings, including the ABS telltale, remain visible
at minimum luminance.

## 5. Boot and update

The cluster must show critical telltales within 2 seconds of power-on. A small bare-metal
pre-boot stage lights the telltale panel before Linux has finished booting.
