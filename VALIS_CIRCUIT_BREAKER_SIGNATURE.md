# VALIS Circuit Breaker Pattern Detection

## Overview

This document describes the telemetry patterns VALIS should detect when the circuit breaker implementation trips in the checkout→payment call path.

## Implementation Details

### Circuit Breaker Configuration

- **Location**: Checkout service (Go)
- **Library**: `github.com/sony/gobreaker/v2`
- **Protected Call**: `PaymentService.Charge` gRPC endpoint
- **Settings**:
  - Name: `payment-service`
  - MaxRequests (half-open): `1` (only one probe request)
  - Interval: `30s` (evaluation window for failures)
  - Timeout: `15s` (how long to stay open before half-open)
  - ReadyToTrip: `3 consecutive failures`

### Chaos Injection Flag

- **Flag Name**: `paymentLatencyInjection`
- **Location**: `src/flagd/demo.flagd.json`
- **Variants**:
  - `off`: 0ms (no latency)
  - `500ms`: 500ms delay
  - `2sec`: 2000ms delay
  - `5sec`: 5000ms delay
  - `timeout`: 30000ms delay (will cause gRPC timeout)

### Triggering the Circuit Breaker

1. Set `paymentLatencyInjection` to `5sec` or `timeout`
2. Payment service will sleep before processing (injected in `charge.js`)
3. Checkout's gRPC calls will timeout after ~10-15 seconds
4. After 3 consecutive timeouts, circuit breaker trips
5. All subsequent checkout requests fast-fail without calling payment

## Expected Telemetry Patterns

### Phase 1: Degradation (Payment Slow, Breaker Closed)

**Payment Service Spans:**
- `span.name`: `charge` or `PaymentService/Charge`
- `span.duration`: 5000ms+ (or 30000ms for timeout variant)
- `span.status`: OK (slow but succeeding)
- `span.attributes`:
  - `app.payment.injected_latency_ms`: 5000 (or 30000)

**Checkout Service Spans:**
- `span.name`: `PlaceOrder` or `chargeCard`
- `span.duration`: 5000ms+ (or timeout at gRPC deadline)
- `span.status`: OK (if within deadline) or ERROR (if timeout)
- `span.attributes`:
  - `circuit_breaker.name`: `payment-service`
  - `circuit_breaker.state`: `closed`

### Phase 2: Failures Accumulating (Breaker Counting)

**Payment Service Spans:**
- `span.duration`: Equal to gRPC deadline (~10-15s)
- `span.status`: ERROR
- `span.status_code`: `DEADLINE_EXCEEDED` or `UNAVAILABLE`

**Checkout Service Spans:**
- `span.status`: ERROR
- `span.attributes`:
  - `circuit_breaker.state`: `closed` (still closed, counting failures)
- Error message: "could not charge the card" or "deadline exceeded"
- **Critical**: 1-3 consecutive failed payment calls before trip

### Phase 3: Tripped (Breaker Open, Fast-Failing)

**Payment Service Spans:**
- **ZERO SPANS** - requests never reach payment service
- This is the smoking gun for circuit breaker detection

**Checkout Service Spans:**
- `span.name`: `PlaceOrder`
- `span.duration`: **< 10ms** (fast-fail, no network call)
- `span.status`: ERROR
- `span.attributes`:
  - `circuit_breaker.name`: `payment-service`
  - `circuit_breaker.state`: `open`
  - `circuit_breaker.tripped`: `true`
  - `circuit_breaker.reason`: `consecutive_failures`
- `span.status_message`: "circuit breaker open: payment service unavailable"
- Error message: "payment service unavailable (circuit breaker open)"

**Logs:**
- Level: WARN
- Message: "Circuit breaker tripped for payment service"
- Attributes: `state=open`

### Phase 4: Recovery (Breaker Half-Open, Probing)

After 15 seconds in open state, the circuit breaker transitions to half-open:

**Payment Service Spans:**
- **ONE PROBE SPAN** - single request allowed through
- `span.name`: `charge`
- `span.duration`: Depends on payment service health
- `span.status`: OK (if recovered) or ERROR (if still broken)

**Checkout Service Spans:**
- `span.attributes`:
  - `circuit_breaker.state`: `half-open`

**Recovery Outcomes:**
- **Success Path**: Probe succeeds → state changes to `closed` → normal operation resumes
- **Failure Path**: Probe fails → state changes back to `open` for another 15s

## VALIS Signature Characteristics

### Primary Detection Signals

1. **Sudden Latency DROP** (not increase!)
   - Checkout span duration: 5000ms → < 10ms
   - This is counter-intuitive but diagnostic of circuit breaker pattern

2. **Error Rate Stays High, Error Type Changes**
   - Before: `DEADLINE_EXCEEDED` or `UNAVAILABLE` (gRPC timeout)
   - After: "circuit breaker open: payment service unavailable"

3. **Zero Downstream Spans**
   - No payment service spans during open state
   - Checkout spans have no child spans for payment calls

4. **Span Attribute Smoking Gun**
   - `circuit_breaker.tripped = true`
   - `circuit_breaker.state = "open"`

5. **State Transition Pattern**
   - closed → closed (counting failures) → open (tripped) → half-open (probing) → closed/open

### Secondary Detection Signals

1. **Temporal Pattern**
   - 3 consecutive failures over ~30-45 seconds (3 × 10-15s timeout)
   - Immediate transition to fast-fail (< 10ms)
   - 15 second open duration before probe

2. **Logs Correlation**
   - WARN level: "Circuit breaker tripped for payment service"
   - INFO level: "Circuit breaker payment-service: closed → open"

3. **Metrics (if available)**
   - `circuit_breaker.state` gauge: 0=closed, 1=open, 2=half-open
   - `circuit_breaker.trips_total` counter increments

## Differentiating from Other Patterns

### Circuit Breaker vs. Cascading Failure
- **Circuit Breaker**: Latency DROPS, errors continue at same rate
- **Cascading Failure**: Latency INCREASES, error rate increases

### Circuit Breaker vs. Service Down
- **Circuit Breaker**: Client-side fast-fail, spans present but fast
- **Service Down**: No client spans either (entire service crashed)

### Circuit Breaker vs. Timeout Pattern
- **Circuit Breaker**: Timeout → fast-fail transition
- **Timeout Only**: Consistent slow duration, no state change

## Testing Instructions

### 1. Enable Latency Injection

```bash
# Set the flag via flagd UI or kubectl
kubectl set env deployment/flagd -n otel-demo paymentLatencyInjection=5sec
```

Or via flagd configuration update:
```json
{
  "paymentLatencyInjection": {
    "defaultVariant": "5sec"
  }
}
```

### 2. Trigger Checkout Requests

Generate 3+ checkout requests within 30 seconds to accumulate failures:

```bash
# Use the load generator or manually trigger checkouts
for i in {1..5}; do
  curl -X POST http://frontend:8080/api/checkout \
    -H "Content-Type: application/json" \
    -d '{"userId": "test", "userCurrency": "USD", ...}'
  sleep 2
done
```

### 3. Observe Circuit Breaker Trip

Watch logs:
```bash
kubectl logs -f deployment/checkout -n otel-demo | grep "Circuit breaker"
```

Expected output:
```
Circuit breaker payment-service: closed → open
Circuit breaker tripped for payment service
```

### 4. Query Telemetry

**Spans Query (Dash0/Jaeger/etc.):**
```
service.name = "checkout"
AND circuit_breaker.tripped = true
```

**Metrics Query (Prometheus):**
```
circuit_breaker_state{name="payment-service"}
```

### 5. Verify Recovery

Wait 15 seconds, then trigger another request:
```bash
# Single probe request in half-open state
curl -X POST http://frontend:8080/api/checkout ...
```

Set flag back to `off`:
```bash
kubectl set env deployment/flagd -n otel-demo paymentLatencyInjection=off
```

Probe should succeed → circuit breaker closes → normal operation resumes.

## VALIS Detection Algorithm

Pseudocode for VALIS pattern detection:

```python
def detect_circuit_breaker_pattern(spans, time_window_seconds=60):
    """
    Detect circuit breaker pattern in spans.

    Returns: {
        "pattern": "circuit_breaker_open",
        "confidence": 0.95,
        "evidence": [...],
        "affected_service": "checkout",
        "protected_dependency": "payment"
    }
    """

    checkout_spans = filter(spans, service="checkout", operation="PlaceOrder")
    payment_spans = filter(spans, service="payment", operation="Charge")

    # Signal 1: Latency drop in checkout
    early_p99 = percentile(checkout_spans[:half], 99, "duration")
    late_p99 = percentile(checkout_spans[half:], 99, "duration")
    latency_dropped = (early_p99 > 5000 and late_p99 < 100)

    # Signal 2: Circuit breaker attributes present
    cb_attributes = any(
        span.attributes.get("circuit_breaker.tripped") == True
        for span in checkout_spans
    )

    # Signal 3: Payment spans disappear
    early_payment_count = count(payment_spans[:half])
    late_payment_count = count(payment_spans[half:])
    payment_dropped = (early_payment_count > 0 and late_payment_count == 0)

    # Signal 4: Error type transition
    early_errors = [s.status_message for s in checkout_spans[:half] if s.error]
    late_errors = [s.status_message for s in checkout_spans[half:] if s.error]
    error_type_changed = (
        any("deadline" in e.lower() for e in early_errors) and
        any("circuit breaker" in e.lower() for e in late_errors)
    )

    # Combine signals
    confidence = sum([
        latency_dropped * 0.4,
        cb_attributes * 0.3,
        payment_dropped * 0.2,
        error_type_changed * 0.1
    ])

    if confidence > 0.7:
        return {
            "pattern": "circuit_breaker_open",
            "confidence": confidence,
            "affected_service": "checkout",
            "protected_dependency": "payment",
            "evidence": [
                f"Latency dropped from {early_p99}ms to {late_p99}ms",
                f"Payment spans dropped from {early_payment_count} to {late_payment_count}",
                "Circuit breaker attributes present in spans",
                "Error messages transitioned from timeout to circuit breaker"
            ]
        }

    return None
```

## Related Patterns

- **Bulkhead Pattern**: Similar isolation but uses resource pools instead of state machine
- **Retry Storm**: Can trigger circuit breaker if retries count as consecutive failures
- **Cascading Failure**: Circuit breaker is the SOLUTION to prevent cascading failures

## References

- gobreaker library: https://github.com/sony/gobreaker
- Circuit Breaker Pattern: https://martinfowler.com/bliki/CircuitBreaker.html
- OpenTelemetry Semantic Conventions (proposed): https://github.com/open-telemetry/semantic-conventions/issues/395
