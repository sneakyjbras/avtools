# Deploying avtools on CERN OpenShift (PaaS)

Runs the SNMP/ping sweep as a sharded, every-5-minutes **Indexed Job**: N pods,
each polling `crc32(equipment_no) % N == JOB_COMPLETION_INDEX` of the fleet.
`completions` (in `cronjob.yaml`) and `SHARD_TOTAL` (env) must be the same N.

Files:

| File | What it is |
|---|---|
| `Dockerfile` | Two-stage build of the avtools image (context = repo root) |
| `configmap.yaml` | Non-secret config (MONIT endpoint, environment, labels) |
| `cronjob.yaml` | The sharded Indexed-Job CronJob |

## One-time: point `oc` at your project

Get the login command from the PaaS web console (top-right → your name →
**Copy login command**), then:

```bash
oc login --token=<token> --server=https://api.paas.cern.ch:6443
oc project avtools
```

## 1. Build the image in-cluster (no local Docker needed)

The Dockerfile lives in `deploy/openshift/` but the build **context is the repo
root**, so point the build config at it:

```bash
oc new-build --name=avtools --binary --strategy=docker
oc patch bc/avtools --type=merge \
  -p '{"spec":{"strategy":{"dockerStrategy":{"dockerfilePath":"deploy/openshift/Dockerfile"}}}}'
oc start-build avtools --from-dir=. --follow
```

This produces the imagestream `avtools:latest`, which `cronjob.yaml` references.
(The build pod must reach `pypi-itdcim.cern.ch` for the CERN deps — it can from
inside the CERN network.)

## 2. Config + secret

```bash
oc apply -f deploy/openshift/configmap.yaml

oc create secret generic avtools-secrets \
  --from-literal=DATABASE_URL='postgresql://<user>:<pass>@<host>:5432/<db>' \
  --from-literal=MONIT_PASSWORD='<monit-tenant-password>'
```

(Same values Puppet/TEIGI injected today: the DBoD URL and the MONIT tenant
password. Start against the **QA** database — `configmap.yaml` sets
`AVTOOLS_ENVIRONMENT: qa`.)

## 3. Deploy the CronJob

```bash
oc apply -f deploy/openshift/cronjob.yaml
```

## 4. Trigger one run now and watch

Don't wait 5 minutes — fire a manual Job from the CronJob:

```bash
oc create job --from=cronjob/avtools-snmp avtools-snmp-manual
oc get pods -w                       # you should see 8 pods, indices 0..7
oc logs -l app=avtools --tail=50     # look for "snmp_shard_applied" + "cycle_summary"
```

Each pod's log should show its own `shard_index` and a `devices_in_shard` roughly
equal to `fleet / 8`. In Grafana, `avtools_snmp_devices_targeted{shard="k"}` will
now have one series per shard, and the new `avtools-k8s-slo` alerts light up.

## Scaling N

To change the number of shards, edit **both** in `cronjob.yaml` and re-apply:
`completions` (and `parallelism`) **and** the `SHARD_TOTAL` env. They must match.
Compute N from `ceil(device_count / target_devices_per_shard)`.

## Known gotcha: ICMP ping needs CAP_NET_RAW

Under the default restricted SCC (all capabilities dropped) **SNMP works but the
ICMP ping probes may fail** — `ping` needs `CAP_NET_RAW`. Options:

- Confirm whether the node allows unprivileged ICMP (`net.ipv4.ping_group_range`)
  — if so, ping works as-is.
- Otherwise request an SCC that grants `NET_RAW` (needs PaaS admin approval), then
  add it under the container's `securityContext.capabilities.add: ["NET_RAW"]`.

Check the ping-status metrics after the first run to see if this bites you.
