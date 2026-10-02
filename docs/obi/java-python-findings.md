# OBI on Spring Boot 3 and FastAPI: measured results

What OBI (v0.12.2, chart 0.13.0, context propagation `all`) captures from a
Spring Boot 3 / Java 21 service and an async Python (FastAPI on uvloop)
service that contain **no OpenTelemetry agent, SDK or library**. It also shows
what the upstream OpenTelemetry Java agent captures from the *same* image, for
comparison.

Everything here was measured on this repo's single-node OrbStack cluster on
2026-10-02 (experiment window 16:21–16:33 UTC). Numbers are indicative, not
benchmarks. The services live in [`src/warehouse`](../../src/warehouse) and
[`src/fx`](../../src/fx); their manifests are in
[`k8s/base/obi-showcase`](../../k8s/base/obi-showcase).

## The test services

```
obi-traffic (curl loop)
   │ HTTP
   ▼
warehouse  — Spring Boot 3.5, Java 21, Spring MVC on virtual threads
   ├─ JDBC (HikariCP) ─────────────► postgresql
   ├─ HTTPS (two client styles, two thread models) ─► fx-full / fx-slim
   │                                     │ asyncio.gather(2 × HTTP)
   │                                     └────────► warehouse /api/rates/{ccy}
   └─ spring-kafka publish ─► kafka ─► @KafkaListener ─► JDBC INSERT
```

- **warehouse:** `POST /api/stock/{sku}/reserve` does a JDBC update, an HTTPS
  call to fx, and a Kafka publish. Two switches control the HTTPS call:
  - `client=jdk` (java.net.http `HttpClient`, async internally) or
    `client=blocking` (`HttpsURLConnection`);
  - `hop=pool` (`CompletableFuture.supplyAsync` on a fixed platform-thread pool)
    or `hop=inline` (on the request's virtual thread).
- **fx-full / fx-slim:** identical FastAPI code on uvloop, served over HTTPS.
  `fx-full` runs on `python:3.12`, whose `libpython` keeps its symbol table.
  `fx-slim` runs on `python:3.12-slim`, which is stripped.
- **Traffic:** each loop exercises every switch combination once, under its own
  SKU. It also fires 16 concurrent requests straight at each fx service, so
  asyncio tasks interleave on the event loop.

## What OBI captured, with zero instrumentation

| Capability | Result |
|---|---|
| Spring MVC server spans on **virtual threads** | ✅ `POST /api/stock/*/reserve`, `GET /api/stock/*` |
| **JDBC** (PostgreSQL wire protocol) | ✅ `SELECT`/`UPDATE`/`INSERT warehouse.*` client spans with operation and table |
| **Java TLS client** (JDK HttpClient and HttpsURLConnection) | ✅ decoded `GET /convert` client spans. TLS is read through OBI's injected Java agent |
| **Python TLS server** (uvicorn + OpenSSL) | ✅ decoded `GET /convert` server spans, via OpenSSL uprobes |
| **Kafka** (spring-kafka, kafka-clients) | ✅ `publish warehouse-events` / `process warehouse-events` with topic |
| Route names | ⚠️ heuristic: the same endpoint showed as both `/api/rates/*` and `/api/rates/EUR`. Fixed with `routes.patterns` in `k8s/obi/values.yaml` |
| SQL text | Off by default (`db.query.text` is opt-in); enabled in `values.yaml` for this demo |

## Trace linking: where OBI connects the dots and where it doesn't

### Java: warehouse → fx

Each row is about 90 `reserve` requests:

| HTTPS call style | Linked to fx | Full chain (warehouse → fx → warehouse) |
|---|---|---|
| `HttpsURLConnection`, **inline on the request thread** → fx-full | **97%** (87/90) | **93%** (84/90) |
| same → fx-slim | 97% (83/86) | 88% (76/86) |
| `HttpsURLConnection` via **`CompletableFuture` on a thread pool** | **0%** (0/89) | 0% |
| **JDK `HttpClient`**, on a pool thread | **0%** (0/89) | 0% |
| **JDK `HttpClient`**, inline | **0%** (0/88) | 0% |

Other links, within warehouse:

| Link | Result |
|---|---|
| Request → its JDBC calls (same thread) | ✅ ~100% |
| Request → Kafka publish | ❌ 0 of ~440. `KafkaTemplate.send` does its socket I/O on the producer's network thread |
| Kafka consume → JDBC `INSERT` in the listener | ❌ separate traces |

**Takeaways for a Spring estate:**
- OBI links calls made **synchronously on the request thread**. That covers Spring
  MVC with blocking clients such as `RestTemplate`, `RestClient` on
  `SimpleClientHttpRequestFactory`, or JDBC.
- It **loses the link when work moves to another thread**:
  - a thread-pool hop (`CompletableFuture`/`@Async`), even though OBI's support
    matrix lists Java thread-pool propagation as stable;
  - a client that does I/O on its own threads (java.net.http `HttpClient`,
    WebClient/Netty, the Kafka producer).

  In this configuration, those calls appear as separate root traces.

### Python asyncio: symbols decide

Concurrent requests straight to fx; each correct trace has exactly 2 child calls:

| Image | Correct | Context lost (0 children) | Misattributed (1 or 3+ children) |
|---|---|---|---|
| fx-full: `python:3.12` (symbols present) | **92.3%** (2517/2726) | 5.0% | 2.7% |
| fx-slim: `python:3.12-slim` (stripped) | **68.1%** (1165/1711) | 18.2% | 13.7% |

With low concurrency, both images link almost perfectly: requests don't
interleave, so plain thread-based correlation works. Under load, the stripped
image loses or misattributes about a third of the child calls. OBI's asyncio
tracking hooks CPython's `task_step`, `context_run` and `_asyncio_Task___init__`,
which only exist in an unstripped `libpython` (see the root README, *Inside the
runtime*). A misattributed call is worse than a lost one: it shows up under the
**wrong** request.

## The same service under the OpenTelemetry Java agent

A temporary copy of the same warehouse image, run with the upstream
OpenTelemetry Java agent 2.23.0 and exporting to the in-cluster Jaeger, received
the same traffic. The copy has since been deleted. Results over 410 `reserve`
traces (82 per call style):

| | OBI (no code) | OTel Java agent |
|---|---|---|
| warehouse → fx linked, inline blocking call | 97% | 100% |
| warehouse → fx linked, thread-pool hop | 0% | **100%** |
| warehouse → fx linked, JDK HttpClient | 0% | **100%** |
| Kafka publish → consume → `INSERT` in one trace | no | **yes** |
| Route template | heuristic (`/api/stock/*/reserve`; patterns configurable) | exact (`/api/stock/{sku}/reserve`) |
| SQL | operation + table; full text opt-in | sanitized `db.statement` (`… WHERE sku = ?`) |
| Outbound URL | path | full URL (`https://fx-full:8443/convert?...`) |
| Thread names, code attributes | no | yes (`thread.name`) |
| TLS-encrypted calls decoded | yes (injected agent + uprobes) | yes (library level) |
| Changes to the image or JVM flags | none | `-javaagent` (`JAVA_TOOL_OPTIONS`) |
| Polyglot coverage from one install | every process on the node | Java only |

**Running both on one service duplicated telemetry.** OBI kept instrumenting the
agent-instrumented copy (about 10,000 OBI spans in 15 minutes). Its
`exclude_otel_instrumented_services` skip did not trigger for the agent's
OTLP/gRPC exports. One possible cause is that this repo's OBI filter drops
OTLP-export spans before the detection runs. Decide per service which source owns
it, rather than relying on the automatic skip.

## What to tell a Java/Python enterprise

1. **OBI gives broad, zero-touch coverage:** RED metrics, JDBC/Kafka/HTTP/gRPC
   spans, TLS visibility, flows and TCP stats for every process on the node, with
   no rollout to application teams.
2. **End-to-end traces only hold for synchronous, same-thread call paths.** Async
   clients, thread-pool hops and messaging break the chain in these tests. For
   critical Spring flows that rely on those patterns, the OTel Java agent (or SDK)
   still gives the complete trace.
3. **For async Python, the base image is an architecture decision.** Slim and
   Alpine images strip the symbols OBI needs.
4. **A pragmatic model:** OBI everywhere for the baseline, plus the agent on the
   handful of services where full distributed traces matter. Assign each service
   to exactly one trace source.
