# Tenchiro SX – SaaS Extension App

Django app installed into WebODM to support SaaS resource usage measurement,
reporting, and local enforcement. Business rules and pool sizing live only in
the SaaS Portal.

## Roles

**Portal**
- Owns plans, limits, billing cycles, and multi-app aggregation.
- Exposes **resource pools** to apps: current **availability** per resource
  (not limits, not business rules).
- Records each app’s reported usage separately, then recomputes pools.
- Handles overage policy (initially: pool → 0; later: tolerate or bill excess).
- All API access requires the shared secret; auth failure → no successful handoff.

**App (Tenchiro XODM)**
- Measures and logs its own usage (`UsageEvents`).
- Reports usage to the Portal; does not derive pool size from its own usage.
- Caches the last known pool (availability) and treats it as operational truth
  until the next successful sync.
- Enforces locally where WebODM allows (upload/create/import, etc.). Perfect
  enforcement is not assumed; Portal absorbs residual overage policy.

## Resource kinds

| Kind | Meaning | Reporting | Pool behavior |
|------|---------|-----------|---------------|
| **Aggregate** (e.g. `disk_bytes`) | Current footprint | Latest snapshot | Can go up or down |
| **Consumed** (e.g. `cpu_seconds`) | Cumulative use | New increments only | Only decreases availability within a cycle |

The app never applies cycle resets; only the Portal does.

## Data flow

1. **Measure** – App writes local `UsageEvents` when it decides a value is real
   (e.g. task completed / removed). Intermediate process state is the app’s problem.
2. **Report (push)** – App sends usage that has not yet been successfully
   handed off. On HTTP 200, mark those events handed off in the webhook log.
   Do not resend successfully handed-off usage.
3. **Sync pool (pull or Portal push)** – App obtains current availability.
   Separate transaction from usage push. Cache the result.
4. **Enforce** – Gate work using the cached pool. On uncertainty (no pool,
   Portal down), app policy applies; overage resolution is Portal-side.

Critical moments (e.g. before a large task) should pull a fresh pool. Divergent
caches across apps are expected; only the Portal has global truth.

## Authority and handoff

- **Usage**: App is source of measurement. After a validated transmission
  (HTTP 200), authority for “this usage was reported” is recorded locally;
  those events are excluded from future reports.
- **Pool**: Portal is source of availability. App cache is a snapshot, not a
  ledger to be decremented by local usage. Optional optimistic local subtraction
  is a dirty cache only until the next pull.
- **Aggregate backfill**: If several aggregate snapshots failed to send, only
  the latest needs to be transmitted; on success, mark the older pending
  aggregates handed off as superseded.
- **Consumed**: Only unsent increments are reported. Values do not go backwards.

## Webhook / transaction log (app)

Log every push/pull attempt: direction, purpose (usage report vs pool sync),
user, resource keys, status code, success/failure, timestamp, and which local
usage events were included / marked handed off.

This log is the audit trail for communication; `UsageEvents` is the audit trail
for measurement. Admin displays the measurement log; communication log supports
support/debug and handoff state.

## Duplication & Ordering Strategy

The Portal handles duplicate or out-of-order usage messages using a dual-header protocol: a unique **Message UUID** and a strict, monotonically increasing **Serial Number**.

1. **Message UUID (Deduplication):**
   - Every transmitted push payload (whether containing `UsageEvents` or pool syncs) is assigned a fresh `message_id` (UUIDv4).
   - The Portal records received `message_id`s in a deduplication cache.
   - If the Portal receives a `message_id` it has already processed (e.g., due to a network retry after a dropped HTTP response), it returns `200 OK` immediately without re-applying consumed usage or reprocessing aggregates.

2. **Serial Number (Strict Ordering):**
   - The App maintains a per-user (or per-app instance) monotonic `serial_number` integer sequence (`1, 2, 3...`).
   - Every outbound message increments and includes this `serial_number`.
   - **Enforcement Rule:** The Portal accepts and applies state updates **only if** `incoming_serial > last_processed_serial` for that app/user stream.
   - If an incoming payload arrives with a `serial_number <= last_processed_serial` (and is not an exact `message_id` retry), it is rejected or logged as stale to prevent out-of-order usage from overwriting newer aggregate snapshots or corrupting consumed totals.
   
## Out of scope for the app

- Plan limits, cycle boundaries, multi-app math, overage billing.
- Reserve/hold amounts (future).
- Guaranteed real-time global consistency across apps.

## Implementation notes (WebODM)

- Measurement hooks are constrained by WebODM signals and patch points.
- Disk aggregate updates at completion/removal; mid-run filesystem dips are
  ignored until the app chooses to log a new aggregate.
- Local enforcement may lag or miss edge cases; Portal policy covers residual risk.

## Examples

**Portal**

- PUSH pool (availability) to entitled apps  
  - After App1 reports CPU consumption, recompute availability and push updated pool to App2  
  - On plan upgrade, push new pools to each entitled app  
  - On user login, push current pool to entitled apps  

- MANAGE pools per resource kind  
  - `disk_bytes` (aggregate)  
  - `cpu_seconds` (consumed)  

- PULL usage from apps when reconciling (optional / periodic)  

- LOG each push/pull communication with apps  

**App (Tenchiro SX)**

- LOG local usage events (`UsageEvents`)  

- PULL pool (availability) from Portal at critical points  

- PUSH usage not yet handed off  
  - Task completed: report disk aggregate + CPU consumed  
  - Task deleted: report reduced disk aggregate  

- LOG webhook transactions (handoff status)  

- DISPLAY usage audit in admin