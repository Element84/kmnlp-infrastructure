# EKS Upgrade & Cost Remediation — Deployment Log

Ongoing log of infrastructure changes made to reduce operating cost and exit EKS
extended support. Newest entries at the bottom of each section.

- **Account:** 515966525558 (`kmnlp` profile, us-east-1)
- **Operator identity:** `AWSReservedSSO_AWSDeveloperAccess` / ahassan@element84.com
- **Started:** 2026-09-08

---

## Motivation

August 2026 usage-only spend was **$2,704**, of which ~$2,394 is KMNLP-attributable.
Cost Explorer identified these addressable items:

| Finding                                                                       | $/mo | Action                                  |
| ----------------------------------------------------------------------------- | ---: | --------------------------------------- |
| EKS extended support (`USE1-AmazonEKS-Hours:extendedSupport`, 744 hr x $0.50) |  372 | Upgrade cluster off 1.33                |
| EFS Elastic-throughput I/O for ClickHouse (2,478 GB metered on 18 GB stored)  |  130 | Move off EFS (not yet actioned)         |
| ECR storage (838 GB, no lifecycle policy)                                     |   84 | Add lifecycle policy (not yet actioned) |
| NAT data processing (552 GB)                                                  |   25 | S3 gateway endpoint (not yet actioned)  |
| Idle public IPv4 addresses                                                    |    7 | Release (not yet actioned)              |

Out of scope / other owners: `nccf-dev-archive-neptune` (~$262/mo, different project),
Security Hub + GuardDuty + Config (~$48/mo, org-level).

---

## Code changes staged (not yet applied)

All in `kmnlp_infra/eks_cluster.py` unless noted.

1. `_KUBERNETES_VERSION = "1.34"` — new constant, applied to both `EksCluster` and
   `EksNodeGroup`. The version was previously unmanaged by Terraform, so AWS pinned
   whatever was current at cluster creation (1.33, Aug 2025). Target is 1.36, reached
   via three sequential applies since EKS upgrades one minor version at a time.
2. `dask-kubernetes-operator` chart `2025.7.0` -> `2026.3.0`. Latest release
   (2026-03-02), declares `kubeVersion: '>=1.30.0-0'`, so 1.36-compatible.
3. `aws-load-balancer-controller` chart `1.13.4` -> `3.5.0`. Not two major versions:
   from v3.0.0 the chart version tracks the controller version (v2.x used chart 1.x),
   so this is controller v2.13 -> v3.5. Requires Kubernetes 1.22+.
4. New `null_resource` applying LBC CRDs via `kubectl apply -k`, gated on chart version
   and set as a dependency of the Helm release. Helm never upgrades CRDs on an existing
   release, and v3.0.0 lists a CRD update as required.
5. Three `EksAddon` resources adopting `vpc-cni`, `kube-proxy`, `coredns`.
   `resolve_conflicts_on_create=OVERWRITE` is required to adopt the existing
   self-managed installs. `addon_version` deliberately omitted so EKS selects the
   default build for the active cluster version on each upgrade hop.
6. `kmnlp_infra/dask_cluster.py`: `replicas` seed changed from hard-coded `8` to
   `config.dask_cluster.min_workers`.

Validation: `ruff check` and `ruff format --check` pass. `scripts/lint.sh` aborts
during shellcheck on pre-existing `.history/` warnings, unrelated to these edits.

---

## Baseline — 2026-09-08, before any apply

Cluster `dask-cluster`, version **1.33**, platform `eks.45`, created 2025-08-22.

```
NAME                                   STATUS   VERSION                OS-IMAGE
fargate-ip-10-81-76-147.ec2.internal   Ready    v1.33.13-eks-254016e   Minimal
fargate-ip-10-81-79-230.ec2.internal   Ready    v1.33.13-eks-254016e   Minimal
fargate-ip-10-81-80-144.ec2.internal   Ready    v1.33.13-eks-254016e   Minimal
ip-10-81-35-132.ec2.internal           Ready    v1.33.1-eks-f5be8fb    Bottlerocket OS 1.45.0 (aws-k8s-1.33)
ip-10-81-81-170.ec2.internal           Ready    v1.33.1-eks-f5be8fb    Bottlerocket OS 1.45.0 (aws-k8s-1.33)
```

- 3 Fargate nodes = 1 Dask scheduler + 2 Dask workers.
- 2 Bottlerocket EC2 nodes, **382 days old** — node group AMI has never been rolled.
- `aws eks list-addons` returns empty: CoreDNS / kube-proxy / VPC CNI are all
  self-managed default installs, frozen since Aug 2025.

Dask state — note the divergence between the seed and the live worker count:

```
daskcluster/kmnlp-compute                WORKERS 8   Running   28d
daskworkergroup/kmnlp-compute-default    WORKERS 2             28d
daskautoscaler/kmnlp-compute             MIN 2  MAX 20         28d
```

The `DaskCluster` spec seed says 8; the autoscaler has driven the actual worker group
to its minimum of 2. Consistent with billing (14,521 Fargate GB-hr ~= 2.1 workers avg).

Scheduler service (LBC-managed NLB — the endpoint `kmnlp_infra` reads to build
`DASK_SCHEDULER_ADDRESS`):

```
kmnlp-compute-scheduler   LoadBalancer   172.20.100.154
  k8s-default-kmnlpcom-df4d200bee-a3e95167a539b605.elb.us-east-1.amazonaws.com
  8786:31922/TCP,8787:31636/TCP
```

---

## Deployment plan

Staged so that a failure is attributable to a single change:

| Stage | Change                                              | Rationale                                                                           |
| ----- | --------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 1     | Add-ons + LBC v3 + Dask operator, still on **1.33** | Isolates "controller upgrade broke the NLB" from "Kubernetes upgrade broke the NLB" |
| 2     | Control plane + nodes -> **1.34**                   | Stops extended-support billing                                                      |
| 3     | -> **1.35**                                         | Watch: cgroup v1 removal; Fargate reportedly still uses cgroup v1                   |
| 4     | -> **1.36**                                         | Watch: `gitRepo` volumes disabled, kube-proxy IPVS removed                          |

After each hop: recycle Fargate pods so they pick up the new kubelet (existing Fargate
pods are never updated in place), then confirm the scheduler NLB hostname is unchanged
and Dask workers rejoin.

Rollback: EKS supports rolling back one minor version within 7 days of an upgrade.
Beyond that window there is no downgrade path.

---

## Run log

### 2026-09-08 — Preconditions verified

- `kubectl`, `terraform`, `cdktf`, `yq`, `jq` present. `helm` binary absent, which is
  fine: the Terraform helm provider embeds its own client and does not shell out.
- `aws sts get-caller-identity --profile kmnlp` succeeds.
- `aws eks update-kubeconfig --name dask-cluster` succeeded; `kubectl get nodes` works,
  so the SSO developer role's `EksAccessEntry` is functioning.
- Baseline captured above.

### 2026-09-08 — Two defects found and fixed before/during stage 1

**1. Missing `NullProvider` in the eks stack.** Synth failed with "Found resources without
a matching provider construct ... for the following providers: null". The new CRD
`null_resource` needs `NullProvider` registered, which `DemoKmnlpEksClusterStack` lacked
(only the dask and infra stacks had it). Fixed by adding `NullProvider(self, "null-provider")`
to that stack in `main.py`.

**2. Invalid CRD git ref.** The v3.0.0 release notes suggest
`kubectl apply -k "github.com/aws/eks-charts/.../crds?ref=master"`. I had pinned that to
`?ref=v3.5.0`, which failed — `aws/eks-charts` only carries `v0.0.x` tags, so the ref does
not exist:

```
fatal: couldn't find remote ref v3.5.0
```

Switched the source to the controller repo, which does publish version-pinned CRDs at
`kubernetes-sigs/aws-load-balancer-controller/v3.5.0/helm/aws-load-balancer-controller/crds/crds.yaml`
(verified HTTP 200). This is strictly better than the documented `?ref=master`, which is
unpinned and would drift. Also prepended `aws eks update-kubeconfig` so the step does not
depend on the operator's ambient kubectl context, matching the `wait-resource` pattern in
`dask_cluster.py`.

Note: the first (failed) apply was not atomic — the three add-ons and the Dask operator
upgrade had already committed before the CRD step failed. The LBC release was correctly
held back by its `depends_on`.

### 2026-09-08 — Stage 1 applied: add-ons + LBC v3 + Dask operator (cluster still 1.33)

Plan was `4 to add, 4 to change, 0 to destroy` — no cluster replacement. Confirmed from the
plan that the current controller is `app_version = "v2.13.4"`, validating that chart 1.13.4
is controller v2.13 and this is a single major hop, not two.

Result: **Apply complete. 1 added, 1 changed, 1 destroyed** (second run; the destroyed
resource is the tainted `null_resource` from the failed first run).

Verification:

| Check                      | Result                                                           |
| -------------------------- | ---------------------------------------------------------------- |
| `aws eks list-addons`      | `coredns`, `kube-proxy`, `vpc-cni` — now EKS-managed (was empty) |
| LBC image                  | `public.ecr.aws/eks/aws-load-balancer-controller:v3.5.0`         |
| LBC pods                   | 2/2 Running                                                      |
| CRDs                       | `ingressclassparams` + `targetgroupbindings` configured          |
| **Scheduler NLB hostname** | **unchanged from baseline**                                      |
| Dask                       | scheduler + 2 workers Running; autoscaler min 2 / max 20 intact  |

The NLB hostname being identical is the key result — the v2 -> v3 controller upgrade
adopted the existing load balancer rather than recreating it, so `DASK_SCHEDULER_ADDRESS`
in the `kmnlp_infra` stack remains valid and the API service was not disturbed.

kubectl emitted a benign warning that the CRDs were "missing the
`kubectl.kubernetes.io/last-applied-configuration` annotation" — expected, since Helm
originally created them; kubectl patched it automatically.

### 2026-09-08 — Stage 2 applied: Kubernetes 1.33 -> 1.34

`Apply complete! Resources: 0 added, 2 changed, 0 destroyed.` Control plane finished in
~6 min; the managed node group rolling replacement took **15m31s**.

| Check         | Result                                                                       |
| ------------- | ---------------------------------------------------------------------------- |
| Control plane | 1.34, `ACTIVE`, platform `eks.32`                                            |
| Node group    | 1.34, `ACTIVE`, Bottlerocket `1.45.0` -> `1.64.0`                            |
| EC2 nodes     | rolled to `v1.34.8`, settled back to desired size 2                          |
| Fargate nodes | `v1.34.9` after recycling                                                    |
| Scheduler NLB | hostname unchanged                                                           |
| Dask          | 2 workers `Registered to` scheduler; scheduler reports 2 workers / 8 threads |

During the roll the node group briefly surged to 4 instances (3 new + 1 draining) against
a `desired_size` of 2. Normal managed-node-group surge behaviour; it settled back to 2.
The drain was clean — `errors: []`, no pods stuck, and only the `aws-node` / `kube-proxy`
DaemonSets remained on the cordoned node before termination. Dask was unaffected during
the roll because it runs on Fargate, not this node group.

**Extended support billing confirmed stopped** (`USE1-AmazonEKS-Hours:extendedSupport`,
daily granularity):

```
2026-09-06  $12.00   <- 24 hr x $0.50
2026-09-07  $12.00
2026-09-08  $6.50    <- 13 hr, then ceased
```

#### Defect found in my own add-on design

After the upgrade the add-ons had not moved:

```
vpc-cni     v1.22.4-eksbuild.3    <- correct 1.34 default
kube-proxy  v1.33.10-eksbuild.2   <- stale
coredns     v1.12.4-eksbuild.29   <- correct 1.34 default
```

Omitting `addon_version` picks the right default **only at creation time**. On a version
bump Terraform has no attribute to diff, so it never triggers an add-on update — exactly
the drift the change was meant to eliminate. Only `kube-proxy` visibly lagged because it
is pinned to the Kubernetes minor; the other two happened to already be at the 1.34
default.

Fixed by sourcing `addon_version` from `DataAwsEksAddonVersion` keyed to
`_KUBERNETES_VERSION`, so each hop re-resolves the add-on to the default build for the new
version. Re-apply produced `0 to add, 1 to change, 0 to destroy` — Terraform correctly
identified `kube-proxy` alone as needing the update, confirming the other two were already
current.

#### Fargate pod recycling

Fargate pods are never updated in place, so they stayed on `v1.33.13` after the control
plane moved. `kubectl delete pods --all -n default` forced replacement; new pods came up
on `v1.34.9` nodes and both workers re-registered with the scheduler.

### 2026-09-08 — Stage 3 applied: Kubernetes 1.34 -> 1.35

`Apply complete! Resources: 0 added, 4 changed, 0 destroyed.`

| Check         | Result                                      |
| ------------- | ------------------------------------------- |
| Control plane | 1.35, `ACTIVE`                              |
| Node group    | 1.35, `ACTIVE`                              |
| EC2 nodes     | `v1.35.5`                                   |
| Fargate nodes | `v1.35.7` after recycling                   |
| Scheduler NLB | hostname unchanged                          |
| Dask          | 2 workers / 8 threads reported by scheduler |

**The add-on version fix proved itself on this hop.** Where the 1.34 upgrade left add-ons
untouched, this one moved them automatically:

```
kube-proxy  v1.34.6-eksbuild.21  ->  v1.35.3-eksbuild.21
coredns     v1.12.4-eksbuild.29  ->  v1.13.2-eksbuild.21
vpc-cni     v1.22.4-eksbuild.3   (unchanged; not tied to the Kubernetes minor)
```

That accounts for 4 changed resources: cluster, node group, and two add-ons.

#### cgroup v1 on Fargate — confirmed, and quantified

Recycling the Fargate pods on 1.35 produced a node event on every Fargate node:

```
Warning  CgroupV1  node/fargate-ip-10-81-95-137.ec2.internal
cgroup v1 detected. cgroup v1 support is deprecated and will be removed in a
future release. Please migrate to cgroup v2.
```

This confirms the AWS documentation note that "Fargate continues to use cgroup v1" while
Bottlerocket and AL2023 use cgroup v2. It is a **warning only** — all pods reached Ready
and the workers re-registered — because AWS sets the `FailCgroupV1=false` override on the
Fargate kubelet, exactly as it does for Bottlerocket.

Checked KEP-5573 before proceeding to 1.36, since a removal (rather than deprecation)
would break every Dask pod:

> We will commit to removing cgroup v1 code but there is not yet a timeline for removal.
> The removal will be done no earlier than 1.38 to maintain the k8s deprecation policy.

The KEP tracking issue shows `FailCgroupV1=true` shipped as **beta in 1.35** (the source of
this warning) and the "Stable" stage — actual code removal — is unscheduled, currently
sitting in SIG Node 1.38 planning. So 1.36 is safe.

**Forward risk to track:** the entire Dask cluster runs on Fargate. If cgroup v1 code is
removed in 1.38 or later and AWS has not migrated Fargate to cgroup v2 by then, Fargate
pods will not start on that version. This is a hard dependency on AWS, not something this
repo can mitigate. Worth re-checking before any future upgrade past 1.37, and a point in
favour of moving Dask workers to a managed node group (Bottlerocket, already cgroup v2)
rather than Fargate.

### 2026-09-08 — Stage 4 applied: Kubernetes 1.35 -> 1.36 (target reached)

`Apply complete! Resources: 0 added, 4 changed, 0 destroyed.`

Final state, all checks passing:

| Check                          | Result                                                                     |
| ------------------------------ | -------------------------------------------------------------------------- |
| Control plane                  | **1.36**, `ACTIVE`, platform `eks.12`                                      |
| Node group                     | **1.36**, `ACTIVE`                                                         |
| EC2 nodes                      | `v1.36.1`                                                                  |
| Fargate nodes                  | `v1.36.2`                                                                  |
| vpc-cni / kube-proxy / coredns | `v1.22.4-eksbuild.3` / `v1.36.0-eksbuild.17` / `v1.14.3-eksbuild.14`       |
| Load balancer controller       | `v3.5.0`, 2 replicas ready                                                 |
| Dask                           | 2 workers / 8 threads; live compute round-trip returned the correct result |
| Scheduler NLB                  | hostname **identical to baseline** across all four stages                  |
| Unhealthy pods                 | none, cluster-wide                                                         |

1.36-specific breaking changes screened against this stack, none applicable: `gitRepo`
volumes permanently disabled (not used), kube-proxy IPVS mode removed (EKS defaults to
iptables), `StrictIPCIDRValidation` enabled by default (no non-canonical CIDRs in the
manifests), SELinux volume labelling GA (no SELinux-enforcing nodes), Service
`externalIPs` deprecated (not used).

---

## Outcome

Cluster went 1.33 -> 1.36 across four applies with no resource replacement at any stage
(`0 to destroy` throughout) and no change to the Dask scheduler's load balancer endpoint,
so the `kmnlp_infra` stack's `DASK_SCHEDULER_ADDRESS` never needed touching.

**Recovered: $372/mo** of EKS extended-support charges, verified stopped in Cost Explorer
rather than assumed. Standard support for 1.36 also removes the near-term risk of the
charge returning.

Incidental improvements: node AMIs rolled for the first time in 382 days (Bottlerocket
1.45.0 -> 1.64.0), the three default add-ons are now EKS-managed and version-tracked
rather than frozen self-managed installs, and the load balancer controller moved from
v2.13.4 to v3.5.0.

### Still open from the cost analysis

| Item                                                | Est. $/mo | Notes                                                                                       |
| --------------------------------------------------- | --------: | ------------------------------------------------------------------------------------------- |
| ClickHouse on EFS                                   |      ~130 | 2,478 GB metered I/O against 18 GB stored. Needs an architecture change, not a config tweak |
| ECR lifecycle policy                                |       ~84 | 838 GB of images, no expiry rules                                                           |
| S3 gateway VPC endpoint                             |       ~25 | Removes NAT processing for Dask/S3 reads                                                    |
| Release idle Elastic IPs                            |        ~7 | $7.45 of `PublicIPv4:IdleAddress` in August                                                 |
| CloudWatch custom metrics / Container Insights mode |       ~20 | 477 custom metrics; `enhanced` observability plus Langfuse metric publishing                |

Not KMNLP, raised separately: `nccf-dev-archive-neptune` (~$262/mo).
