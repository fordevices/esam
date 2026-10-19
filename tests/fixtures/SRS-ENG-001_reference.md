# SRS-ENG-001 — Engine Control Module Software Requirements

> TEST FIXTURE: a hand-written reference draft that passes the SRS lint. SYNTH-LTV is fictional.

| Document | Type | Revision | Subsystem |
|---|---|---|---|
| SRS-ENG-001 | SRS | A | SUB-ENG-ECM |

## 1 Scope

### 1.1 Identification

Engine Control Module (ECM) software, subsystem SUB-ENG-ECM, J1939 source address 0.

### 1.2 System overview

The ECM controls the engine of the SYNTH-LTV ground vehicle and exchanges J1939 data with the brake ECU over the J1939 backbone.

### 1.3 Document overview

This SRS specifies the ECM software requirements. No security or privacy considerations.

## 2 Referenced documents

ICD-ENG-001 rev A, Engine Control Module J1939 Interface Control Document. MIL-STD-882E (2012). DI-IPSC-81433A (1999).

## 3 Requirements

### 3.1 Required states and modes

No states or modes are required.

### 3.2 CSCI capability requirements

- **SRS-ENG-010** While ASR engine control active is 01 in EBC1, the software shall limit engine torque to the level requested for traction control.
  Safety: SwCI 3 | Verification: T | Traces to: SSF-ENG-02, ICD-ENG-001 §2.1 | Basis: MIL-STD-882E 4.4.2

### 3.3 CSCI external interface requirements

#### 3.3.1 Interface identification and diagrams

EBC1 (PGN 61441) is received from the brake ECU; EEC1 (PGN 61444) is transmitted. See ICD-ENG-001.

- **SRS-ENG-030** The software shall transmit EEC1 (PGN 61444) every 10 ms.
  Timing: 10 ms period | Verification: T | Traces to: ICD-ENG-001 §3.1 | Basis: DI-IPSC-81433A 3.3.xe

### 3.4 CSCI internal interface requirements

All internal interfaces are left to the design.

### 3.5 CSCI internal data requirements

All decisions about internal data are left to the design.

### 3.6 Adaptation requirements

No requirements.

### 3.7 Safety requirements

- **SRS-ENG-020** While ABS active is 01 in EBC1, the software shall inhibit the engine compression brake within 20 ms.
  Safety: SwCI 2 | Timing: 20 ms max | Verification: T | Traces to: SSF-ENG-01, ICD-ENG-001 §2.1 | Basis: MIL-STD-882E 4.4.2, DI-IPSC-81433A 3.7
- **SRS-ENG-021** If no EBC1 frame is received for 300 ms, the software shall set a lost-communication trouble code in DM1.
  Safety: SwCI 2 | Timing: 300 ms timeout | Verification: T | Traces to: SSF-ENG-03, ICD-ENG-001 §2.1 | Basis: MIL-STD-882E B.2.2.5a

### 3.8 Security and privacy requirements

No requirements.

### 3.9 CSCI environment requirements

No requirements.

### 3.10 Computer resource requirements

#### 3.10.1 Computer hardware requirements

No requirements.

#### 3.10.2 Computer hardware resource utilization requirements

No requirements.

#### 3.10.3 Computer software requirements

No requirements.

#### 3.10.4 Computer communications requirements

No requirements.

### 3.11 Software quality factors

No requirements.

### 3.12 Design and implementation constraints

No requirements.

### 3.13 Personnel-related requirements

No requirements.

### 3.14 Training-related requirements

This paragraph has been tailored out.

### 3.15 Logistics-related requirements

This paragraph has been tailored out.

### 3.16 Other requirements

No requirements.

### 3.17 Packaging requirements

This paragraph has been tailored out.

### 3.18 Precedence and criticality of requirements

SRS-ENG-020 and SRS-ENG-021 are SwCI 2 and take precedence; SRS-ENG-010 is SwCI 3.

## 4 Qualification provisions

Each requirement is annotated with its method: I inspection, A analysis, D demonstration, T test.

## 5 Requirements traceability

Each requirement is annotated with Traces to (5a); the compliance matrix gives 5b.

## 6 Notes

ASR: anti-slip regulation. DM1: J1939 active diagnostic trouble codes. SwCI: software criticality index.
