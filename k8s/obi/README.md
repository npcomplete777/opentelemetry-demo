# Operating OBI (OpenTelemetry eBPF Instrumentation)

OBI runs as a DaemonSet in the `obi` namespace and instruments workloads in the
`otel-demo` namespace via eBPF. It exports OTLP to `otel-gateway`, a small
OpenTelemetry Collector in the same namespace ([`gateway/`](gateway)), which
forwards everything to **Dynatrace and Coralogix**. OBI supports only one
destination per signal, so fan-out needs the collector.
Both are deployed by the `obi` Argo CD Application
([`k8s/argocd/obi-application.yaml`](../argocd/obi-application.yaml)): the official
`opentelemetry-ebpf-instrumentation` Helm chart (**0.13.0**, OBI **v0.12.2**) with
[`values.yaml`](values.yaml) from this repo, plus the `gateway/` kustomization. Argo CD has `automated` sync and
`selfHeal`, so a push to `main` is applied automatically.

For *what OBI is and how it instruments*, see
[`docs/obi/how-obi-instruments.md`](../../docs/obi/how-obi-instruments.md).

## One-time setup: backend credentials

(For any other OTLP backend, see
[Sending OBI data to any OTLP backend](#sending-obi-data-to-any-otlp-backend).)

No tokens or tenant URLs are committed. The gateway reads all four values from
one Secret. Create it **before** the first sync; the gateway pod won't start
without it:

```sh
kubectl create namespace obi --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic otel-gateway-secret -n obi \
  --from-literal=DT_ENDPOINT="https://<tenant>.live.dynatrace.com/api/v2/otlp" \
  --from-literal=DT_AUTH="Api-Token <DT_API_TOKEN>" \
  --from-literal=CX_ENDPOINT="https://ingress.<coralogix-domain>:443" \
  --from-literal=CX_AUTH="Bearer <Send-Your-Data API key>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

- **Dynatrace token scopes:** `openTelemetryTrace.ingest`, `metrics.ingest`.
- **Coralogix:** use a Send-Your-Data key (`cxtp_…`). The domain is the region's,
  e.g. `cx498.coralogix.com` for US2 or `eu2.coralogix.com` for EU2. The
  generic `otlphttp` exporter is enough, since Coralogix ingests standard OTLP;
  the contrib `coralogix` exporter isn't needed. The gateway sets
  `cx.application.name=otel-demo` and `cx.subsystem.name=<service.name>` on the
  Coralogix pipelines only.

After rotating a token, restart the gateway (OBI needs no restart):

```sh
kubectl rollout restart deployment -n obi otel-gateway
```

To drop a backend, remove its two pipelines from
[`gateway/config.yaml`](gateway/config.yaml).

## Configuration reference (`values.yaml`)

| Setting | Value here | Why |
|---|---|---|
| `env.OTEL_EXPORTER_OTLP_ENDPOINT`, `env.OTEL_EXPORTER_OTLP_PROTOCOL` | `http://otel-gateway.obi.svc.cluster.local:4318`, `http/protobuf` | In-cluster gateway, no auth; resolves because OBI uses `dnsPolicy: ClusterFirstWithHostNet` |
| `env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | Dynatrace rejects cumulative histograms; the gateway passes delta through to both backends |
| `env.OTEL_EBPF_KUBE_CLUSTER_NAME` | `orbstack-obi-eval` | Becomes `k8s.cluster.name`; use it to scope queries |
| `env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION` | `all` | Cross-service trace linking; v0.12.2 default is `disabled` (the chart's `contextPropagation.enabled` only grants privileges). Use `headers` if TCP-option injection causes trouble |
| `env.OTEL_EBPF_METRICS_FEATURES` | `application,application_host,application_runtime,application_service_graph,network,network_flow_packets,stats` | Metric groups to emit (default is `application` only). `ebpf` is omitted: it is Prometheus-only |
| `config.data.otel_traces_export` / `otel_metrics_export` | `null` | Use the standard `OTEL_EXPORTER_OTLP_*` env vars instead of OBI's own exporter blocks |
| `config.data.discovery.instrument` | `k8s_namespace: otel-demo` | What to instrument |
| `config.data.prometheus_export` | `null` | Turn off the chart's default `/metrics` on :9090 (exposed on the node via hostNetwork) |
| `config.data.discovery.exclude_instrument` | `exe_path: '*chrome*'`; `prometheus`/`grafana`/`jaeger` Deployments, `opensearch` StatefulSet | Skip headless-browser churn (see Troubleshooting) and the idle observability backends |
| `config.data.attributes.kubernetes.enable` | `true` | Add `k8s.*` metadata |
| `config.data.attributes.select.obi_network_flow_*` | IPs, `server.port`, transport, pod names, CIDR names | L4 detail on flows (off by default). Not `src.port`/`dst.port`: flows are directional, so each carries ephemeral client ports on one side. `src.port` blew past Dynatrace's 4 MiB OTLP request limit (HTTP 413); `dst.port` produced 52k distinct values in 5 minutes. `server.port` is `0` on `direction=unknown` flows (handshake not seen; `network.guess_ports` defaults to `disable`) |
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

OBI is a standard OTLP exporter, so the backends are configuration. In this repo
the simplest change is to the gateway: add an exporter and a pipeline pair to
[`gateway/config.yaml`](gateway/config.yaml) and its credentials to
`otel-gateway-secret`. The rest of this section is about pointing OBI itself at
a single backend without the gateway. Switching
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

Without the gateway, put the endpoint and headers in a Secret (below,
`obi-dynatrace-secret` with keys `otlp-endpoint` and `otlp-headers`) and
reference it from `values.yaml`:

```yaml
envValueFrom:
  OTEL_EXPORTER_OTLP_ENDPOINT:
    secretKeyRef: {name: obi-dynatrace-secret, key: otlp-endpoint}
  OTEL_EXPORTER_OTLP_HEADERS:
    secretKeyRef: {name: obi-dynatrace-secret, key: otlp-headers}
```

and remove `env.OTEL_EXPORTER_OTLP_ENDPOINT`.

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
in this deployment** (only Dynatrace and Coralogix are), and regions, ports and header names
change, so confirm against the vendor docs before use.

| Platform | `otlp-endpoint` (base URL) | `otlp-headers` | Protocol | Temporality |
|---|---|---|---|---|
| Dynatrace *(used here)* | `https://<env-id>.live.dynatrace.com/api/v2/otlp` | `Authorization=Api-Token <token>` (scopes `openTelemetryTrace.ingest`, `metrics.ingest`) | `http/protobuf` | `delta` (required) |
| Coralogix *(used here)* | `https://ingress.<domain>:443` (e.g. `cx498.coralogix.com`) | `Authorization=Bearer <Send-Your-Data key>` | `http/protobuf` or `grpc` | `delta` accepted here without rejections |
| Grafana Cloud | `https://otlp-gateway-<zone>.grafana.net/otlp` | `Authorization=Basic <base64(instanceID:token)>` | `http/protobuf` | `cumulative` |
| Honeycomb | `https://api.honeycomb.io` | `x-honeycomb-team=<api-key>`; metrics also need `x-honeycomb-dataset=<name>` via `OTEL_EXPORTER_OTLP_METRICS_HEADERS` | `http/protobuf` or `grpc` | `delta` or `cumulative` |
| New Relic | `https://otlp.nr-data.net` (EU: `https://otlp.eu01.nr-data.net`) | `api-key=<license-key>` | `http/protobuf` | `delta` (recommended) |
| Elastic Observability | your deployment's OTLP / APM endpoint | `Authorization=ApiKey <key>` | `http/protobuf` | `cumulative` |
| Any OpenTelemetry Collector | `http://<collector-svc>.<ns>:4318` (or `:4317` with `grpc`) | usually none | either | whatever its exporters need |

Backends without native OTLP intake, or ones needing different credentials per
signal, are best reached through an OpenTelemetry Collector (next section).

### Example: traces to Jaeger, metrics to Prometheus (per-signal endpoints)

The demo already runs both, and both accept OTLP natively: Jaeger for traces,
Prometheus at `/api/v1/otlp`; both are scaled to 0 in OBI-only mode, so scale them up first. Per-signal
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

OBI has **one destination per signal** and no fan-out, which is why this repo
runs [`gateway/`](gateway): OBI sends to it with no headers, and it has one
`otlphttp` exporter and one traces/metrics pipeline pair per backend. Each
exporter has its own retry queue, so one backend failing doesn't hold up the
other. The gateway deliberately has no `batch` processor: OBI already batches,
and merged requests could exceed Dynatrace's 4 MiB limit. The collector is also
the place for temporality conversion (`cumulativetodelta`), filtering, sampling
and attribute renaming. It runs in `obi`, which OBI doesn't instrument
(`discovery.instrument` is `otel-demo` only); a collector placed in `otel-demo`
would need excluding, as the `otel-collector*` filters do.

## Troubleshooting

**OBI restarts repeatedly / `OOMKilled`.** Check `kubectl get pod -n obi -o
jsonpath='{.items[0].status.containerStatuses[0].lastState}'`. Memory is driven by
*process churn* (every new or exiting process is discovered and attached), not just
traffic. In this demo the load generator's headless Chromium created hundreds of
short-lived processes and OBI hit its 6 GiB limit about every 5 minutes; the
`exe_path: '*chrome*'` exclude fixed it (steady ~300 MiB, 0 restarts; 445 MiB on
2026-10-16 with the showcase services added). The `obi-traffic` curl loop forks
short-lived processes too, but OBI caches the executable after the first attach
(`process ended for already instrumented executable`), so it adds little. Do not just
raise the limit — find the churn first. Limits that are *below* the real working
set cause an immediate OOM loop; 2 GiB and 4 GiB both proved too tight during
mass pod restarts.

**No data in a backend.** Backend errors are logged by the gateway, not OBI:
`kubectl logs -n obi deploy/otel-gateway | grep -i -E "error|401|403|413"`.
The exporter name (`otlphttp/dynatrace` or `otlphttp/coralogix`) shows which
one. Check `otel-gateway-secret` and the token scopes (Dynatrace:
`openTelemetryTrace.ingest`, `metrics.ingest`). If *both* backends are empty,
check OBI's logs for `failed to upload` (gateway unreachable). Dynatrace's
"OTLP partial success … dimension dropped" messages are warnings about empty
attribute values, not failures.

**Metrics rejected.** Dynatrace needs delta temporality
(`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta`).

**A feature group is rejected after editing `OTEL_EBPF_METRICS_FEATURES`.** Check the
pod logs on start; revert the env var to roll back (Argo CD re-syncs).

**Startup warnings you can ignore:** "timed out while waiting for Cloud metadata"
(no cloud on a laptop cluster) and "creating OTEL namespace in bpffs failed" (OBI
continues; only pinned-map features such as the log enricher are disabled).

**Telemetry from the apps themselves appears as OBI data.** (Not an issue here,
since the SDKs are removed; relevant in mixed estates.) If app SDKs still
export, OBI records those attempts as spans, flows and TCP failed-connection
stats. See the *OBI is the only telemetry source* section of the
[root README](../../README.md) for the controls.

**A service has no spans after an OBI restart.** [observed 2026-10-16] After
OrbStack restarted, OBI attached to every running runtime except `email`'s Ruby
(Puma) process: no `instrumenting process ... cmd=/usr/local/bin/ruby` line, and
no `email` spans for 40 minutes while it kept serving requests. Deleting the pod
fixed it (attached within seconds). After an OBI restart, check
`kubectl logs -n obi ds/obi-opentelemetry-ebpf-instrumentation | grep "instrumenting process"`
against the expected runtimes; in the notebook, an empty per-language tile is
the symptom.

**Log enricher shows `logenricher=true` but logs have no trace_id.**
[observed 2026-10-02] Enabled with the host bpffs mounted (the "bpffs failed"
warning is gone) for fx-full, fx-slim and checkout. OBI reports
`logenricher=true` for those processes, but no `trace_id` appeared in their
container logs in the first 10 minutes. Not yet diagnosed: run OBI with
`OTEL_EBPF_LOG_LEVEL=debug` and see `devdocs/trace-log-correlation.md` (pipe
registration, stdout buffering) in the OBI repo.

**Requests fail or hang after enabling context propagation.** Set
`OTEL_EBPF_BPF_CONTEXT_PROPAGATION` to `headers` (drops TCP-option injection) or
`disabled`, and check the OBI logs for TC attach errors.

## Why there is a gateway

OBI exported directly to Dynatrace until 2026-10-16, which meant fewer moving
parts. Adding Coralogix needed fan-out, which OBI doesn't have (one OTLP
destination per signal; its Prometheus exporter is turned off here), so the
gateway was added. See [Several backends at once](#several-backends-at-once).
