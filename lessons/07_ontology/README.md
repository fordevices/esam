# Lesson 07: The ontology

The baseline says what *this* vehicle is made of. The ontology (`data/ontology/esam_ontology.yaml`) says what
*kinds* of things, links, values and documents may exist, and what each kind of subsystem must have. It's
SE-owned YAML, versioned next to the baseline. SE-facing explanation: `docs/ESAM_SE_User_Manual.md` §8–9.

```bash
python -m esamkit.ontology          # check the baseline against the ontology; exit 1 on errors
```

```
Ontology : data/ontology/esam_ontology.yaml v0.2: 9 classes, 6 relationships, 7 document types
Baseline : data/baseline/vehicle_decomposition.yaml

  0 errors, 0 warnings.
```

## What reads it (`esamkit/ontology.py`)

| Caller | Uses | Replaced |
|---|---|---|
| `check_baseline()` | classes, `id_pattern`, attribute types and value sets, document types, relationship properties | nothing: new. It reads the **raw** YAML because `esamkit/baseline.py` silently drops unknown attributes |
| `esamkit/coverage.py` → `doc_types_needed()` | `document_types.*.answers` + `synonyms`, matched at word starts | the hard-coded `DOC_TYPE_CUES` table |
| `esamkit/coverage.py` → `mentioned_subsystems()` | subsystem `name` + baseline `aliases` (declared as `text_list` in the ontology) | name-only matching |
| `cockpit/app.py` | `required_types()` for the Dashboard columns; `required_documents()` per subsystem from `rules` | the fixed `DOC_TYPES` list checked for every subsystem |

`default_ontology()` reads the file once per process. Restart the Cockpit after editing the ontology.

## Rules

```yaml
rules:
  required_documents:
    - { when: [software], require: [srs, sdd] }
    - { when: [software, safety_critical], require: [std] }
    - { when: [has_interfaces], require: [icd] }
```

Conditions are evaluated per subsystem: `software` = has an `os_class`; `safety_critical` = the attribute is
`true`; `has_interfaces` = it sends or receives an `INTERFACES_WITH` edge. SYNTH-LTV goes from 16 fixed
checks (11 "missing") to **9 required, 5 present, 4 missing, 2 of them on safety-critical subsystems**.

## Things to try

1. Misspell an attribute in the baseline (`safty_critical: true`) and run the checker: a warning, where the
   loader would have dropped it silently.
2. Set a document's `type: ssr` (the pre-MIL-STD-498 name): rejected, and the ID/type mismatch is reported.
3. Ask `python lessons/05_hybrid/hybrid.py "Where is the QTP for SRS-BRK-011?"`: step 3b finds no document
   type. Add `"QTP"` (qualification test procedure) to `document_types.std.synonyms`, ask again, and step 3b
   now needs an STD and reports it missing. Vocabulary changed, no code changed.
4. Add a rule `{ when: [safety_critical], require: [ssdd] }`, restart the Cockpit, and watch the Dashboard
   gain an SSDD column with the backbone and brake ECU missing it.

`tests/test_ontology.py` and `tests/test_coverage.py` cover each of these.
