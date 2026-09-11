# OBI (OpenTelemetry eBPF Instrumentation)

Deploys OBI as a DaemonSet in the `obi` namespace, instrumenting workloads in
the `otel-demo` namespace via eBPF, exporting OTLP directly to the Coralogix
`ebpf-demo` tenant (eu2 region). Managed by the `obi` ArgoCD Application
(`k8s/argocd/obi-application.yaml`), which pulls the chart straight from the
official `open-telemetry` Helm repo and this repo's `values.yaml` for config.

## One-time setup: Coralogix credentials

The Coralogix Send-Your-Data API key is never committed. Create the secret
manually before (or after) the Application syncs:

```sh
kubectl create namespace obi --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic obi-coralogix-secret -n obi \
  --from-literal=otlp-headers="Authorization=Bearer <CX_API_KEY>" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Key needs `DATA-INGEST-API-KEYS:MANAGE` permission (Settings → Users and
Teams → API Keys in the Coralogix UI).

If the key changes, update the secret and restart the DaemonSet:

```sh
kubectl rollout restart daemonset -n obi -l app.kubernetes.io/name=opentelemetry-ebpf-instrumentation
```
