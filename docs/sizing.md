# Sizing an event

Size for simultaneously active pages. Plugin **0.1.20 / CTFd 3.8.9** generated
about **0.35 API requests/s per participant** in our room/standings mix. Challenge
workloads ran on separate clusters; closed pages do not generate polling load.

## What we measured

In November 2026, we tested on OpenShift with 16-vCPU AMD EPYC VM nodes,
50 teams of eight, 25 challenges and 1,070 historical native solves. Authenticated
HTTP clients used native sessions, CSRF and the exact shipped polling/session
coordinator through the public HTTPS Route, from an isolated test pod. This was
not 400 rendered browsers. We captured latency, errors/retries, CPU/throttling,
memory, traffic, DB connections/lock waits and restarts.

- **400 clients for ten minutes:** 85,155 originating requests,
  141.7 successful requests/s, zero unrecovered errors. CTFd averaged
  2.77 CPU cores and about 0.79 GiB RAM;
  steady endpoint p95 ≤0.27s and p99 ≤0.35s.
- **50 simultaneous team flags:** all acknowledged correctly, p95
  8.34s; all 400 members observed their team's result. Native DB reconciliation
  confirmed exactly 50 new solves, unchanged prior records and no duplicate credit.
- **400 reconnects and 400 reloads, each spread over ten seconds:** all became
  ready with fresh accepted state. One reused-connection GET reset recovered
  with one bounded retry; POSTs were never retried.

The driver reports roughly one additional live second after each nominal phase;
independent validation also checks the exact offered window. Warm HTTP response
traffic was about 4.9 Mbit/s, excluding TLS; internal DB/cache traffic is separate.

A paired 180-second comparison with unchanged old polling, data and allocation
reduced CTFd CPU from 5.16 to 3.38 cores (35%) and DB commands by 52%. The new
client then reduced requests from about 181/s to 142/s: one lightweight five-second
sync replaces recurring full bootstrap and separate onboarding. Full metadata
loads on relevant revisions; attendance and authorization remain live.

We selected a six-core application limit for burst headroom. A four-core trial
passed steady polling but throttled about 13% of CPU periods and slowed initial
challenge reads. The shared event write lock still serializes flag submissions;
more CPU does not remove it. Previous external connection stress exposed socket
closures/deadlines, so these results qualify origin HTTP capacity, not venue
Wi-Fi/WAN or end-to-end browser readiness.

## Starting estimates: 50–500 attendees

Assume this polling mix and roughly eight people per team. **Only 400 attendees
were measured.** Other rows scale that CPU allocation, rounded up to half a core.
Hardware, history, team count and bursts can change requirements.

| Active attendees | Approx. API requests/s | CTFd CPU request / limit |
|---:|---:|---:|
| 50 | 18 | 0.5 / 1 core |
| 100 | 35 | 1 / 1.5 cores |
| 150 | 53 | 1.5 / 2.5 cores |
| 200 | 71 | 2 / 3 cores |
| 250 | 89 | 2.5 / 4 cores |
| 300 | 106 | 3 / 4.5 cores |
| 350 | 124 | 3.5 / 5.5 cores |
| **400 (measured)** | **142** | **4 / 6 cores** |
| 450 | 159 | 4.5 / 7 cores |
| 500 | 177 | 5 / 7.5 cores |

All rows retain **8 workers and CTFd memory requests/limits of 1 / 2 GiB**.
Add these supporting services to each row:

| Component | CPU request / limit | Memory request / limit |
|---|---|---|
| MariaDB | 500m / 1 core | 512 MiB / 1 GiB |
| Redis | 250m / 500m | 128 / 256 MiB |

Workers are processes, independent of the CPU limit. Each has a ten-connection
pool with overflow zero: at most 80 application connections, below MariaDB's
default 151. Leave room for administration/migrations and recheck pools when
changing workers. Attendance above 400, larger challenge paths/history and more
simultaneous team submissions need additional load tests.

## OpenShift example: 400 attendees

Apply these settings to your own Deployment; this is a partial example, not a
complete Kubernetes manifest:

```yaml
spec:
  template:
    spec:
      containers:
        - name: ctfd
          env:
            - name: WORKERS
              value: "8"
            - name: SQLALCHEMY_MAX_OVERFLOW
              value: "0"
          resources:
            requests: {cpu: "4", memory: 1Gi}
            limits: {cpu: "6", memory: 2Gi}
```

Allocate MariaDB and Redis using the supporting-service table above. Keep
platform configuration and image pins in your deployment repository; see
[hosting](deployment.md) for the application's requirements.

The stack totals **4.75 requested / 7.5 limited CPU** and **1.625 / 3.25 GiB RAM**,
excluding OpenShift. The CTFd pod needs a node with room for its full 4-CPU /
1-GiB request; limits do not reserve capacity. Storage requests were 10 GiB for
uploads/logs, 10 GiB MariaDB and 1 GiB Redis; adjust for content and retention.

Before an event, repeat steady polling, simultaneous flags and paid hint
purchases, reconnects and cold loads against a disposable instance with
representative history. Inspect tail
latency and errors alongside resources, traffic and DB lock waits. Validate from
the venue network too. Shared read caching requires MariaDB/MySQL REPEATABLE READ;
other database/isolation modes bypass it. Use supported APIs during the event;
out-of-band SQL edits require clearing the application cache/restarting readers.
