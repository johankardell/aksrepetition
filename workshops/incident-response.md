# Optional workshop — Diagnose the symptom before choosing a repair

**Entry:** complete [lab 14](../labs/14-observability.md); understand lab 13 GitOps ownership.
**Time:** 75–100 minutes (four 15-minute tickets plus review).
**Mode:** entirely offline, **design-only synthetic evidence review**, not a live incident pass.
**Cost:** no additional cloud resources, queries, traffic or ingestion. Existing resources
remain billable independently of this exercise.
**Exit:** submit a reasoned incident report for each ticket, compare against the hidden
facilitator references, and identify what remains unproven without post-repair measurements.

Read the [shared operating contract](../docs/operations.md) and
[state/ownership model](../docs/state.md). Do not apply manifests, edit GitOps sources,
grant roles, restart workloads, run injectors or execute the illustrative evidence commands.
Everything under [`assets/incidents/`](assets/incidents/) is **static, explicitly synthetic**
data, written for this workshop; none is a measured result or real customer record.
Names, timestamps, IDs, addresses, counts and proposed acceptance outputs are invented.
No Azure/Kubernetes connection or credentials are needed. Linux/Bash and a text viewer
are sufficient; Python 3.12+ is needed only for the optional offline fixture tests.

## Working agreement

Work independently or in pairs: one incident lead maintains the timeline and decision
record; the second challenges assumptions. Treat every ticket as a separate incident,
not a sequence of faults on one cluster. Read only its learner evidence first. Do not
open the facilitator section until your hypothesis ordering and distinguishing check
are written down. The filenames and initial tickets deliberately identify symptoms,
not the fault to repair.

For each ticket submit:

1. **Impact and timeline:** UTC first symptom, relevant change, checks, and current scope.
   Distinguish event time from collection time; don't infer causality from proximity alone.
2. **Ordered hypotheses:** at least three plausible layers, supporting/refuting clues,
   uncertainty, and which evidence is absent. Include a tempting but wrong repair.
3. **Distinguishing check:** one narrowly scoped read-only query/inspection that would
   separate your top two hypotheses; specify expected alternatives, not just a command.
4. **Minimal repair plan:** exact object/field or permission scope, owner, desired-state
   path, prerequisite approval, risk, rollback and a stop condition. A command is optional;
   do not execute it. Preserve namespace, identity, workload and GitOps boundaries.
5. **Post-repair acceptance:** concrete platform and user-path observations, duration,
   baseline/comparison, and failure criteria. A Ready pod or successful deployment alone
   is not sufficient. Any sample target in this workshop is an exercise target, not a
   guaranteed SLO, Azure SLA or production availability claim.

Keep your reports in a private local evidence directory such as
`.artifacts/incident-response/`; do not commit credentials or real incident details.
Use [lab 14's signal definitions](../labs/14-observability.md) to separate request
acceptance, durable completion and missing telemetry.

## Ticket A — Requests pause during an otherwise quiet morning

**09:15 UTC, synthetic on-call notification:** the API intermittently disappears from the
service endpoint list. Most requests succeed, but a small synthetic client sees resets.
There is no reported traffic surge. A deployment completed shortly before the alert.

**Your task:** decide whether to start with the application process, scheduler, node
pressure or ingress. The responder has not established which layer caused the resets.

Read [`ticket-a.txt`](assets/incidents/ticket-a.txt). Do not fix every warning; identify
the first discriminating fact, explain what exit codes can and cannot prove, and propose
the smallest reversible repair. State what additional data would be needed before
concluding there is an application leak.

## Ticket B — Release is stuck, but users can still place orders

**10:12 UTC, synthetic release notification:** the rollout deadline has passed. The change
is approved and its image is present in the registry. Existing user-path checks still pass.
An engineer suggests restarting every pod or allowing more surge.

**Your task:** establish whether the release is waiting for image retrieval, placement,
readiness or dependency authorization. Explain why healthy old replicas must be preserved.

Read [`ticket-b.txt`](assets/incidents/ticket-b.txt). Include a stop condition that prevents
an unsuccessful rollout from becoming a service outage, and explain why new infrastructure
is not automatically the right repair.

## Ticket C — HTTP acceptance looks healthy; orders do not finish

**11:07 UTC, synthetic support notification:** the API returns 202 and the health dashboard
is green, but queue age and unfinished-order reports are increasing. Both API and worker
pods are Running. An engineer proposes opening the firewall and sharing the API identity
with the worker.

**Your task:** locate the failing transaction boundary without trusting a shallow probe.
Distinguish DNS/TLS, token acquisition, identity selection, and data-plane permission.

Read [`ticket-c.txt`](assets/incidents/ticket-c.txt). Specify a least-privilege repair and
how you would check both queue draining and one specific synthetic order's completion.
Do not use the durable order lookup introduced only in lab 21.

## Ticket D — A dashboard disappears while application probes remain green

**12:22 UTC, synthetic monitoring notification:** the custom API latency panel shows
“No data” in both namespaces after a telemetry-only change. A responder calls it an ingress
outage; another wants to restart the API. No customer-path failure has been confirmed.

**Your task:** separate missing observations from an unhealthy observed service. Identify
which telemetry pipeline owns this panel and what direct evidence tests the ingress theory.

Read [`ticket-d.txt`](assets/incidents/ticket-d.txt). Explain why zero matching series is
neither zero errors nor proof that the platform metrics agent is broken. Your repair must
not weaken TLS, open a public endpoint or duplicate instrumentation.

## Grading rubric

Score each ticket out of 10; **32/40 with no safety violation** is the design-review target.
This score is not live operational acceptance.

| Points | Evidence required |
|---|---|
| 2 | Correct impact boundary and ordered UTC timeline, with source references |
| 2 | Three plausible hypotheses ordered by evidence; alternatives explicitly rejected or left uncertain |
| 2 | A distinguishing read-only check with different predicted outcomes |
| 2 | Minimal repair owned by the right controller/operator, with approval and rollback |
| 2 | Post-repair platform **and** user-path acceptance, explicit observation interval and stop criteria |

Deduct the relevant points for unexplained guesses, treating missing evidence as success,
or treating a synthetic output as a measurement. A proposed unapproved live fault, broad
role grant, shared-network opening, cluster-wide restart, or destructive namespace
cleanup is a safety violation: revise the plan before passing. Alternative diagnoses
receive credit if they identify the unresolved evidence and safely discriminate them;
matching the facilitator's words is not the goal.

<details>
<summary>Facilitator solutions — open only after submitting all four diagnoses</summary>

The separate [facilitator reference](assets/incidents/facilitator.md) contains intended
diagnoses, distinguishing checks, narrowly scoped repair/rollback plans and **synthetic
reference acceptance outputs**. Those outputs are teaching examples, not executed repairs.
Ask learners to defend their ordering before revealing them.

There is no automatic live-practice extension. For separately approved operational
practice, return to the existing lab's explicit ownership, bounded fault, recovery and
acceptance instructions rather than transcribing these static command headings into a
terminal. The optional [workload reliability workshop](workload-reliability.md) confines
probe/memory experiments to its own namespace; it never authorizes faults in `orders`.

</details>

## Acceptance and cleanup

Retain four reports, rubric scores and a short “what would falsify my conclusion?” review.
Mark the result **offline design review complete**, never “incident repaired” or “SLO met.”
There is no cluster cleanup: no resource was created. Retain or remove only your own
local `.artifacts/incident-response/` notes under your evidence-retention policy.
Do not delete either application namespace, monitoring resources or the static fixtures.

## References and onward route

- [Lab 13 — desired-state ownership](../labs/13-gitops.md)
- [Lab 14 — monitoring paths and SLO definitions](../labs/14-observability.md)
- [Kubernetes debugging running pods](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/)
- [README lab index](../README.md#lab-index)

**Next:** continue [lab 15 — scaling](../labs/15-scaling.md). This is an optional,
unnumbered detour after lab 14.
