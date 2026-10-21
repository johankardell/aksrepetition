# Facilitator reference — synthetic, not measured repair evidence

Do not distribute this file before independent diagnosis. Every output and outcome below
is an **invented reference acceptance example**, not an executed repair or a guaranteed
SLO. Learners must say what they would collect after an approved repair; they cannot
claim this evidence was observed. Scope each ticket independently.

## A — Container limit regression; not demonstrated node eviction

**Timeline / ordering:** source limit changed at 09:10, rollout appeared available at
09:11, the recorded process ran 09:13:11–09:14:55 before an `OOMKilled` termination,
and restarted at 09:15:10. Client failures overlap, but the samples alone do not prove
which individual reset came from which pod. Lead with container memory exhaustion (A3/A5);
consider node pressure/eviction (A4 argues against this in the sample), application
crash/leak (no traceback, inadequate memory history) and gateway trouble (does not
explain container termination).

**Distinguishing check:** compare container `lastState.terminated.reason`, pod-level
`status.reason/message`, events and the actual container limit for the same pod UID/time.
OOM gives container `OOMKilled`; eviction gives pod `Evicted` with an eviction message.
Code 137 alone can also be SIGKILL. A green node snapshot cannot rule out earlier pressure.
Inspect time-aligned working set/RSS and previous-revision behavior before alleging a leak.

**Minimal repair:** application/GitOps owner proposes reverting only the reviewed API
resource patch to the previously working 128 MiB request / 256 MiB limit, after capacity
approval. Use a reviewed Git change and the named `orders` reconciler; do not patch live
replicas or raise all limits. If capacity is unavailable, stop and escalate rather than
forcing scheduling. Rollback the repair commit only if it introduces a separate regression;
restoring the known bad limit is not a safe incident resolution.

**Reference acceptance design:** same low synthetic load over five minutes; two available
API replicas, no new OOM/restarts, bounded memory headroom, successful real HTTPS plus a
synthetic order acceptance/completion correlation. Reopen if restarts recur or completion
fails; five minutes does not rule out a slow leak.

```text
SYNTHETIC REFERENCE ONLY — 09:25..09:30 acceptance window
orders Flux Ready=True at reviewed repair revision 92ac002
deployment/order-api updated=2 available=2
new pod restart counts: 0 -> 0; no OOMKilled/Evicted in window
memory working-set samples: 110..122 MiB, limit 256 MiB
HTTPS synthetic requests: 300/300 successful; failures=0
order ticket-a-repair-001: 202 at 09:26:00, completion log at 09:26:03
```

## B — Impossible selector; not image pulling or exhaustion

**Timeline / ordering:** placement constraint changed 09:59, Flux applied 10:01, scheduler
rejected placement from 10:01:05 onward, deadline notification at 10:12. B2 has no node
assignment or container status: the scheduler has not reached image pull/startup.
Lead with selector mismatch (`frontend` versus `apps` in B3); then taints/affinity,
resource exhaustion and image pull. Requests-based headroom is not actual utilization,
but the explicit event rejects selector matching, not CPU/memory.

**Distinguishing check:** compare the new ReplicaSet's effective nodeSelector/affinity
against actual approved node labels and `PodScheduled` event. A matching selector with
`Insufficient memory` would lead elsewhere; `ImagePullBackOff` requires a scheduled pod.

**Minimal repair:** GitOps app owner reverts just
`template.spec.nodeSelector["kubernetes.azure.com/agentpool"]`
to `apps` in the `orders` source, reviews the diff and observes its existing reconciler.
Do not relabel every node, increase surge, remove isolation, restart the old healthy
replicas, or add capacity for a constraint no node can match. Stop if new pods cannot become
Ready while retaining both old healthy replicas. If the revised template fails for a
second reason, revert to the last reviewed working template and investigate separately.

**Reference acceptance design:** new pods placed on approved apps nodes, no new
FailedScheduling for their UIDs, rollout updated/available=2 and old replicas=0 after
graceful handoff; five minutes of private user-path checks and one completed synthetic order.
Distinguish scheduler recovery from application/dependency readiness.

```text
SYNTHETIC REFERENCE ONLY — after reviewed repair 1ab2003
new PodScheduled=True, nodes aks-apps-000001 / aks-apps-000002
deployment/order-api updated=2 available=2 total=2
old ReplicaSet desired=0
10:25..10:30 HTTPS requests 300, successful 300
ticket-b-repair-001 accepted 10:26:00, worker completion 10:26:04
```

## C — Correct worker identity, wrong queue permission

**Timeline / ordering:** queue permission changed 10:59; token acquisition succeeded
but the peer rejected receive access at 11:01:04; accepted work accumulates through 11:10.
The token subject/client/principal chain is consistent (C4). The actual Service Bus peer
is reached with verified TLS (C3); its `Listen` error and Sender-only role (C5) distinguish
authorization from connectivity or federation failure. Worker Running/Ready proves no
transaction success when it has no business readiness probe; its six restarts and Error
termination also show that the sampled Running state is not sustained process health.

**Distinguishing check:** correlate the worker's effective principal (not its client ID)
with role assignments and inherited scopes on the exact queue and the broker operation.
A token-acquisition failure suggests federation/tenant configuration; an authenticated
broker denial with only Sender suggests missing receive permission. Preserve the
distinction between Service Bus management roles and data-plane roles.

**Minimal repair:** authorized role-assignment/infrastructure owner restores **Azure
Service Bus Data Receiver at the exact orders queue** for the existing worker principal,
and removes the accidental worker Sender assignment after review. Preserve the API's
separate queue-scoped Sender. Update/review the narrowly scoped desired permission state;
never redeploy the foundation snapshot over a progressed cluster. Allow bounded role
propagation and existing SDK retries; do not share identities, grant Owner, broaden to
namespace-wide access or open the firewall. If the principal/scope does not match the
approved inventory, stop. Record assignment IDs for rollback, but do not knowingly
restore unauthorized privilege as a fallback.

**Reference acceptance design:** within a chosen ten-minute propagation/observation
budget, receive operations succeed, unauthorized errors cease, backlog and oldest age
trend down, a specific newly submitted synthetic order completes within lab 14's
60-second baseline target, and API sending remains healthy. Existing backlog can take
longer to clear; specify its starting count/arrival rate. At lab 14 use worker logs/traces,
not lab 21's durable order lookup. Escalate on timeout; do not grant broader roles.

```text
SYNTHETIC REFERENCE ONLY — not a guaranteed propagation interval
11:20 exact worker queue roles: Data Receiver; no Data Sender
11:22..11:27 unauthorized receive errors=0
queue active: 91 -> 45 -> 0; oldest age: 540s -> 280s -> 0s
ticket-c-repair-001 accepted 11:23:00, worker completion 11:23:05
API queue-scoped sender remains a distinct principal; HTTP acceptance still succeeds
```

## D — Target discovery mismatch; not established ingress failure

**Timeline / ordering:** telemetry selector changed 12:14; Flux applied and new custom
samples stopped at 12:15; the alert fired 12:22. The directly tested private HTTPS order
path succeeded 12:23 (D2). It does not prove uninterrupted availability, but contradicts
the claim that “No data” itself establishes a current ingress outage.

Lead with PodMonitor selection (D3/D4), then scrape networking/endpoint availability,
datasource authorization/time range and application/ingress failure. The managed
`azmonitoring.coreos.com/v1` CRD is correct; the label value is not. Healthy platform
series and HTTP 200 datasource queries argue against total agent/datasource failure.
Log Analytics receiving logs is a different pipeline and does not prove Prometheus scraping.

**Distinguishing check:** inspect the deployed PodMonitor selector against pod labels,
and compare discovered targets with direct `/metrics` availability. Zero selected targets
differs from selected targets with `up=0`/scrape errors. An ingress diagnosis needs
direct DNS/TLS/HTTPS and gateway evidence, not an empty Prometheus vector.

**Minimal repair:** reviewed change to both namespace GitOps app overlays restores
PodMonitor `spec.selector.matchLabels.app: order-api`. Observe **both named** reconcilers
and discovery before claiming repair. Do not restart healthy app pods, change gateway/TLS,
enable public networking, broaden identity privileges or add a second instrumentation
stack. Stop if selection matches but scrapes fail; investigate that separate boundary.
Rollback the selector-only repair commit if it unexpectedly selects the wrong workload.

**Reference acceptance design:** both monitors select their actual API pods; fresh
application-series timestamps advance across at least two scrape intervals and a
five-minute observation window. Correctly scoped Grafana panels show new data, not only
historical cache. Repeat private HTTPS and order completion checks to confirm the telemetry
repair did not damage the user path. Do not substitute `or vector(0)` for a missing-data fix.

```text
SYNTHETIC REFERENCE ONLY — after both reconcilers apply reviewed repair b013005
orders/order-api selected targets=2; orders-test/order-api selected targets=2
selected target up=1; custom metric sample timestamps advance at 12:35 and 12:36
12:35..12:40 both namespace request-duration panels contain fresh samples
HTTPS 200; ticket-d-repair-001 accepted 12:37:00, worker completion 12:37:03
```

Accept a different minimally scoped plan if the learner shows its evidence, approval,
rollback and distinguishing outcomes. Never mark these invented outputs as live verification.
