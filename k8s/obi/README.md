# Operating OBI (OpenTelemetry eBPF Instrumentation)

OBI runs as a DaemonSet in the `obi` namespace and instruments workloads in the
`otel-demo` namespace via eBPF, exporting OTLP **directly** to Dynatrace.
It is deployed by the `obi` Argo CD Application
([`k8s/argocd/obi-application.yaml`](../argocd/obi-application.yaml)): the official
`opentelemetry-ebpf-instrumentation` Helm chart (**0.13.0**, OBI **v0.12.2**) with
[`values.yaml`](values.yaml) from this repo. Argo CD has `automated` sync and
`selfHeal`, so a push to `main` is applied automatically.

For *what OBI is and how it instruments*, see
[`docs/obi/how-obi-instruments.md`](../../docs/obi/how-obi-instruments.md).

## One-time setup: Dynatrace credentials

(For any other OTLP backend, see
[Sending OBI data to any OTLP backend](#sending-obi-data-to-any-otlp-backend).)

Neither the token nor the tenant URL is committed. Create the Secret **before**
the first sync (the pod won't start without both keys):

```sh
kubectl create namespace obi --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic obi-dynatrace-secret -n obi \
  --from-literal=otlp-endpoint="https://<tenant>.live.dynatrace.com/api/v2/otlp" \
  --from-literal=otlp-headers="Authorization=Api-Token <DT_API_TOKEN>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Token scopes: `openTelemetryTrace.ingest`, `metrics.ingest`.

After rotating the token, restart OBI:

```sh
kubectl rollout restart daemonset -n obi -l app.kubernetes.io/name=opentelemetry-ebpf-instrumentation
```

## Configuration reference (`values.yaml`)

| Setting | Value here | Why |
|---|---|---|
| `envValueFrom.OTEL_EXPORTER_OTLP_ENDPOINT`, `env.OTEL_EXPORTER_OTLP_PROTOCOL` | Secret key `otlp-endpoint`, `http/protobuf` | Direct export, no collector hop |
| `env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | Dynatrace rejects cumulative histograms |
| `env.OTEL_EBPF_KUBE_CLUSTER_NAME` | `orbstack-obi-eval` | Becomes `k8s.cluster.name`; use it to scope queries |
| `env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION` | `all` | Cross-service trace linking; v0.12.2 default is `disabled` (the chart's `contextPropagation.enabled` only grants privileges). Use `headers` if TCP-option injection causes trouble |
| `env.OTEL_EBPF_METRICS_FEATURES` | `application,application_host,application_runtime,application_service_graph,network,network_flow_packets,stats` | Metric groups to emit (default is `application` only). `ebpf` is omitted: it is Prometheus-only |
| `envValueFrom.OTEL_EXPORTER_OTLP_HEADERS` | Secret `obi-dynatrace-secret` | Credentials |
| `config.data.otel_traces_export` / `otel_metrics_export` | `null` | Use the standard `OTEL_EXPORTER_OTLP_*` env vars instead of OBI's own exporter blocks |
| `config.data.discovery.instrument` | `k8s_namespace: otel-demo` | What to instrument |
| `config.data.prometheus_export` | `null` | Turn off the chart's default `/metrics` on :9090 (exposed on the node via hostNetwork) |
| `config.data.discovery.exclude_instrument` | `exe_path: '*chrome*'`; `prometheus`/`grafana`/`jaeger` Deployments, `opensearch` StatefulSet | Skip headless-browser churn (see Troubleshooting) and the idle observability backends |
| `config.data.attributes.kubernetes.enable` | `true` | Add `k8s.*` metadata |
| `config.data.attributes.select.obi_network_flow_*` | IPs, `dst.port`, transport, pod names, CIDR names | L4 detail on flows (off by default). Not `src.port`: ephemeral ports blew past Dynatrace's 4 MiB OTLP request limit (HTTP 413) |
| `config.data.filter.application` / `.network` / `.stats` | drop `otel-collector*` (spans also by `rpc.method` `/opentelemetry.proto.collector.*`) | Don't record apps' own (refused) OTLP exports as spans, flows or TCP stats; the three families are independent |
| `config.data.network.enable`, `network.cidrs`, `stats.cidrs` | `true`; pods / services / node / external | `enable` is a deprecated alias; the CIDR list names address ranges |
| `resources` | requests `1Gi`/`250m`, limits `6Gi`/`2500m` | See sizing note |

The chart's `filter.network` defaults (owner names `kube*`, `*prometheus*`,
agents) are merged per key with the values here, so the network keys in
`values.yaml` restate the default pattern plus `otel-collector*`. They match
owner names, not namespaces.

Render locally before pushing:

```sh
helm repo add open-telemetry https://open-telemetry.github.io/opentelemetry-helm-charts
helm template obi open-telemetry/opentelemetry-ebpf-instrumentation \
  --version 0.13.0 -n obi -f k8s/obi/values.yaml | less
```

Inspect what OBI actually loaded:

```sh
kubectl get cm -n obi obi-opentelemetry-ebpf-instrumentation \
  -o jsonpath='{.data.ebpf-instrument-config\.yml}'
kubectl get ds -n obi obi-opentelemetry-ebpf-instrumentation \
  -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'
```

## Verify it is working

```sh
kubectl get pods -n obi                                   # Running, restarts 0
kubectl logs -n obi ds/obi-opentelemetry-ebpf-instrumentation | grep "instrumenting process"
```

In Dynatrace (DQL):

```dql
fetch spans, from: now() - 10m
| filter telemetry.distro.name == "opentelemetry-ebpf-instrumentation"
    and k8s.cluster.name == "orbstack-obi-eval"
| summarize spans = count(), services = countDistinct(service.name)
```

Expect `obi.network.flow.bytes`, `obi.stat.tcp.*`, `traces_service_graph_*`,
`jvm.memory.*` and `nodejs.eventloop.*` among the metrics (scope
`go.opentelemetry.io/obi`, `network_ebpf_events`, `stats_ebpf_events`).

## Network (L3/L4) data

The switch is the `network`, `network_flow_packets` and `stats` entries in
`OTEL_EBPF_METRICS_FEATURES` (`network.enable` is a deprecated alias). Capture
uses a socket filter by default (`network.source: tc` for TC). Flows default to
k8s owner/namespace + direction only; IPs, ports and transport come from
`attributes.select`, and `network.cidrs` names address ranges. It covers the
whole node, so scope it with `filter.network`. It needs `hostNetwork` and
elevated capabilities, which the chart sets. If the cluster runs Cilium and OBI
attaches TC programs (TC source, or context propagation as here), make both use
TCX (or set Cilium `bpf.tc.priority: 2` and OBI's `OTEL_EBPF_BPF_TC_BACKEND`:
`tc`, `tcx` or `auto`).

## Sending OBI data to any OTLP backend

OBI is a standard OTLP exporter, so Dynatrace is just one destination. Switching
platforms is a configuration change: no rebuild, and no change to the apps.
OBI v0.12.2 exports **traces and metrics** (no logs) and reads the standard
OpenTelemetry exporter variables
([`pkg/export/otel/otelcfg/`](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.12.2/pkg/export/otel/otelcfg)):

| Variable | Purpose | Notes |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Base URL for both signals | OBI appends `/v1/traces` and `/v1/metrics` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `_METRICS_ENDPOINT` | Per-signal URL | Used **as is**: include the full path. Overrides the base URL for that signal |
| `OTEL_EXPORTER_OTLP_HEADERS` | Auth/headers for both signals | `key=value,key2=value2` |
| `OTEL_EXPORTER_OTLP_TRACES_HEADERS` / `_METRICS_HEADERS` | Per-signal headers | e.g. a metrics-only dataset header |
| `OTEL_EXPORTER_OTLP_PROTOCOL` (and `_TRACES_` / `_METRICS_` variants) | `http/protobuf`, `grpc` or `http/json` | gRPC endpoints are usually port 4317, HTTP 4318 or 443 |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` or `cumulative` | Pick what the backend wants (table below) |
| `OTEL_EBPF_INSECURE_SKIP_VERIFY` | Skip TLS certificate checks | Only for self-signed endpoints. `http://` URLs are sent in plaintext automatically |

In this repo the endpoint and headers come from the `obi-dynatrace-secret`
Secret (keys `otlp-endpoint` and `otlp-headers`). Everything else is in
[`values.yaml`](values.yaml). The Secret's name is historical: it can hold any
backend's values.

### Steps

1. **Collect three facts from the target platform's OTLP docs:** the endpoint
   URL, the auth header, and the protocol and temporality it expects. See the
   table below for common values.
2. **Update the Secret** (endpoint and credentials never go in git):

   ```sh
   kubectl create secret generic obi-dynatrace-secret -n obi \
     --from-literal=otlp-endpoint="https://otlp.example.com" \
     --from-literal=otlp-headers="Authorization=Bearer <token>" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

   Separate multiple headers with commas: `api-key=abc,x-tenant=demo`.
   Percent-encode any comma or `=` *inside* a value. A space, as in
   `Api-Token <token>`, works as is.
3. **Adjust `values.yaml` if the protocol or temporality differs**, then commit
   and push. Argo CD rolls OBI automatically:

   ```yaml
   env:
     OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"            # or "grpc"
     OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "cumulative"   # Dynatrace needs "delta"
   ```

4. **Restart OBI if only the Secret changed.** A Secret update alone does not
   roll the DaemonSet:

   ```sh
   kubectl rollout restart daemonset -n obi -l app.kubernetes.io/name=opentelemetry-ebpf-instrumentation
   ```

5. **Verify.** OBI logs export problems at `INFO` with the text `failed to upload`:

   ```sh
   kubectl logs -n obi ds/obi-opentelemetry-ebpf-instrumentation | grep -E "failed to upload|401|403|413"
   ```

   - `401` / `403`: wrong header or token scope.
   - `404`: wrong path. Remember that per-signal endpoints need the full path.
   - `413`: payload too large; reduce flow attribute cardinality (see the
     `src.port` note in `values.yaml`).
   - `OTLP partial success … dimension dropped`: warnings only.

   Then look for spans with
   `telemetry.distro.name = opentelemetry-ebpf-instrumentation` and metrics
   named `obi.network.flow.bytes`, `http.server.request.duration` and so on in
   the new backend.

### Common endpoint values

Typical values from each vendor's OTLP documentation. They are **not exercised
in this deployment** (only Dynatrace is), and regions, ports and header names
change, so confirm against the vendor docs before use.

| Platform | `otlp-endpoint` (base URL) | `otlp-headers` | Protocol | Temporality |
|---|---|---|---|---|
| Dynatrace *(used here)* | `https://<env-id>.live.dynatrace.com/api/v2/otlp` | `Authorization=Api-Token <token>` (scopes `openTelemetryTrace.ingest`, `metrics.ingest`) | `http/protobuf` | `delta` (required) |
| Grafana Cloud | `https://otlp-gateway-<zone>.grafana.net/otlp` | `Authorization=Basic <base64(instanceID:token)>` | `http/protobuf` | `cumulative` |
| Honeycomb | `https://api.honeycomb.io` | `x-honeycomb-team=<api-key>`; metrics also need `x-honeycomb-dataset=<name>` via `OTEL_EXPORTER_OTLP_METRICS_HEADERS` | `http/protobuf` or `grpc` | `delta` or `cumulative` |
| New Relic | `https://otlp.nr-data.net` (EU: `https://otlp.eu01.nr-data.net`) | `api-key=<license-key>` | `http/protobuf` | `delta` (recommended) |
| Elastic Observability | your deployment's OTLP / APM endpoint | `Authorization=ApiKey <key>` | `http/protobuf` | `cumulative` |
| Dash0 | `https://ingress.<region>.aws.dash0.com` | `Authorization=Bearer <token>` | `http/protobuf` or `grpc` | `cumulative` |
| Any OpenTelemetry Collector | `http://<collector-svc>.<ns>:4318` (or `:4317` with `grpc`) | usually none | either | whatever its exporters need |

Backends without native OTLP intake, or ones needing different credentials per
signal, are best reached through an OpenTelemetry Collector (next section).

### Example: traces to Jaeger, metrics to Prometheus (per-signal endpoints)

The demo already runs both, and both accept OTLP natively: Jaeger for traces,
Prometheus at `/api/v1/otlp`, which the app collector already uses. Per-signal
endpoints take the **full** path. Remove `OTEL_EXPORTER_OTLP_ENDPOINT` and the
headers from `envValueFrom` (neither backend needs auth), then set:

```yaml
env:
  OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: "http://jaeger.otel-demo:4318/v1/traces"
  OTEL_EXPORTER_OTLP_METRICS_ENDPOINT: "http://prometheus.otel-demo:9090/api/v1/otlp/v1/metrics"
  OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE: "cumulative"   # Prometheus wants cumulative
```

`http://` means plaintext, which is fine inside the cluster. Jaeger, Prometheus
and Grafana are excluded from OBI's own instrumentation in `values.yaml`, so
this export traffic won't be traced back into itself.

### Several backends at once

OBI has **one destination per signal** and no fan-out. To send the same data to
Dynatrace *and* another platform, run an OpenTelemetry Collector, point OBI at
it (`otlp-endpoint=http://<collector-svc>.<ns>:4318`, no headers), and configure
one exporter per backend in the collector. A dedicated gateway works well; the
demo's existing collector no longer has an OTLP receiver. The collector is also
the place for temporality conversion (`cumulativetodelta`), filtering,
sampling and attribute renaming. If that collector runs in `otel-demo`, its
traffic is already excluded by the `otel-collector*` filters; elsewhere, add a
matching rule.

## Troubleshooting

**OBI restarts repeatedly / `OOMKilled`.** Check `kubectl get pod -n obi -o
jsonpath='{.items[0].status.containerStatuses[0].lastState}'`. Memory is driven by
*process churn* (every new or exiting process is discovered and attached), not just
traffic. In this demo the load generator's headless Chromium created hundreds of
short-lived processes and OBI hit its 6 GiB limit about every 5 minutes; the
`exe_path: '*chrome*'` exclude fixed it (steady ~300 MiB, 0 restarts). Do not just
raise the limit — find the churn first. Limits that are *below* the real working
set cause an immediate OOM loop; 2 GiB and 4 GiB both proved too tight during
mass pod restarts.

**No data in Dynatrace.** Check the Secret exists and the token has
`openTelemetryTrace.ingest` and `metrics.ingest`; then look for HTTP errors:
`kubectl logs -n obi ds/obi-opentelemetry-ebpf-instrumentation | grep -i -E "error|401|403"`.
"OTLP partial success … dimension dropped" messages are warnings about empty
attribute values, not failures.

**Metrics rejected.** Dynatrace needs delta temporality
(`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`).

**A feature group is rejected after editing `OTEL_EBPF_METRICS_FEATURES`.** Check the
pod logs on start; revert the env var to roll back (Argo CD re-syncs).

**Startup warnings you can ignore:** "timed out while waiting for Cloud metadata"
(no cloud on a laptop cluster) and "creating OTEL namespace in bpffs failed" (OBI
continues; only pinned-map features such as the log enricher are disabled).

**Telemetry from the apps themselves appears as OBI data.** If app SDKs still
export, OBI records those attempts as spans, flows and TCP failed-connection
stats. See the *OBI is the only telemetry source* section of the
[root README](../../README.md) for the controls.

**Requests fail or hang after enabling context propagation.** Set
`OTEL_EBPF_BPF_CONTEXT_PROPAGATION` to `headers` (drops TCP-option injection) or
`disabled`, and check the OBI logs for TC attach errors.

## Why OBI exports directly

Fewer moving parts for a demo: no collector hop to size or secure. The trade-off
is that OBI supports one OTLP destination per signal with no built-in fan-out (its
Prometheus exporter is turned off here); see
[Several backends at once](#several-backends-at-once).
