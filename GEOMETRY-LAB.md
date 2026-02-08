# Anti-Pattern Geometry Lab

*An extension of the [OpenTelemetry Astronomy Shop](https://opentelemetry.io/docs/demo/) that produces controlled, feature-flagged distributed system anti-patterns for detection testing and observability research.*

---

## What Is This?

The Anti-Pattern Geometry Lab adds new microservices to the standard OTel Astronomy Shop demo. Each new service has a clear e-commerce business purpose and works correctly by default. When a feature flag is enabled, the service produces a specific anti-pattern with a characteristic **trace geometry** — a measurable structural signature in the distributed trace tree.

This creates ground truth for:
- Testing anti-pattern detection tools and APM features
- Benchmarking observability platforms against known patterns
- Researching trace topology analysis and autonomous detection
- Teaching distributed systems anti-patterns with live, observable examples

All anti-patterns are behind [flagd](https://flagd.dev) feature flags. The demo runs cleanly with all flags off.

---

## The Shapes

Every distributed system anti-pattern produces a characteristic shape in trace topology space. We define shape using four geometric dimensions:

| Dimension | What It Measures |
|-----------|-----------------|
| **Fan-out** | How many child spans does the parent produce? Does it scale with input? |
| **Homogeneity** | Are the children the same operation, or different operations? |
| **Temporality** | Are children sequential (one after another) or concurrent (overlapping)? |
| **Scaling** | Is fan-out bounded (fixed) or unbounded (grows with input)? |

Additional dimensions for specific patterns: duration distribution across traces, error propagation, parent-child duration ratio.

---

## Phase 1 Services

| Service | Language | Shape | Anti-Pattern | Feature Flag |
|---------|----------|-------|-------------|-------------|
| **inventoryservice** | Java | 🪜 Staircase | Retry storm with exponential backoff | `inventoryServiceRetryStorm` |
| **reviewservice** | Python | 🪥 Comb | N+1 individual fetches per review | `reviewServiceNPlusOne` |
| **notificationservice** | Python | 👻 Ghost | Fire-and-forget with orphaned spans | `notificationServiceFireAndForget` |
| **searchservice** | Node.js | 🍭 Lollipop | Sync-over-async event loop block | `searchServiceSyncOverAsync` |
| **userservice** | .NET/C# | ⏳ Hourglass | Connection pool exhaustion | `userServicePoolExhaustion` |
| **orderservice** | Go | 🁡 Domino Chain | Cascading failure without circuit breakers | `orderServiceCascadingFailure` |
| **pricingservice** | Go | 🌀 Expanding Fan | Unbounded concurrent goroutine blast | `pricingServiceUnboundedFanOut` |

### Pre-existing Anti-Patterns (from upstream experiments)

| Service | Language | Shape | Feature Flag |
|---------|----------|-------|-------------|
| **checkoutservice** | Go | 🪥 Comb | *(natural — always active)* |
| **recommendationservice** | Python | 🪥 Comb | `recommendationServiceNPlusOne` |
| **adservice** | Java | 🪥 Comb | `adServiceNPlusOne` |

---

## Shape Catalog

### 🪥 The Comb (N+1 Query / Chatty API)

**What it looks like:** One parent span with N identical sequential child spans. Each child calls the same downstream service. Inter-span gaps are tiny (~15μs) — the signature of a `for` loop.

**Why it's bad:** N network round trips when 1 batch call would suffice. Latency scales linearly with collection size.

```
Parent
├─ GetProduct (item 1)    ~0.5ms
├─ GetProduct (item 2)    ~0.5ms
├─ GetProduct (item 3)    ~0.5ms
├─ GetProduct (item 4)    ~0.5ms
└─ GetProduct (item 5)    ~0.5ms
```

**Geometric signature:** Linear fan-out, high homogeneity, sequential temporality, data-proportional scaling.

**Services:** checkoutservice (Go), recommendationservice (Python), adservice (Java), reviewservice (Python)

---

### 🪜 The Staircase (Retry Storm)

**What it looks like:** One parent span with N sequential child spans (retry attempts). Inter-span gaps grow exponentially — ~150ms, ~300ms, ~600ms, ~1200ms. The "steps" get wider.

**Why it's bad:** Retries without circuit breakers amplify load on already-failing services. Exponential backoff helps but doesn't prevent the cascade.

```
Parent (~3s total)
├─ attempt [0]    ~5ms  [gap: 150ms]
├─ attempt [1]    ~5ms  [gap: 300ms]
├─ attempt [2]    ~5ms  [gap: 600ms]
├─ attempt [3]    ~5ms  [gap: 1200ms]
└─ attempt [4]    ~5ms  (success)
```

**Geometric signature:** Bounded fan-out (retry count), high homogeneity, sequential with exponentially growing gaps, not data-proportional.

**Key distinction from Comb:** Gaps are milliseconds (not microseconds) and grow exponentially. Fan-out is bounded by retry config, not input data.

**Services:** inventoryservice (Java)

---

### 👻 The Ghost (Fire-and-Forget)

**What it looks like:** A parent span that completes quickly (~5ms) with child spans that complete much later (200-800ms). The parent returns "success" before knowing whether children succeeded or failed.

**Why it's bad:** Errors in async dispatches are invisible. The parent reports success while notifications silently fail. Monitoring shows green dashboards while customers get nothing.

```
Parent (~5ms, SUCCESS)          ← Returns immediately
├─ dispatch_email   (~400ms, completes AFTER parent)
├─ dispatch_sms     (~300ms, completes AFTER parent)
└─ dispatch_webhook (~600ms, ERROR — but parent already said "success")
```

**Geometric signature:** Parent duration << child duration. Children outlive parent. Error status on children doesn't propagate to parent.

**Services:** notificationservice (Python)

---

### 🍭 The Lollipop (Sync-over-Async)

**What it looks like:** A parent span with several fast children and ONE child that dominates total duration (>80%). The "stem" of the lollipop is a single blocking call.

**Why it's bad:** One synchronous block in an async context defeats the entire concurrency model. The event loop / thread pool is held hostage.

```
Parent (~2500ms)
├─ parse_query     ~1ms
├─ check_cache     ~2ms
├─ blocking_fetch  ~2500ms  ←←← THE STEM
└─ rank_results    ~3ms
```

**Geometric signature:** Low homogeneity (different operations), one child >80% parent duration, sequential.

**Key distinction from Comb:** One dominant child vs. many similar children. Low homogeneity vs. high homogeneity.

**Services:** searchservice (Node.js)

---

### ⏳ The Hourglass (Connection Pool Exhaustion)

**What it looks like:** Across many traces, request durations form a bimodal distribution — most complete in ~5ms (pool available) or ~5000ms (pool drained, waiting for timeout). Almost nothing in between.

**Why it's bad:** Under load, the pool drains and ALL new requests wait. Latency degrades catastrophically, not gradually.

```
Fast path:  GetUserProfile  ~5ms    (pool.acquire ~0.1ms)
Slow path:  GetUserProfile  ~5500ms (pool.acquire ~5000ms)
Timeout:    GetUserProfile  ~5001ms (pool.acquire TIMEOUT, ERROR)
```

**Geometric signature:** Bimodal duration distribution ACROSS traces. Single-trace tree structure looks normal — the pattern is in the population.

**Key distinction:** Requires analyzing duration distributions, not individual trace trees.

**Services:** userservice (.NET/C#)

---

### 🁡 The Domino Chain (Cascading Failure)

**What it looks like:** A sequential chain of service calls where one fails mid-chain, but subsequent calls still execute (and fail) instead of stopping early.

**Why it's bad:** Time and resources wasted on doomed calls. No circuit breaker, no fail-fast. Error propagates through the entire chain.

```
Parent (~2250ms, ERROR)
├─ validate_cart      ~20ms   OK        ← Standing
├─ reserve_inventory  ~25ms   OK        ← Standing
├─ process_payment    ~500ms  ERROR     ← FALLS
├─ arrange_shipping   ~800ms  ERROR     ← Toppled (wasted)
└─ send_confirmation  ~900ms  ERROR     ← Toppled (wasted)
```

**Geometric signature:** Sequential chain, mixed operations (low homogeneity), error inflection point with post-failure spans still executing.

**Key distinction from Comb:** Different operations (not homogeneous). Error propagation pattern is the defining feature.

**Services:** orderservice (Go)

---

### 🌀 The Expanding Fan (Unbounded Concurrency)

**What it looks like:** One parent span with N child spans that ALL start at approximately the same time. No concurrency limit — every item gets its own goroutine/thread/task.

**Why it's bad:** Overwhelms downstream services. Resource contention degrades performance for everyone. Later-launched concurrent calls take longer due to contention.

```
Parent (~200ms for 20 products)
├─ price.calculate [0]   ~50ms   ← All start at same time
├─ price.calculate [1]   ~55ms   ← All start at same time
├─ price.calculate [2]   ~48ms   ← All start at same time
... (20 concurrent)
├─ price.calculate [18]  ~190ms  ← Slower (contention)
└─ price.calculate [19]  ~210ms  ← Slower (contention)
```

**Geometric signature:** Linear fan-out, high homogeneity, **concurrent** temporality (all children start within microseconds), data-proportional scaling, duration degradation on later children.

**Key distinction from Comb:** Same fan-out and homogeneity, but CONCURRENT not SEQUENTIAL. The only geometric difference is temporality — this tests whether your detector can distinguish `for` loops from goroutine blasts.

**Services:** pricingservice (Go)

---

## Quick Start

### Prerequisites

- k3s/k3d cluster running the OTel Astronomy Shop
- kubectl configured
- flagd deployed (included in the demo)

### Enable Anti-Patterns

Enable individual patterns:
```bash
# Edit flagd ConfigMap
kubectl edit configmap flagd-config -n otel-demo

# Set any flag's defaultVariant to "on":
# "inventoryServiceRetryStorm": { "defaultVariant": "on", ... }

# Restart flagd to pick up changes
kubectl rollout restart deployment flagd -n otel-demo
```

Enable ALL patterns at once:
```bash
# Set all geometry-lab flags to "on" in the ConfigMap, then:
kubectl rollout restart deployment flagd -n otel-demo
```

### Verify

```bash
# Check all geometry lab services are running
kubectl get pods -n otel-demo | grep -E "inventory|review|notification|search|user|order|pricing"

# Check logs for anti-pattern activity
kubectl logs deployment/inventory -n otel-demo --tail=10
kubectl logs deployment/review -n otel-demo --tail=10
kubectl logs deployment/notification -n otel-demo --tail=10
kubectl logs deployment/search -n otel-demo --tail=10
kubectl logs deployment/user -n otel-demo --tail=10
kubectl logs deployment/order -n otel-demo --tail=10
kubectl logs deployment/pricing -n otel-demo --tail=10
```

### Observe

Traces flow to whatever backend your OTel Collector is configured to export to (Dash0, Jaeger, Datadog, etc.). Look for:
- Parent span names matching the service operations
- `app.*.mode` attributes indicating anti-pattern activation
- The characteristic geometric patterns described above

---

## Span Attribute Convention

All geometry lab services use a consistent attribute naming scheme:

| Attribute | Description | Example |
|-----------|-------------|---------|
| `app.{domain}.mode` | Current mode | `app.retry.mode = "retry_storm"` |
| `app.{domain}.outcome` | Result | `app.retry.outcome = "recovered"` |
| `app.retry.attempt` | Retry attempt number | `0`, `1`, `2`... |
| `app.retry.delay_ms` | Backoff delay | `150`, `300`, `600`... |
| `app.pool.wait_ms` | Pool acquire wait | `0.1` or `5000` |
| `app.pool.acquired` | Whether pool acquired | `true`, `false` |
| `app.order.wasted_call` | Post-failure call | `true` |
| `app.pricing.unbounded` | No concurrency limit | `true` |
| `app.notification.awaited` | Whether parent waited | `false` |

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              Frontend (Node.js)                  │
└──────┬──────┬──────┬──────┬──────┬──────┬───────┘
       │      │      │      │      │      │
   ┌───▼──┐ ┌─▼───┐ ┌▼────┐ ┌▼───┐ ┌▼──┐ ┌▼─────┐
   │Search │ │Order│ │Cart │ │User│ │Ad │ │Review│
   │Node.js│ │ Go  │ │.NET │ │.NET│ │Jav│ │ Py   │
   └───┬───┘ └──┬──┘ └──┬──┘ └─┬──┘ └─┬─┘ └──┬───┘
       │        │       │      │      │       │
   ┌───▼────────▼───────▼──────▼──────▼───────▼──┐
   │           ProductCatalogService (Go)         │
   │           CurrencyService (C++)              │
   │           PaymentService (Node.js)           │
   │           ShippingService (Rust)             │
   │           EmailService (Ruby)                │
   └──────────────────────────────────────────────┘
       │              │
   ┌───▼──────┐  ┌────▼───────┐
   │Inventory │  │Notification│
   │  Java    │  │  Python    │
   └──────────┘  └────────────┘
       │
   ┌───▼──────┐
   │ Pricing  │
   │   Go     │
   └──────────┘
```

---

## Comparison: How to Tell the Shapes Apart

| | Comb | Staircase | Ghost | Lollipop | Hourglass | Domino | Fan |
|---|---|---|---|---|---|---|---|
| **Fan-out** | N (data) | N (bounded) | N (channels) | Low | Low | N (steps) | N (data) |
| **Homogeneity** | High | High | Moderate | Low | N/A | Low | High |
| **Temporality** | Sequential | Sequential | Concurrent* | Sequential | N/A | Sequential | Concurrent |
| **Inter-span gaps** | ~15μs | Growing (ms) | N/A | N/A | N/A | Fixed | ~0 |
| **Scaling** | Linear | Bounded | Fixed | Fixed | N/A | Fixed | Linear |
| **Key signal** | Uniform teeth | Growing steps | Parent < children | Dominant stem | Bimodal duration | Error inflection | Overlapping start |

*Ghost children are concurrent with each other AND outlive the parent.

---

## Contributing

This extension is designed to be contributed back to the OpenTelemetry community. If you're interested in adding new shapes, services, or detection tools:

1. Each new service should have a real business purpose in the e-commerce domain
2. Anti-patterns must be behind feature flags, default OFF
3. Follow existing language conventions for the chosen runtime
4. Document the geometric signature with the four-dimension framework
5. In-memory data only — no database dependencies

---

## Research Background

The theoretical framework for trace geometry classification is described in:
- [The Geometry of Failure: Language-Agnostic Anti-Pattern Signatures in Distributed Trace Topology](https://npcomplete777.github.io/o11y-alchemy/)
- [Anti-Patterns Have Shapes, and Shapes Don't Care What Language You Write In](https://npcomplete777.github.io/o11y-alchemy/)

The N+1 (Comb) pattern has been empirically validated across Go, Python, and Java at 99.9% Bayesian confidence using the VALIS autonomous detection engine.

---

*Built by Aaron Jacobs · [O11y Alchemy](https://npcomplete777.github.io/o11y-alchemy/) · [GitHub](https://github.com/npcomplete777/opentelemetry-demo)*
