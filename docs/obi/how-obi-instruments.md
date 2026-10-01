# How OBI instruments applications

A technical explanation of OpenTelemetry eBPF Instrumentation (OBI), written
against what we observed running it on the Astronomy Shop. Statements are tagged:

- **[docs]** — stated in the upstream OBI documentation
  (<https://opentelemetry.io/docs/zero-code/obi/>)
- **[observed]** — measured on this deployment (single-node Kubernetes, Linux
  7.0 kernel, OBI Helm chart 0.13.0 / OBI v0.12.2, October 2026)

Anything untagged is general eBPF background. Probe-level details change between
OBI versions; treat the upstream docs as authoritative for specifics.

---

## 1. The idea

Conventional instrumentation lives **inside** the application: an SDK, a language
agent, or a sidecar. OBI lives **beside** it. It is a single privileged agent per
node that uses eBPF to watch the application's behaviour from the kernel and from
the process's own memory, and reconstructs requests, database calls and message
operations from that.

Consequences:

| | In-process SDK / agent | OBI |
|---|---|---|
| Code change / rebuild | Required (or agent flag + restart) | None |
| Per-language work | One SDK per language, versioned separately | One agent for all languages |
| Covers third-party binaries (databases, proxies, brokers) | No | Yes |
| Application-level context (user ID, cart contents, custom spans) | Yes | No |
| Runs with the app's privileges | Yes | Needs elevated node privileges |

[docs] OBI "does not replace language-level instrumentation" for custom spans or
business logic and "cannot always recover application-specific details" that are
invisible at the syscall/protocol level.

## 2. Requirements and privileges

- [docs] Linux **5.8+** (RHEL 4.18 with backports), kernel **BTF** support,
  amd64 or arm64.
- [docs] Root or specific capabilities. For the Kubernetes DaemonSet: `hostPID`,
  `hostNetwork`, `/sys/fs/cgroup` mounted, and capabilities including `BPF`,
  `PERFMON`, `SYS_PTRACE`, `NET_RAW`, `NET_ADMIN`, `DAC_READ_SEARCH`,
  `CHECKPOINT_RESTORE`. The Helm chart configures these; this deployment runs the
  container privileged.
- [docs] If the kernel is in integrity lockdown mode
  (`/sys/kernel/security/lockdown` not `[none]`), context propagation and
  distributed tracing are disabled.

## 3. The pipeline, step by step

```mermaid
flowchart TB
  A[1 Discovery<br/>/proc + Kubernetes API] --> B[2 Classify runtime<br/>inspect the executable]
  B --> C[3 Attach eBPF probes]
  C --> D[4 Kernel emits events<br/>into ring buffers]
  D --> E[5 User-space decode<br/>HTTP · gRPC · SQL · Redis · Kafka]
  E --> F[6 Build spans, RED metrics,<br/>service graph, flow & TCP stats]
  F --> G[7 Add k8s.* metadata]
  G --> H[8 OTLP export]
```

1. **Discovery.** OBI continuously watches node processes and Kubernetes
   metadata. Selection rules (`discovery.instrument`) decide what to instrument;
   exclusions (`discovery.exclude_instrument`) remove things such as OBI itself.
   [docs] Selectors include executable path, open ports, language, container,
   namespace, deployment/pod names, and labels/annotations.
   This deployment: instrument namespace `otel-demo`, exclude `*chrome*`.
2. **Classification.** OBI inspects the process's executable to decide its runtime
   (its logs print `type=go|java|dotnet|python|nodejs|php|cpp|generic`).
   [observed] This is a classification of the *executable*, not of how the service
   was developed — a self-contained .NET app launched as a native `./cart` binary
   was classified as C++/native.
3. **Probe attachment.** Three families of eBPF probes are used (general eBPF
   design; exact probe points vary by OBI version):
   - **Socket and syscall probes** in the kernel see data sent and received by any
     process regardless of language. This is why a Ruby, Rust and C++ service all
     produce spans with no language-specific work.
   - **User-space probes (uprobes)** attach to functions inside a process or
     shared library where that gives more precision — for Go libraries, and for
     TLS libraries so encrypted traffic can be read at the library boundary, before
     encryption.
   - **Traffic-control (TC) hooks** on network interfaces observe packets for
     network-flow metrics and, optionally, for trace-context injection.
4. **Events to user space.** Probes write compact event records into kernel ring
   buffers; the OBI agent reads them.
5. **Protocol decoding.** The agent reassembles request/response pairs and parses
   the protocol. [docs] Supported: HTTP/S, HTTP/2, gRPC, Kafka, NATS, MQTT,
   Memcached, SunRPC, JSON-RPC (client and server); AMQP 1.0 and DNS (client);
   PostgreSQL, MySQL, MSSQL, Redis, MongoDB, Couchbase, Elasticsearch,
   OpenSearch, Aerospike.
6. **Telemetry construction.** Spans, RED metrics (rate, errors, duration),
   service-graph metrics, and — if enabled — network and TCP statistics.
7. **Enrichment.** Kubernetes attributes (`k8s.namespace.name`, `k8s.pod.name`,
   `k8s.deployment.name`, `k8s.node.name`, `k8s.cluster.name`, …) are added from the
   API. [docs]
8. **Export.** OTLP (here HTTP/protobuf, delta temporality, direct to Dynatrace).

## 4. Distributed trace context

A trace crosses services only if something links one service's outgoing call to
the next service's incoming request.

- [docs] **Incoming:** OBI reads the W3C `traceparent` header automatically; if
  none exists it creates a new trace ID.
- [docs] **Outgoing — Go (library level):** uprobes plus `bpf_probe_write_user`
  write context into process memory; Go 1.18+, up to six nested goroutine levels.
- [docs] **Outgoing — network level (any language):** inject `traceparent` into
  HTTP headers, add it to TCP/IP packets via TC, and for gRPC a per-stream HPACK
  header. Needs kernel ≥ 5.17 and `CAP_NET_ADMIN`.
- [docs] **Runtime-specific context tracking:** Node.js async hooks (8.0+),
  Python asyncio (3.9+, requires `uvloop`), Java thread pools (JDK 8+), Ruby Puma
  (5.0+).
- [docs] Limits: L7 proxies and load balancers disrupt the TCP-level propagation;
  encrypted traffic can only carry context between two OBI-instrumented services;
  non-gRPC HTTP/2 propagation is Go-only.
- [observed] About 81% of traces held a single service, ~16% two, ~3% three, and
  four-service traces were rare. Linking works, but in this deployment it is
  partial, not end-to-end for every path.

## 5. Per-language behaviour (as seen here)

| OBI classification | Services | What is distinctive |
|---|---|---|
| Go | `product-catalog`, `checkout`, `flagd` | Highest fidelity. Library-level context propagation [docs]. Client and server spans with parent/child links [observed]. |
| Java | `ad`, `fraud-detection`, `kafka` | JDK 8+ [docs]. With the runtime metric group enabled, `jvm.memory.*` metrics arrive with no `-javaagent` [observed]. |
| .NET | `accounting` | Normal (managed) .NET is handled at the protocol level: Kafka consumer spans and PostgreSQL client spans [observed]. |
| Node.js | `frontend`, `payment` | Async-hook context tracking [docs]; `nodejs.eventloop.*` metrics [observed]. |
| Python | `recommendation`, `product-reviews`, `llm`, `load-generator` | Protocol-level; asyncio propagation needs `uvloop` [docs]. |
| PHP | `quote` | Classified and decoded at protocol level; nothing PHP-specific visible in the data [observed]. |
| Ruby | `email` | Puma 5.0+ propagation [docs]; protocol-level spans [observed]. |
| Rust | `shipping` | Protocol-level; low volume in this demo [observed]. |
| C/C++ / native | `currency`, `postgresql`, `cart` | No runtime to hook; sockets and protocol parsing only. gRPC method names often unrecoverable (see §8) [observed]. |
| "generic" | `valkey-cart` (Redis), Envoy `frontend-proxy`, nginx-style proxies | Recognised processes with no language logic; protocol decoding still works [observed]. |

## 6. What the protocol decoding yields

[observed] Examples of the span attributes OBI produced with zero instrumentation:

| Protocol | Attributes captured |
|---|---|
| PostgreSQL | `db.system.name`, `db.operation.name` (e.g. `SELECT`, `INSERT`), `db.collection.name` (e.g. `accounting.order`), `server.address/port`. **Not** the SQL text. |
| Redis | `db.system.name=redis`, command (`HGET`, …) |
| Kafka | `messaging.system`, `messaging.destination.name` (topic), partition, **offset**, operation type (publish/process), client id |
| gRPC | `rpc.system.name`, `rpc.method` (when recoverable), status |
| HTTP | method, route, path, status code, request/response body size, client address |

## 7. Metrics beyond requests

Selected with `OTEL_EBPF_METRICS_FEATURES` [docs]. Enabled in this deployment:

| Feature group | Produces |
|---|---|
| `application` (default) | RED metrics: `http.server/client.request.duration`, `rpc.*.call.duration`, `db.client.operation.duration`, `messaging.*`, body sizes |
| `application_service_graph` | `traces_service_graph_request_*` (client → server edges) |
| `application_host` | Host-level application info (`traces.host.info`) |
| `application_runtime` | JVM and Node.js runtime metrics [observed]. Go/.NET/Python runtime metrics were **not** received. |
| `network`, `network_flow_packets` | `obi.network.flow.bytes`, `obi.network.flow.packets` (L3/L4) |
| `stats` | `obi.stat.tcp.rtt`, `.retransmits`, `.failed.connections`, `.io` |
| `ebpf` | OBI's own probe/map health — **not received** in this deployment |

Also observed: `dns.lookup.duration`.

## 8. Layer 3 / 4 network data

Independent of any application protocol. OBI attaches TC programs to the node's
interfaces and reads TCP socket statistics.

- Enabled by `network.enable: true` and the `network` / `network_flow_packets` /
  `stats` feature groups.
- [observed] Flow metrics are keyed by source and destination **Kubernetes owner**
  (workload), namespace, owner type, and direction; TCP statistics also carry source
  and destination IPs.
- [observed] Scope is the **whole node**: flows involving `argocd` and
  `kube-system` appeared alongside `otel-demo`, and 182 distinct workload pairs
  were seen in 30 minutes. Endpoints with no Kubernetes owner (external or node
  traffic) appear with an empty owner.
- Filter with `filter.network` (this repo excludes `kube*`, Prometheus and agent
  workloads).
- Bytes/packets/TCP behaviour only — no payload visibility.
- [docs] The metric group list also includes `network_inter_zone` (cross-zone
  traffic), which is only meaningful on multi-zone clusters; it is not enabled here.
- Cilium coexistence [docs]: both OBI and Cilium attach TC programs. They coexist
  when both use TCX (kernel ≥ 6.6; Cilium ≥ 1.16 default). On the legacy netlink
  path, OBI refuses to start if Cilium uses priority 1; configure Cilium
  `bpf.tc.priority: 2` or set OBI's TC backend explicitly
  (`OTEL_EBPF_BPF_TC_BACKEND`: `tcx`, `netlink`, `auto`).

## 9. Limits we measured

1. **Business context is invisible.** No custom attributes, no custom spans.
2. **Method names can be lost.** For the C++ `currency` service ~99% of gRPC
   spans reported `rpc.method = *`.
3. **No SQL text** — operation and table only.
4. **Service naming.** If OBI cannot derive a name it falls back to the namespace
   (`otel-demo`), which is not a real service.
5. **Runtime metrics are partial** (§7).
6. **Already-instrumented services are skipped by default**
   (`discovery.exclude_otel_instrumented_services`, default `true`) [docs] — it
   matters in estates that mix SDKs and OBI.
7. **Trace stitching is partial** (§4).
8. **Process churn is the cost driver.** Headless Chromium in the load generator
   spawned many short-lived processes; OBI attached and detached to each and was
   OOM-killed repeatedly at a 6 GiB limit. Adding
   `discovery.exclude_instrument: [{exe_path: '*chrome*'}]` brought it to a steady
   ~300 MiB with no restarts. Size limits from observed peaks, and exclude noisy
   process trees.
9. **OBI will observe the application's own telemetry traffic.** If SDKs still
   export, OBI traces those exports as spans. Here this is removed with
   `filter.application.server.address.not_match: 'otel-collector*'`.

## 10. Reproducing the measurements

Dynatrace Query Language (DQL), scoped to this cluster:

```dql
// Is OBI the only span source?
fetch spans, from: now() - 15m
| filter k8s.cluster.name == "orbstack-obi-eval"
| summarize spans = count(),
    by:{source = if(telemetry.distro.name == "opentelemetry-ebpf-instrumentation", "OBI", else: "other")}
```

```dql
// Languages OBI classified, and the services in each
fetch spans, from: now() - 1h
| filter telemetry.distro.name == "opentelemetry-ebpf-instrumentation"
    and k8s.namespace.name == "otel-demo"
| summarize spans = count(), services = collectDistinct(service.name),
    by:{language = telemetry.sdk.language}
| sort spans desc
```

```dql
// L3/L4: heaviest workload-to-workload flows
timeseries bytes = sum(obi.network.flow.bytes), from: now() - 1h,
    by:{k8s.src.owner.name, k8s.dst.owner.name}
| fieldsAdd total = arraySum(bytes)
| sort total desc
| limit 12
| fields k8s.src.owner.name, k8s.dst.owner.name, total
```

```dql
// Trace stitching: how many OBI services per trace
fetch spans, from: now() - 30m
| filter telemetry.distro.name == "opentelemetry-ebpf-instrumentation"
    and k8s.namespace.name == "otel-demo"
| summarize services = countDistinct(service.name), by:{trace.id}
| summarize traces = count(), by:{services}
| sort services asc
```

Always use `telemetry.distro.name == "opentelemetry-ebpf-instrumentation"`
(equality). `isNotNull(telemetry.distro.name)` also matches other agents, and
`!= "…"` silently drops spans where the attribute is null.

The full set — 25 queries with commentary — is the importable notebook
[`dynatrace-notebook.json`](dynatrace-notebook.json).
