# OBI (eBPF) Zero-Code Observability on the OpenTelemetry Astronomy Shop

A working reference for **OpenTelemetry eBPF Instrumentation (OBI)**: a polyglot,
20+ service microservice application observed end to end **without adding an SDK,
sidecar or code change to any service**, with all telemetry exported over OTLP to
Dynatrace. (One nuance: for Node.js, OBI injects a small agent script into the
running process at runtime; nothing is added to the image or the code.)

This repository is a fork of the
[OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo)
("Astronomy Shop"), deployed on Kubernetes with Kustomize + Argo CD, plus the
OBI deployment, the configuration that makes OBI the *only* telemetry source,
and the material that explains how it works.

> The original upstream README is preserved at
> [`docs/UPSTREAM_README.md`](docs/UPSTREAM_README.md).

| I want to… | Go to |
|---|---|
| Understand what OBI is and how it instruments code | [What OBI is](#what-obi-is) and [`docs/obi/how-obi-instruments.md`](docs/obi/how-obi-instruments.md) |
| See how it reaches inside Python and Java (symbols, USDT, Java agent) | [Inside the runtime](#inside-the-runtime-symbols-usdt-probes-and-the-java-agent-python-and-java) |
| See what it captured per language | [Results by language](#what-obi-captured-in-this-demo) |
| Know whether L3/L4 network data is possible | [Network layer (L3/L4)](#network-layer-l3--l4-data) |
| Deploy or reconfigure it | [`k8s/obi/README.md`](k8s/obi/README.md) |
| Send OBI's data somewhere other than Dynatrace | [Any OTLP backend](k8s/obi/README.md#sending-obi-data-to-any-otlp-backend) |
| Import the explanatory Dynatrace notebook | [`docs/obi/dynatrace-notebook.json`](docs/obi/dynatrace-notebook.json) |
| Know what it can't do | [Limits](#honest-limits) |

---

## What OBI is

OBI is an OpenTelemetry project that uses **eBPF** — small, kernel-verified
programs loaded into the Linux kernel — to observe applications from the outside.
It runs as **one privileged DaemonSet per node**. It watches the processes on
that node, attaches probes to them, and turns what it sees into standard
OpenTelemetry **traces, metrics and network-flow data**.

- **No code changes, no rebuilds, no restarts.** Services run as they were built.
- **One agent for every language.** The same OBI pod instruments Go, Java, .NET,
  Node.js, Python, Ruby, Rust and native C/C++ processes (the officially supported
  set), plus databases and brokers you could never put an SDK into (PostgreSQL,
  Redis, Kafka). OBI also classified this demo's PHP service.
- **Standard output.** OTLP, with Kubernetes metadata attached, to any backend.

### How it instruments (short version)

```mermaid
flowchart LR
  subgraph node["Kubernetes node (Linux kernel)"]
    direction TB
    app1["checkout (Go)"] --- k
    app2["frontend (Node.js)"] --- k
    app3["ad (Java)"] --- k
    app4["postgres, redis, kafka…"] --- k
    k["eBPF probes<br/>socket / syscall hooks · library uprobes · socket-filter / TC network hooks"]
    k -->|ring buffers| obi["OBI agent<br/>(DaemonSet)"]
  end
  obi -->|"decode protocols · build spans, RED metrics,<br/>service graph, flow & TCP stats ·<br/>add Kubernetes metadata"| otlp(("OTLP"))
  otlp --> dt["Dynatrace"]
```

1. **Discover.** OBI finds processes on the node and selects the ones matching
   its rules (here: namespace `otel-demo`). It classifies each by runtime
   (Go, Java, .NET, Node.js, Python, …) from the executable itself.
2. **Attach.** It attaches eBPF probes: to the kernel's socket/network paths
   (works for *every* language), to functions inside the process where that adds
   precision (for example Go libraries and TLS libraries), and packet-level
   hooks for flow data (a socket filter by default, optionally TC).
3. **Decode.** Raw events are reassembled in user space into requests and
   responses and parsed as HTTP, HTTP/2, gRPC, SQL, Redis, Kafka and more.
4. **Enrich & export.** Spans and metrics are decorated with `k8s.*` attributes
   and sent as OTLP.

Full detail, per-language behaviour and the exact limits we measured are in
[`docs/obi/how-obi-instruments.md`](docs/obi/how-obi-instruments.md).

### Inside the runtime: symbols, USDT probes and the Java agent (Python and Java)

Kernel socket probes alone tell OBI *that* bytes moved, but not which request
inside an interpreter or JVM they belong to. For Python and Java, OBI goes one
level deeper using two different techniques. Source links point at OBI
**v0.12.2**, the version deployed here; "observed" means checked in this cluster
on 2026-10-02.

**Python: uprobes on CPython's own C functions (ELF symbols).** OBI never
touches Python code. It attaches eBPF uprobes to C functions inside the
interpreter, found **by symbol name** in `libpython3.*.so` and in the `_asyncio`
extension module:

| Symbol | Library | Why OBI hooks it |
|---|---|---|
| `task_step` | `_asyncio` | Records which `asyncio.Task` is currently running on the event-loop thread |
| `_asyncio_Task___init__` | `_asyncio` | Records the parent task when a child task is created (`create_task`, `gather`) |
| `PyContext_CopyCurrent` | `libpython3.x` | Binds a copied `contextvars.Context` to its task (`asyncio.to_thread`) |
| `context_run` | `libpython3.x` | Lets a worker thread map that context back to the original task |

- Where it lives: the symbol table is in
  [`pkg/internal/ebpf/generictracer/generictracer.go:515-555`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/internal/ebpf/generictracer/generictracer.go#L515-L555)
  and the eBPF programs in
  [`bpf/generictracer/python.c`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/bpf/generictracer/python.c) (for example
  `SEC("uprobe/_asyncio.so:task_step")`). The design is described in
  [`devdocs/python-asyncio-context-propagation.md`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/devdocs/python-asyncio-context-propagation.md).
  **Support boundary:** OBI's own support matrix lists asyncio propagation as
  Python 3.9+ **with `uvloop` only**
  ([`SUPPORT_MATRIX.md`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/SUPPORT_MATRIX.md), "Context Propagation
  Frameworks"). The devdoc describes the same probes for plain asyncio, but
  treat that path as unsupported.
- The table carries variants for Python versions and compilers: `task_step` for
  `< 3.12` vs `>= 3.12`, `context_run.lto_priv.0` for Python 3.14 built with LTO,
  and `context_new_from_vars` where `PyContext_CopyCurrent` was tail-call
  optimised.
- Every Python probe is `Required: false`: if a symbol is missing, OBI skips that
  probe and carries on.
- **[observed] This is where symbols matter in this demo.**
  - All four Python services (`recommendation`, `product-reviews`, `llm`,
    `load-generator`) load `libpython3.12.so.1.0` and `_asyncio`.
  - Their base image is `python:3.12-alpine3.22`
    ([`src/recommendation/Dockerfile:17`](src/recommendation/Dockerfile)), and
    its `libpython` is **stripped**: no `.symtab`, only the 2,097 exported
    dynamic symbols.
  - So `PyContext_CopyCurrent` (exported) can be hooked, but `task_step`,
    `context_run` and `_asyncio_Task___init__` (static, unexported) cannot.
  - It doesn't hurt here: these services serve gRPC from a thread pool, not
    asyncio (`grpc.server(futures.ThreadPoolExecutor(max_workers=10))` at
    [`src/recommendation/recommendation_server.py:189`](src/recommendation/recommendation_server.py)
    and
    [`src/product-reviews/product_reviews_server.py:361`](src/product-reviews/product_reviews_server.py)).
    Thread-based correlation covers that model.
  - For an asyncio service, use an image whose `libpython` keeps its symbol table
    (or ships debug symbols).
  - [observed] **The base image decides this.** The official `python:3.12-slim`
    image is stripped the same way (0 static symbols, so no `context_run`,
    `task_step` or `_asyncio_Task___init__`). The full `python:3.12` image
    keeps them (54,691 symbols, all four present). Check an image with
    `docker run --rm --entrypoint cat <image> /usr/local/lib/libpython3.12.so.1.0 > lp.so && nm -a lp.so | grep -w context_run`
    (adjust the path and version; no output means stripped).

**Java: two mechanisms, a USDT probe and an injected agent.**

1. **USDT probes for runtime metrics.** HotSpot's `libjvm.so` ships
   statically-defined tracepoints: an ELF `.note.stapsdt` table of named probe
   sites, a stable cousin of symbols. With `application_runtime` enabled, OBI
   attaches to `hotspot:mem__pool__gc__begin` and `mem__pool__gc__end`
   ([`generictracer.go:560-585`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/internal/ebpf/generictracer/generictracer.go#L560-L585),
   [`bpf/generictracer/jvm.c`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/bpf/generictracer/jvm.c)) to produce the
   `jvm.memory.*` metrics seen in Dynatrace. [observed] `ad`'s
   `/opt/java/openjdk/lib/server/libjvm.so` (Eclipse Temurin 21,
   [`src/ad/Dockerfile:23`](src/ad/Dockerfile)) carries `.note.stapsdt`.
2. **The OBI Java agent (dynamic attach).** Bytecode is compiled at run time by
   the JIT, so there are no stable machine-code symbols for uprobes. OBI instead
   loads a small Java agent into the running JVM, without a restart or a
   `-javaagent` flag.
   - **Attach:**
     [`pkg/internal/java/java_inject.go`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/internal/java/java_inject.go)
     copies the embedded `obi-java-agent.jar` into the container's `/tmp`
     (`copyAgent`, line 244). It then uses the JVM Attach API (HotSpot's
     `/tmp/.java_pid<N>` socket; `attachJDKAgent`, line 329) to send
     `load instrument false <jar>`.
   - **Instrument:** the agent
     ([`Agent.java`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/internal/java/agent/src/main/java/io/opentelemetry/obi/java/Agent.java),
     `agentmain`, line 157) uses ByteBuddy to wrap:
     - `SSLSocket`, `SSLEngine`, `SocketChannel` and Netty's `SslHandler`, to
       read TLS plaintext;
     - `Runnable`, `Callable`, `Executor`, `ForkJoinTask` and virtual threads,
       to follow a request across thread pools (this is OBI's "Java thread
       pools" context support).
   - **Report:** the agent talks to the kernel side through a JNI call to
     `ioctl()` with magic command `0x0b10b1`
     ([`io_opentelemetry_obi_java_jni.c:32`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/internal/java/agent/src/main/c/io_opentelemetry_obi_java_jni.c)).
     OBI catches it with `SEC("kprobe/sys_ioctl")` in
     [`bpf/generictracer/java_tls.c:55`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/bpf/generictracer/java_tls.c).
   - It is on by default (`javaagent.enabled`, `OTEL_EBPF_JAVAAGENT_ENABLED`,
     [`pkg/obi/config.go:351-354`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/obi/config.go#L351-L354)).

   [observed] In the running `ad` pod, `/tmp` holds `.java_pid1`,
   `obi-java-agent.jar` and two `libobijni*.so` copies. OBI's log shows
   `OpenTelemetry eBPF Java Agent already loaded, not reloading` for `ad`,
   `fraud-detection` and `kafka`: the agent outlives an OBI restart because it
   lives inside the JVM.

> **Honest note: `ad` and `fraud-detection` also carry the upstream
> OpenTelemetry Java agent.** Their images set
> `JAVA_TOOL_OPTIONS=-javaagent:/usr/src/app/opentelemetry-javaagent.jar`
> ([`src/ad/Dockerfile:31-32`](src/ad/Dockerfile),
> [`src/fraud-detection/Dockerfile:21-22`](src/fraud-detection/Dockerfile)).
> [observed] It is loaded in `ad`'s JVM, with its exporters set to `none`, so it
> instruments but sends nothing. For these two services "no agent" is only true
> of *exported* telemetry. Two agents share the JVM. OBI still instruments them
> because its "already OTel-instrumented" skip
> (`exclude_otel_instrumented_services`) keys on the OTLP export requests it
> actually observes from each process
> ([`checkIfExportsOTel`, `pkg/ebpf/common/pids.go:143-144`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/ebpf/common/pids.go#L143-L144)),
> and there are none.

**Contrast:** Go needs neither trick. OBI reads Go's symbol table and runtime
structures directly and attaches uprobes to library functions such as
`net/http` and `google.golang.org/grpc`. Node.js gets an injected script
(`pkg/internal/nodejs`) for async-hook context and event-loop metrics.

---

## Architecture of this deployment

```
 Astronomy Shop (otel-demo namespace, ~25 workloads, many languages)
        │   unmodified services — no telemetry leaves them
        ▼
   OBI DaemonSet (obi namespace) ── eBPF on the node ──► OTLP/HTTP ──► Dynatrace
        ▲
   Argo CD  ◄── k8s/obi/values.yaml  (OBI Helm chart 0.13.0, app v0.12.2)
```

- Cluster: single-node Kubernetes (OrbStack on macOS, Linux 7.0 kernel); also
  suitable for any Linux node ≥ 5.8 with BTF.
- GitOps: Argo CD applies `k8s/overlays/demo` (the shop) and `k8s/argocd/obi-application.yaml`
  (OBI, from the official Helm chart + `k8s/obi/values.yaml`).
- OBI exports **directly** to Dynatrace; credentials live in a Kubernetes Secret
  created out-of-band (never committed) — see [`k8s/obi/README.md`](k8s/obi/README.md).
- **Any OTLP backend works.** Dynatrace is only the destination chosen here.
  Changing it is a Secret update, plus protocol and temporality in
  `values.yaml`: step-by-step instructions, common vendor values (Grafana Cloud,
  Honeycomb, New Relic, Elastic, Dash0, any OpenTelemetry Collector), a
  per-signal Jaeger + Prometheus example and multi-backend fan-out are in
  [`k8s/obi/README.md` → Sending OBI data to any OTLP backend](k8s/obi/README.md#sending-obi-data-to-any-otlp-backend).

### OBI is the only telemetry source (and how that's enforced)

The shop's services still contain their original OpenTelemetry SDK code. To make
the data in Dynatrace **purely OBI-produced**, four controls are applied:

| Control | Where | Effect |
|---|---|---|
| App-side collector no longer forwards to Dynatrace and **no longer listens for OTLP** | `k8s/overlays/demo/patches/collector-dynatrace-export.yaml` | Any SDK export is refused at connect; nothing app-side is delivered |
| `OTEL_*_EXPORTER=none` on every service | `k8s/overlays/demo/patches/disable-app-sdk.yaml` | Silences SDKs that honour the standard env vars |
| `OTEL_SDK_DISABLED=true` on every service except `email`, `cart`, `ad` | `k8s/overlays/demo/patches/sdk-disabled.yaml` | Turns off SDKs that honour the flag (`email`/Ruby and `cart`/.NET crash with it) |
| OBI filters drop traffic to/from the collector | `k8s/obi/values.yaml` (`filter.application`, `filter.network`, `filter.stats`) | Stops OBI recording the remaining export attempts as spans, flows or TCP failed-connection stats. Spans are matched on `server.address` **and** the OTLP gRPC method, because a refused connection has an empty `server.address` |

> **Honest note:** the env vars alone are *not* enough. [observed] Even with both
> set, `frontend-proxy` (Envoy's built-in tracer), `shipping` (Rust),
> `product-catalog`, `checkout` and `flagd` (Go) and Jaeger kept opening
> connections to the collector, and `cart` has its metrics exporter deliberately
> re-enabled to avoid a .NET SDK crash. All of these are refused at the collector,
> and the OBI filters keep them out of the data. The filters are per family in
> OBI v0.12.2: `filter.application` alone does not touch flows or TCP stats.
> Removing the SDKs from the source would need rebuilt images.

Verify in Dynatrace. Scope by **namespace**, not by `k8s.cluster.name`: only
OBI sets the cluster name, so a cluster filter would hide SDK spans and the check
could never fail.

```dql
fetch spans, from: now() - 15m
| filter k8s.namespace.name == "otel-demo"
| summarize spans = count(),
    by:{source = if(telemetry.distro.name == "opentelemetry-ebpf-instrumentation", "OBI (eBPF)", else: "anything else")}
```

Use **equality** on `telemetry.distro.name`. `isNotNull(...)` is wrong: other
auto-instrumentation agents set their own distro names. Spans only — metrics are
identified by instrumentation scope (`go.opentelemetry.io/obi`,
`network_ebpf_events`, `stats_ebpf_events`).

---

## What OBI captured in this demo

Languages as **classified by OBI** (a runtime classification, not a statement
about how the service was built). Observed on this deployment:

| OBI classification | Services | What the data shows |
|---|---|---|
| Go | `product-catalog`, `checkout`, `flagd` | Client + server spans, parent/child links; richest coverage |
| Java | `ad`, `fraud-detection`, `kafka` | Spans plus **JVM memory** runtime metrics |
| .NET | `accounting` | Kafka consumer spans, PostgreSQL `INSERT` client spans |
| Node.js | `frontend` (Next.js), `payment` | HTTP/gRPC spans plus **event-loop** metrics |
| Python | `recommendation`, `product-reviews`, `llm`, `load-generator` | HTTP/gRPC spans |
| PHP / Ruby / Rust | `quote` / `email` / `shipping` | Protocol-level spans |
| Native / "generic" | `currency` (C++), `cart`, `postgresql`, `valkey-cart` (Redis), Envoy `frontend-proxy` | Protocol-level spans; DB/cache semantics decoded |

Beyond request telemetry, OBI also produced (all from the kernel, none requiring
workload cooperation): network flow bytes and packets, TCP RTT / retransmits /
failed connections / I/O, a service graph, DNS lookup timing and node-level host
info.

An importable Dynatrace notebook that renders all of this live —
**25 DQL tiles + explanatory text** — is in
[`docs/obi/dynatrace-notebook.json`](docs/obi/dynatrace-notebook.json).

---

## Network layer (L3 / L4) data

**Yes — it is feasible, and it is enabled here.** OBI captures network-layer data
independently of application protocols, using an eBPF packet hook (a **socket
filter** by default in v0.12.2; TC with `network.source: tc`) plus TCP socket
statistics.

[docs] The `network` entry in `OTEL_EBPF_METRICS_FEATURES` turns flows on
(`network.enable` is a deprecated alias in v0.12.2, kept here because chart
0.13.0 keys its hostNetwork mounts off `config.data.network`). By default flows
carry only Kubernetes owner/namespace/type and direction; IPs, ports and transport
are opt-in via `attributes.select`. This repo enables them and labels addresses
by CIDR ([`k8s/obi/values.yaml`](k8s/obi/values.yaml)):

```yaml
env:
  OTEL_EBPF_METRICS_FEATURES: "application,…,network,network_flow_packets,stats"
config:
  data:
    attributes:
      select:
        obi_network_flow_bytes:
          include: [direction, transport, src.address, dst.address, dst.port,
                    src.cidr, dst.cidr, k8s.src.name, k8s.dst.name, …]
    network:
      cidrs:            # narrowest match wins
        - {cidr: 192.168.194.0/25,   name: pods}
        - {cidr: 192.168.194.128/25, name: services}
        - {cidr: 192.168.139.0/24,   name: node}
        - {cidr: 0.0.0.0/0,          name: external}
    stats:
      cidrs: …          # same list
```

| Metric | Meaning | Key attributes |
|---|---|---|
| `obi.network.flow.bytes` | L3/L4 bytes between two endpoints | source/destination workload, pod, namespace, owner type, IP, port, CIDR name, transport, direction |
| `obi.network.flow.packets` | Packet counts | same |
| `obi.stat.tcp.rtt` | TCP round-trip time | source/destination workload, IPs |
| `obi.stat.tcp.retransmits` | TCP retransmissions | same |
| `obi.stat.tcp.failed.connections` | Failed connection attempts | destination workload, IPs |
| `obi.stat.tcp.io` | Bytes at the socket layer | same |

Observed here (before the IP/port/CIDR attributes were added): **182 distinct
workload-to-workload flows in 30 minutes**, spanning `otel-demo`, `argocd` and
`kube-system`. The single largest destination had no Kubernetes owner (node or
external traffic) and could not be identified without addresses, which is why
the CIDR labels above were added. `direction` is `request`/`response` from the
observed TCP handshake and `unknown` when OBI did not see the connection start.

With addresses, ports and CIDR labels on (2026-10-02, 6 min) the "unknown"
traffic resolved immediately:

- The largest `node → external` flow (TCP 443) is **OBI's own OTLP export to
  Dynatrace**, about 3.5 MB/min (~5 GB/day) for this one-node demo. OBI runs on
  the host network, so it appears as owner `orbstack` (the node). It is left in
  deliberately; it is a real cost of the agent.
- The load generator's headless Chromium sends QUIC (UDP 443) to Google
  addresses, which shows up as both a pod flow and a node flow, after SNAT.

That is the kind of answer L3/L4 data gives that request traces cannot.

Things to know before enabling it elsewhere:

- It sees the **whole node**, not just instrumented namespaces. Scope it with
  `filter.network`. The chart's default filter matches **owner names** (`kube*`,
  `*prometheus*`, agents), not namespaces, so `kube-system` workloads such as
  CoreDNS still appear; this repo adds `otel-collector*`. Filter on
  `k8s_src_namespace`/`k8s_dst_namespace` if you need namespace scoping.
- Needs `hostNetwork` and elevated capabilities (the Helm chart sets these) and a
  kernel with BTF. [docs] The socket-filter source needs `BPF` + `NET_RAW`; the
  TC source needs `BPF` + `NET_ADMIN` + `PERFMON`.
- It counts bytes and packets; it does **not** read payloads, so it works for
  encrypted traffic but says nothing about the request inside it.
- Cardinality grows with workload pairs, and much faster with IPs and ports —
  filter accordingly.
- If the cluster runs **Cilium**: OBI attaches TC programs only with
  `network.source: tc` or when context propagation is on (it is, here, with
  `all`). They coexist when both use TCX (kernel ≥ 6.6, the default in recent
  Cilium). See the OBI/Cilium compatibility page in the upstream docs.

---

## Honest limits

Measured on this deployment (single node, 2026-10-01); treat as indicative.

- **Trace stitching is partial, and depends on context propagation.** OBI's
  v0.12.2 default is propagation **disabled** (the chart's
  `contextPropagation.enabled` only grants privileges). [observed] Excluding
  health-probe traces:

  | Services per trace | Propagation off (30 min) | `all` (9 min) |
  |---|---|---|
  | 1 | 82% | 78% |
  | 2–3 | 11% | 9% |
  | 4–6 | 7% | 12% |
  | 10–11 (full checkout path) | 0 | 1.1% |

  Turning it on produced complete 10–11-service checkout traces that never
  appeared before, but most traces are still single-service. The first
  (pre-correction) measurement, ~81/16/3%, also counted health probes. The span
  error rate did not change because of it (all errors were refused OTLP-export
  spans, since filtered).
- **No business context.** OBI cannot attach application-level attributes (for
  example the currency codes in a conversion call). Custom spans need an SDK.
- **gRPC method names can be lost** for some native services (`currency`: ~99% of
  spans reported the method as `*`).
- **Service naming fallbacks.** Health-probe traffic to the demo's bundled
  Prometheus, Grafana and Jaeger (~5.5k spans / 30 min) was reported under the
  namespace name `otel-demo`. Those backends are idle in OBI-only mode and are now
  excluded from instrumentation.
- **SQL text is off by default.** Spans carry operation and table. Full
  `db.query.text` is opt-in (`attributes.select.traces.include: [db.query.text]`)
  and may contain literals or PII. Statements prepared before OBI started (for
  example by a connection pool at boot) can lack text until reconnect.
- **Runtime metrics are limited by design:** v0.12.2 defines runtime metrics
  only for Go, the JVM and Node.js (none for .NET or Python). JVM and Node.js were
  received; the missing Go runtime metrics are an open gap.
- **OBI skips services it detects as already OpenTelemetry-instrumented by
  default** (`exclude_otel_instrumented_services`).
- **Resource cost depends on process churn, not just traffic.** Headless-browser
  processes in the load generator made OBI restart repeatedly (OOM at 6 GiB);
  excluding them brought it to a steady ~300 MiB.

---

## Repository map

| Path | What |
|---|---|
| `k8s/obi/values.yaml` | **OBI configuration** (Helm values) |
| `k8s/obi/README.md` | Operating OBI: secrets, config reference, troubleshooting |
| `k8s/argocd/obi-application.yaml` | Argo CD Application that deploys OBI |
| `k8s/overlays/demo/` | The shop (Kustomize overlay); collector and SDK-silencing patches in `patches/` |
| `docs/obi/how-obi-instruments.md` | Deep dive: mechanics, per-language behaviour, protocols, limits |
| `docs/obi/dynatrace-notebook.json` | Importable Dynatrace notebook (live DQL) |
| `docs/UPSTREAM_README.md` | Original OpenTelemetry Demo README |

Other directories (`src/`, `pb/`, …) are the upstream demo's services. This fork
also carries unrelated experiments (`GEOMETRY-LAB.md`); they are not part of the
OBI work.

## References

- [OBI documentation](https://opentelemetry.io/docs/zero-code/obi/)
- [OBI distributed traces / context propagation](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/)
- [OBI and Cilium compatibility](https://opentelemetry.io/docs/zero-code/obi/cilium-compatibility/)
- [OpenTelemetry Demo (upstream)](https://github.com/open-telemetry/opentelemetry-demo)
