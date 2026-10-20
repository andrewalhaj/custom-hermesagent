# NASA SEH 2.0 Fundamentals of Systems Engineering — NPR 7123.1

Source: https://www.nasa.gov/reference/2-0-fundamentals-of-systems-engineering/ (NASA Systems Engineering Handbook, Chapter 2, updated 2025-01-09). Load this for NASA vocabulary (KDPs, Pre-Phase A–F, NPR 7123.1), the SE-engine framing, or a concise statement of the discipline's core rules.

## Definitions (§2.0)
- System: combination of elements functioning together to produce the capability required to meet a need — hardware, software, equipment, facilities, personnel, processes, procedures. The value "beyond that contributed independently by the parts" comes from how they are interconnected.
- Systems engineering: methodical, multi-disciplinary approach to design, realization, technical management, operations, retirement. Holistic and integrative — balances structural, electrical, mechanisms, power, human factors, etc. so no single discipline dominates. Contains life-cycle cost. "A logical way of thinking."
- The art: knowing when and where to probe. Goal is both "get the design right" (meet requirements) and "get the right design" (meet operational goals and stakeholder expectations).
- Role: title varies (lead SE, technical manager, chief engineer); on small projects the PM may do it; the functions must be performed regardless.
- SE leads: ConOps and resulting architecture, boundaries, requirements definition/allocation, design trade evaluation, risk balancing between systems, interface definition/assessment, V&V oversight, technical planning and most technical documentation.
- Project management triad (Fig 2.0-1): technical (SE) · team · cost & schedule (Project Planning & Control, PP&C). SE supplies technical inputs where SE and PP&C overlap; PP&C supplies programmatic/cost/schedule.

## The SE Engine — 17 common technical processes (§2.1, Fig 2.1-1)
- Three sets, from NPR 7123.1:
  - System Design (4): define/baseline stakeholder expectations → generate/baseline technical requirements → decompose into logical and behavioral models → convert to a design solution. Applied top-down to every product in the system structure until the lowest products can be built, bought, or reused.
  - Product Realization: create the solution for each product (buy, code, build, reuse) → verify → validate → transition up one level. Applied bottom-up, lowest product first.
  - Technical Management (processes 10–17): establish/evolve technical plans, manage communication across interfaces, assess progress vs plans and requirements, control execution, support decisions.
- Processes 1–9 = executing the project; 10–17 = crosscutting tools for carrying out 1–9.
- Iterative vs recursive (NPR 7123.1 definitions):
  - Iterative: application of a process to the same product(s) to correct a discovered discrepancy or variation from requirements.
  - Recursive: adding value by repeated application to design the next lower layer, to realize the next upper layer, or to the same structure in the next life-cycle phase.
- Decompose recursively/iteratively until detail is concrete enough to implement; then integrate recursively/iteratively upward until the whole is assembled, verified, validated, transitioned.
- AS9100 (aerospace QMS): some NASA Centers certify to it and may require contractors to follow NPR 7123.1; Table 2.1-1 maps the 17 processes to AS9100.
- Detailed worked example and poster-size life-cycle flow: NASA Expanded Guidance for SE at https://nen.nasa.gov/web/se/doc-repository (NASA-internal access).

## Engine by project phase (§2.2, Fig 2.2-1, Table 2.2-1)
- Phases: Pre-Phase A → A → B → C → D → E (operations) → F (closeout). Chart rows: system maturity + KDPs + major reviews; technical processes 1–9 per phase; technical management 10–17 per phase.
- Technical development processes cycle 5× from Pre-Phase A through D. Phases C and D each run half the engine ("split") for closer management control.
- Technical management processes cycle 7×, Pre-Phase A through F.
- Phase E/F work shifts to operations, sustaining engineering, closeout; new capabilities/upgrades re-enter the engine as new developments.

## Worked phase-by-phase use (§2.3)
- Pre-Phase A: initial concepts; define unique roles of humans, hardware, software; functional and performance boundaries; draft key high-level requirements; one or more initial ConOps scenarios; realize concepts via modeling, mock-ups, simulation; verify/validate that the concepts could meet the key requirements and ConOps.
  - ConOps must include all significant operational situations including known off-nominal, malfunction, and degraded-mode cases. Early ConOps "cannot be underestimated" — as requirements get detailed, stakeholders lose the picture; ConOps is the check for missing/conflicting requirements.
  - Not the formal V&V program — a methodical run-through proving feasibility. Develop concepts only to the level that proves feasibility and reduces risk enough; going to circuit-board level everywhere is time and money wasted.
- Phase A: recursive engine use; concepts + draft key requirements → baseline system requirements and ConOps. Simulate high-risk areas; identify V&V tools/techniques needed later.
- Phase B: mature requirements and designs across the product tree; V&V of concepts against requirements; evaluate operational designs/mission scenarios vs capabilities and cost.
- Phase C: left side of engine — finalize requirement updates, finalize ConOps validation, final designs to the lowest tier, begin fabrication.
- Phase D: right side — final implementation, integration, verification, validation; final pass transitions the end product to the user.
- Phases E–F: technical management only — monitor performance, control configuration, decide on operations/sustaining/closeout.

## Verification vs Validation (§2.4)
- Verification: proof of compliance with requirements — each "shall" shown by test, analysis, inspection, or demonstration (or combination). Relates to the approved requirements set; can occur at different life-cycle stages. Approved specs/drawings/parts lists establish the configuration baseline; without a verified baseline + configuration control, later modifications get costly or break performance.
- Validation: product accomplishes intended purpose in intended environment — meets customer and stakeholder expectations, shown by test/analysis/inspection/demonstration. Relates to the ConOps. Conducted under realistic or simulated conditions by typical users for effectiveness and suitability. Can be done in each phase on phase products (e.g., models), not only at delivery.
- Methods may differ between phases as designs mature. Program success relates to the frequency and diligence of validation during design, especially Pre-Phase A and A, where course corrections are still cheap. Continually ask: "Are we building the right product for our users and other stakeholders?"
- Method selection is engineering judgment: whichever most reliably shows conformance (verification) or intended operation per ConOps (validation).

## Cost effectiveness (§2.5)
- Objective: designed, built, operable to accomplish its purpose safely in the most cost-effective way considering performance, cost, schedule, risk. Many designs can satisfy the cost-effective condition.
- Trade studies seek the best cost/effectiveness combination. Win-win cases (cost down with effectiveness flat, or effectiveness up with cost flat) are easy; trading cost against effectiveness is hard.
- The Systems Engineer's Dilemma — at each cost-effective solution:
  - Reduce cost at constant risk → performance must be reduced.
  - Reduce risk at constant cost → performance must be reduced.
  - Reduce cost at constant performance → accept higher risk.
  - Reduce risk at constant performance → accept higher cost.
  - Schedule time is a critical resource and behaves like a cost.
- Life-cycle cost lock-in (Fig 2.5-1, from Defense Acquisition University): costs lock in early. During design only ~15% of cost is expended but ~75% of life-cycle cost is committed, because the design determines how expensive test, manufacture, integration, operation, sustainment will be. Cost to change rises each phase; descopes taken late save less. Problems first found at verification force redesign and reverification.
- Decision analysis techniques (SEH §6.8) help when attributes are incommensurable; SE's job is to make sure decision-makers have the information to pick the most cost-effective option.

## Human Systems Integration (§2.6)
- NPR 7123.1: the systems approach must equally address and integrate hardware, software, and human systems integration. Personnel, processes, procedures are inside the system definition. Expanded guidance in NASA Expanded Guidance for SE §2.6 (NEN repository).

## Competency model (§2.7, Table 2.7-1)
- Four proficiency levels per competency: Team Practitioner / Technical Engineer → Team Lead / Subsystem Lead → Project Systems Engineer → Chief Engineer. Full model: http://appel.nasa.gov/competency-model/

## Decision rules to carry
- ConOps before detailed requirements; include off-nominal scenarios.
- Decompose only until each leaf is buildable/buyable/reusable; prototype only to the feasibility tier.
- Two V&V columns — requirements → verification, ConOps → validation — never one standing in for the other.
- Validate early and often; the cheap corrections are in Pre-Phase A and A.
- Name the held-constant variable in every trade (cost / risk / performance / schedule).
- Baseline and control configuration once verified.
- Humans and procedures are system elements — design for them, don't bolt them on.
