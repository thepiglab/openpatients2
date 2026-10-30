# Local official FHIR R4 validation smoke — October 30, 2026

The deterministic compiler was tested with the official HL7 validator, in
addition to the repository's schema/reference tests. These are compiler smoke
tests, not results from a live Beam run and not clinical correctness measurements.

| Input | Resources | Official errors/fatal | Warnings | Result |
| --- | ---: | ---: | ---: | --- |
| First real patient from `tests/fixtures/unified_scoring_reference.jsonl` | 36 | 0 | 78 | Passed |
| Synthetic, source-cited sodium series point and patient/source/provenance resources | 6 | 0 | 8 | Passed |

An initial run found **64 errors** in the real reference Bundle because custom
provenance extensions lacked StructureDefinitions. Adding five packaged
definitions and loading them with `-ig` resolved all 64. This was a real issue
that the JSON Schema checks alone did not detect.

The 78 remaining real-reference warnings are 36 missing resource narratives, 21
missing Observation performers and 21 missing effective dates. These are official
best-practice recommendations, not errors. The compiler does not invent a
clinician or calendar timestamp to silence them. Information messages remain in
the OperationOutcome. No terminology server was used (`-tx n/a`); clinical
equivalence, ICD-10-CM coding/sequence rules and implementation-guide conformance
remain separate gates.

Validator: [HL7 FHIR validator 7.0.1](https://github.com/hapifhir/org.hl7.fhir.core/releases/tag/7.0.1),
build `d8783284409e`, Java 19.0.1, 2 GiB heap. JAR SHA-256:
`1178b6a3a3fe98d9cd9545cadee505274967332466001d857e4d8bed7138a5bc`.
Downloads and validation artifacts were stored in `/tmp`, not installed globally
or sent to an external validation service.

Equivalent command, with an existing JAR and a chosen new outcome path:

```bash
java -Xmx2g -jar validator_cli.jar bundle.json \
  -version 4.0.1 -tx n/a \
  -ig src/openpatients2/rules/fhir-extensions \
  -output operation-outcome.json
```

The validator reported these definition packages: `hl7.fhir.r4.core#4.0.1`,
`hl7.fhir.xver-extensions#0.1.0`, `hl7.terminology.r4#6.2.0`,
`hl7.fhir.uv.extensions.r4#5.2.0`, `hl7.terminology#7.5.0`,
`hl7.terminology.r5#7.5.0`, `hl7.fhir.uv.extensions#5.4.0`. Validator CLI defaults
can resolve newer auxiliary packages later; campaign receipts therefore retain
the command and logs, as well as the JAR hash. This is a version-recorded local
smoke, not a hermetically pinned validator environment.

| Artifact | SHA-256 |
| --- | --- |
| Real-reference Bundle | `6091c42fe18eced82fc13271c483f7c227ce414135bd8b43bad339e1cd08a7b4` |
| Real-reference OperationOutcome | `e1ac0c6cb119fffcc740535d00e73937904dcd195c31e72b27e9fa219ef75ead` |
| Measurement Bundle | `ed3880e24ac7eea47dee16b81622e8e671dcd20d954a84c972eef9e970068dc6` |
| Measurement OperationOutcome | `55cd7dbfdcdfd2d3976f58f7a5546b5f39f8f3e58c1ae7c9812969f33ddec9d7` |

The offline test suite covers candidate membership, duplicate/missing decisions,
real FY2027 release pins, immutable source packets, no self-approval, native Beam
request settings and returned reasoning/token capture, per-patient linkage,
negation/plans, exact numeric-series parsing, literal cells, and unresolved
ownership/dose deferral. Mock endpoint tests incur no model or API charges.
