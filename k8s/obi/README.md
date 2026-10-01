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

The token is never committed. Create the Secret before or after the first sync:

```sh
kubectl create namespace obi --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic obi-dynatrace-secret -n obi \
  --from-literal=otlp-headers="Authorization=Api-Token <DT_API_TOKEN>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Token scopes: `openTelemetryTrace.ingest`, `metrics.ingest`. Set the endpoint in
`values.yaml` (`OTEL_EXPORTER_OTLP_ENDPOINT`, form
`https://<tenant>.live.dynatrace.com/api/v2/otlp`).

After rotating the token, restart OBI:

```sh
kubectl rollout restart daemonset -n obi -l app.kubernetes.io/name=opentelemetry-ebpf-instrumentation
```

## Configuration reference (`values.yaml`)

| Setting | Value here | Why |
|---|---|---|
| `env.OTEL_EXPORTER_OTLP_ENDPOINT` / `_PROTOCOL` | Dynatrace OTLP, `http/protobuf` | Direct export, no collector hop |
| `env.OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | Dynatrace rejects cumulative histograms |
| `env.OTEL_EBPF_KUBE_CLUSTER_NAME` | `orbstack-obi-eval` | Becomes `k8s.cluster.name`; use it to scope queries |
| `env.OTEL_EBPF_METRICS_FEATURES` | `application,application_host,application_runtime,application_service_graph,network,network_flow_packets,stats,ebpf` | Metric groups to emit (default is `application` only) |
| `envValueFrom.OTEL_EXPORTER_OTLP_HEADERS` | Secret `obi-dynatrace-secret` | Credentials |
| `config.data.otel_traces_export` / `otel_metrics_export` | `null` | Use the standard `OTEL_EXPORTER_OTLP_*` env vars instead of OBI's own exporter blocks |
| `config.data.discovery.instrument` | `k8s_namespace: otel-demo` | What to instrument |
| `config.data.discovery.exclude_instrument` | `exe_path: '*chrome*'` | Skip headless-browser churn (see Troubleshooting) |
| `config.data.attributes.kubernetes.enable` | `true` | Add `k8s.*` metadata |
| `config.data.filter.application` | `server.address: not_match 'otel-collector*'` | Don't record apps' own OTLP exports as telemetry |
| `config.data.network.enable` | `true` | L3/L4 flow metrics |
| `resources` | requests `1Gi`/`250m`, limits `6Gi`/`2500m` | See sizing note |

The chart merges `filter.network` defaults (exclude `kube*`, Prometheus, agent
workloads) with the `filter.application` block above.

Render locally before pushing:

```sh
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

Two switches: `config.data.network.enable: true` and the `network`,
`network_flow_packets` and `stats` entries in `OTEL_EBPF_METRICS_FEATURES`. It
covers the whole node, so scope it with `filter.network`. It needs `hostNetwork`
and elevated capabilities, which the chart sets. If the cluster runs Cilium, make
both Cilium and OBI use TCX (or set Cilium `bpf.tc.priority: 2` and OBI's
`OTEL_EBPF_BPF_TC_BACKEND` explicitly).

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
export, OBI traces those exports. See the *OBI is the only telemetry source*
section of the [root README](../../README.md) for the three controls.

## Why OBI exports directly

OBI supports one OTLP destination per signal and has no built-in fan-out. To send
the same data to two backends, put a small collector in front of OBI and export
to that instead.
