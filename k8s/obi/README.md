# OBI (OpenTelemetry eBPF Instrumentation)

Deploys OBI as a DaemonSet in the `obi` namespace, instrumenting workloads in
the `otel-demo` namespace via eBPF, exporting OTLP directly to the Dynatrace
demo tenant (`nxy41179`). Managed by the `obi` ArgoCD Application
(`k8s/argocd/obi-application.yaml`), which pulls the chart straight from the
official `open-telemetry` Helm repo and this repo's `values.yaml` for config.

## One-time setup: Dynatrace credentials

The Dynatrace OTLP API token is never committed. Create the secret manually
before (or after) the Application syncs:

```sh
kubectl create namespace obi --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic obi-dynatrace-secret -n obi \
  --from-literal=otlp-headers="Authorization=Api-Token <DT_API_TOKEN>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Token needs the same scopes as the app-side collector export:
`openTelemetryTrace.ingest`, `metrics.ingest`.

If the token changes, update the secret and restart the DaemonSet:

```sh
kubectl rollout restart daemonset -n obi -l app.kubernetes.io/name=opentelemetry-ebpf-instrumentation
```
