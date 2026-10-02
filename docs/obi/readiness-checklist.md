# OBI readiness checklist for Java and Python estates

A pre-adoption checklist for an organisation whose code is mostly Java (including
Spring Boot 3.x) and Python. Each item says **why it matters**, **how to check
it**, and **where the evidence comes from**. The evidence tags are:
- [matrix]: OBI v0.12.2 [`SUPPORT_MATRIX.md`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/SUPPORT_MATRIX.md)
- [source]: OBI v0.12.2 code
- [measured]: [`java-python-findings.md`](java-python-findings.md)
- [observed]: seen in this repo's cluster
- [untested]: inference only

## Platform

- [ ] **Kernel and BTF.** Linux ≥ 5.8 with BTF (`ls /sys/kernel/btf/vmlinux`).
  RHEL/Rocky/Alma 8 (4.18 with backports) is the documented exception. amd64 or
  arm64 only. [matrix]
- [ ] **Privileges and security review.** OBI is a privileged DaemonSet. It:
  - loads eBPF programs into the kernel;
  - writes into process memory (`bpf_probe_write_user`) for Go context propagation;
  - **injects a Java agent into running JVMs**;
  - with the log enricher, rewrites application log writes.

  Get explicit sign-off; on OpenShift you need a privileged SCC. [source, observed]
- [ ] **Version pinning.** OBI is pre-1.0 and config keys change between releases
  (e.g. `network.enable` is deprecated in v0.12.2; the public docs page cites a
  key that no longer exists). Pin the chart and image, and re-test every upgrade.
  [source]
- [ ] **Validate config before rollout.** Run the release's binary against the
  rendered config: it rejects bad keys at startup. This repo caught a wrong
  `log_enricher` selector that way. [observed]
- [ ] **Backend limits and cost.**
  - High-cardinality flow attributes (e.g. `src.port`) exceeded Dynatrace's 4 MiB
    OTLP request limit, and every flow export failed. [observed]
  - OBI's own export was ~5 GB/day from one demo node. Size per node. [observed]

## Java / Spring Boot 3

- [ ] **Existing OTel instrumentation.** Find services that already export OTLP
  (Micrometer Tracing with an OTLP exporter, or the OTel agent). Assign each
  service to exactly one trace source. OBI's automatic skip
  (`exclude_otel_instrumented_services`) did not trigger for an agent exporting
  OTLP/gRPC in this repo. [measured]
- [ ] **JVM attach must be allowed.** OBI loads its Java agent through the attach
  API. Without it you lose TLS plaintext and thread-pool context.
  - `-XX:+DisableAttachMechanism` blocks it. [source]
  - It needs a writable `TMPDIR`, `/tmp` or `/var/tmp` in the container, which
    matters with `readOnlyRootFilesystem` (mount an `emptyDir`). [source]
  - It can be disabled with `OTEL_EBPF_JAVAAGENT_ENABLED=false`. [source]
- [ ] **Threading model of critical flows.**
  - Synchronous calls on the request thread link end to end (97% in tests).
  - Thread-pool hops (`CompletableFuture`/`@Async`), the JDK `HttpClient`,
    WebClient/Netty and the Kafka producer did **not** link (0%). [measured]
  - Inventory which business flows depend on these patterns.
- [ ] **Virtual threads (Java 21).** Request handling on virtual threads works
  [measured]. Log enrichment is skipped on virtual threads [matrix].
- [ ] **GraalVM native images** (Spring Boot 3 AOT) have no JVM: no Java agent, no
  JVM metrics, protocol-level visibility only. [untested]
- [ ] **gRPC between Java services.** Context propagation for gRPC and HTTP/2 is
  Go-only, so Java gRPC chains will not stitch. [matrix]
- [ ] **Route cardinality.** OBI cannot see `@GetMapping` templates. Configure
  `routes.patterns` (e.g. `/api/stock/:sku`), or use `unmatched: low-cardinality`.
  [source, measured]
- [ ] **JDBC.** PostgreSQL, MySQL and MSSQL are decoded. SQL text is off by
  default; enabling `db.query.text` can expose literals and PII. Statements
  prepared before OBI started (HikariCP prepares at boot) may lack text. [matrix,
  source]
- [ ] **JVM runtime metrics expectations.** Only four `jvm.memory.*` metrics
  (Experimental), from HotSpot DTrace/USDT probes. There are no thread, CPU or
  GC-pause metrics as with the OTel agent. Check that your JDK build has
  `.note.stapsdt` in `libjvm.so`. [matrix, observed]

## Python

- [ ] **Concurrency model per service.**
  - Sync workers (gunicorn sync, gRPC thread pools) correlate by thread: fine.
  - asyncio needs `uvloop` per the support matrix [matrix], plus unstripped
    CPython symbols (next item).
  - gevent and eventlet are out of scope. [source devdoc]
- [ ] **Base image symbols.** `python:*-slim` and `-alpine` strip `libpython`.
  Under concurrency, async context linking fell from 92% (full image) to 68% (slim),
  with 14% of child calls attached to the wrong request. [measured] Check with:
  `docker run --rm --entrypoint cat <img> /usr/local/lib/libpython3.12.so.1.0 > lp.so && nm -a lp.so | grep -w context_run`.
- [ ] **TLS libraries.** Python's `ssl` module (system OpenSSL) was decoded
  [measured]. Wheels that statically bundle their own TLS (for example grpcio's
  BoringSSL) are not covered by the OpenSSL uprobes. [untested]
- [ ] **Runtime metrics.** OBI has none for Python. [source]
- [ ] **Logs.** For trace/log correlation via the log enricher, set
  `PYTHONUNBUFFERED=1`. [source devdoc]

## Operating model

- [ ] **Decide the hybrid split.** Use OBI for baseline coverage everywhere, and the
  OTel agent or SDK where complete distributed traces, business attributes or
  exception stack traces are required.
- [ ] **Exclude noise.** Health probes, bundled backends, short-lived process trees
  (e.g. headless browsers) and the telemetry pipeline's own traffic all show up
  unless filtered. Use `discovery.exclude_instrument` and
  `filter.application`/`.network`/`.stats`, which are separate filter families.
  [observed]
- [ ] **Resource limits** sized above the observed peak during pod-churn events,
  not steady state. OBI peaked above 4 GiB while re-attaching to many restarting
  processes. [observed]
