# AV Tools on Kubernetes (OpenStack / Magnum)

Sharded SNMP/ping sweep delivered by **GitOps (ArgoCD + Helm)** to a self-managed
**Magnum** cluster, QA + PROD in parallel. See `../../terraform/` for the cluster.

```
deploy/k8s/
  Dockerfile              # container image (python:3.11-slim, NET_RAW ping, [sentry] extra)
  chart/                  # Helm chart: 3 Indexed CronJobs + ConfigMap + (opt) Fluent Bit + freshness rule
    values.yaml           #   base
    values-qa.yaml        #   QA overlay   (avtools-qa,  QA accounts, :qa image)
    values-prod.yaml      #   PROD overlay (avtools-prod, PROD accounts, :prod image)
  argocd/                 # AppProject + ApplicationSet (qa+prod) + app-of-apps + kube-prometheus-stack
  scripts/sync-secret.sh  # tbag -> K8s Secret bridge (run from an itdcim/avtools host)
  secrets/secret.example.yaml   # manual fallback template (filled copy is gitignored)
```

## Topology

| Service | Shards × threads | Cadence | NET_RAW |
|---|---|---|---|
| `snmp-timeseries` | 8 × 16 = 128 | `*/2` | yes (ICMP ping) |
| `run-eam` | 4 × 16 = 64 | `*/2` | no |
| `run-landb` | 1 × 8 | `*/2` | no |

**Nodes ≠ shards ≠ threads.** The cluster is **1 master + 3 workers** (autoscale
3–6); the 8/4/1 are *shard-pods* the scheduler spreads across those workers; the
16/16/8 are *threads per pod*. `shards == completions == parallelism == SHARD_TOTAL`
and the app refuses to start on an out-of-range shard.

## First deploy (QA)

```bash
# 1. cluster (see ../../terraform) and kubeconfig
eval $(openstack coe cluster config avtools-k8s)

# 2. secret from tbag (run on an itdcim/avtools host, e.g. the monolith)
AVTOOLS_ENVIRONMENT=qa ./deploy/k8s/scripts/sync-secret.sh

# 3a. GitOps path: install ArgoCD (see argocd/README via bootstrap) then:
kubectl apply -n argocd -f deploy/k8s/argocd/app-of-apps.yaml

# 3b. or render locally without ArgoCD:
helm template avtools deploy/k8s/chart -n avtools-qa \
  -f deploy/k8s/chart/values.yaml -f deploy/k8s/chart/values-qa.yaml | kubectl apply -n avtools-qa -f -
```

## Trigger + watch a run

```bash
kubectl -n avtools-qa create job --from=cronjob/avtools-snmp-timeseries manual
kubectl -n avtools-qa get pods -w                 # 8 pods, indices 0..7
kubectl -n avtools-qa logs -l app.kubernetes.io/component=snmp-timeseries --tail=50
```

## Rotate a secret

Update the value in tbag, then re-run the bridge — no manifest change:
```bash
AVTOOLS_ENVIRONMENT=qa ./deploy/k8s/scripts/sync-secret.sh
```

## Observability

- **Metrics** push OTLP → MONIT (tenant `avtools`, label `avtools_environment`) — *not* scraped.
- **Logs** → stdout → Fluent Bit → OpenSearch (`otel-logs_avtools`); **off by default**
  until MonIT provisions the logs tenant (`values.fluentBit.enabled`).
- **Errors** → Sentry (set `sentry.enabled` + `SENTRY_DSN` in the secret).
- **Cluster health** → in-cluster Prometheus (`argocd/kube-prometheus-stack.yaml`).
