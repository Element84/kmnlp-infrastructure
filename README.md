# kmnlp_infrastructure

Defines the infrastructure for the KM NLP Project.

## Developer Setup

1. Install cdktf and terraform
   1. `brew install cdktf terraform` on the mac
1. Checkout the code.
1. Create/activate your Python environment of choice (skip if only using uv).
1. Install uv: `pip install uv` (skip if only using uv).
1. Install dependencies: `uv sync --all-extras`.
1. Run `pre-commit install` to install pre-commit hooks.
1. Configure your editor for realtime linting:
	- For VS Code:
		- Set the correct Python environment for the workspace via `ctrl+shift+P` > `Python: Select Interpreter`.
		- Install the Pylance and Ruff extensions.
1.  Make changes.
1. Verify linting passes `scripts/lint.sh`.
1. Verify tests pass `scripts/test.sh`.
1. Commit and push your changes.

## Stacks

The full infrastructure is deployed in 4 stacks:
1. `bootstrap`
1. `eks_cluster`
1. `dask_cluster`
1. `kmnlp_infra`

In the most common workflow, only `dask_cluster` and `kmnlp_infra` need to be deployed.

### `bootstrap`

#### Intent

Create the things that need to be present for CI deploy to work.

#### Content

The deploy role the CI job will assume.

### `eks_cluster`

#### Intent

Create the EKS Cluster where Kubernetes work (i.e. the Dask Cluster) will be scheduled. (Unfortunately, the word "cluster" is overloaded.)

#### Content

* EKS Cluster
* Roles to manage it
* AWS Controller to create ELB for exposed scheduler service (created in Dask Cluster)
* Dask Operator to manage creating all resources needed for Dask Cluster and Dask Autoscaler
* Node pool to schedule system tasks
* Fargate profile to schedule Dask workers and scheduler
* Managed add-ons (`vpc-cni`, `kube-proxy`, `coredns`), version-tracked to the cluster version

#### Upgrading Kubernetes

The cluster version is pinned by `_KUBERNETES_VERSION` in `kmnlp_infra/eks_cluster.py` and
applies to both the control plane and the node group. Running a version past its standard
support window adds an **extra $0.50/hr** (~$372/mo) in EKS extended support charges, so
don't let it drift.

EKS upgrades **one minor version at a time**, so each hop is a separate apply: bump the
constant, `scripts/deploy.sh eks_cluster`, then recycle the Fargate pods with
`kubectl delete pods --all -n default`. Fargate pods never pick up a new kubelet in place,
so without that step they stay on the old version indefinitely. Expect ~15 minutes for the
node group rolling replacement on each hop.

> [!WARNING]
> **Do not upgrade past Kubernetes 1.37 without first checking that AWS has migrated EKS
> Fargate to cgroup v2.**
>
> Every Dask worker and the scheduler run on the Fargate profile, and Fargate nodes still
> use cgroup v1 — they log a `CgroupV1` warning event on each launch. Kubernetes 1.35 made
> the kubelet refuse to start on cgroup v1 by default, and AWS currently keeps Fargate
> working through the `FailCgroupV1=false` override. [KEP-5573](https://git.k8s.io/enhancements/keps/sig-node/5573-remove-cgroup-v1)
> removes the cgroup v1 code entirely "no earlier than 1.38", which also removes that
> override. If AWS has not moved Fargate to cgroup v2 by then, **no pod matched by the
> Fargate profile will start** on that version.
>
> Bottlerocket node groups are already cgroup v2, so moving the Dask workers off Fargate is
> the durable fix.

### `dask_cluster`

#### Intent

Create the Dask Cluster running inside the EKS Cluster

#### Content

* Dask Cluster
* Dask Autoscaler to autoscale the Dask Cluster (currently not very useful)

### `kmnlp_infra`

#### Intent

The demo app.

#### Content

* ECS Service to run the app, referencing the Dask Cluster's scheduler service
* Load balancer to expose the app

## Manual Deploy

Assumes you've done the developer setup

1. Copy `.env.template` to `.env` and modify as needed
2. Connect as necessary to AWS to get credentials or login with your profile.
3. Run `scripts/deploy.sh bootstrap`
4. Run `scripts/deploy.sh eks_cluster`
5. Run `scripts/deploy.sh dask_cluster`
6. Run `scripts/deploy.sh kmnlp_infra`
7. Check to see whether the service is up at https://demo.kmnlp.element84.com/
    * If the page doesn't resolve, either your IP address isn't in the allow list, or the deploy created the load balancer (rather than updating an existing one).


### If your IP address is not in the allow list
1. Get your public IP address.
1. Go to the AWS Console
1. Use search to go to Systems Manager
1. Click on "Parameter Store"
1. Select `e84-kmnlp-demo-chainlit-allowed-cidrs`
1. Click "Edit"
1. Add your IP address appended with `/32` to the value. (Individual items are comma-separated.)

### If the load balancer is new (or the route is otherwise out of date)
1. Login to the AWS Console.
1. Search for "Load balancers" and select the one labeled "EC2 Feature"
1. Select the relevant load balancer. It should be named: `demo-kmnlp-chainlit-alb`.
1. Copy the DNS name
1. Navigate to Route 53 in the AWS Console. (I like to do this in a separate tab.)
1. Select "Hosted Zones".
1. Select `kmnlp.element84.com`.
1. Select the CNAME record whose record name is `demo.kmnlp.element84.com`.
1. Click on "Edit record"
1. Replace the value with the DNS name you copied from the load balancer.
1. Hit save.

### Other notes

In order for the deploy to work, there must be a certificate present for the domain demo.kmnlp.element84.com. If this certificate disappears for whatever reason, or if the domain name is changing:

1. Go to the AWS Console.
1. Use search to go to Certificate Manager.
1. Click on Request.
1. Select "Request a public certificate" and click Next..
1. Enter the fully qualified domain name (e.g. demo.kmnlp.element84.com).
1. For validation method, use DNS validation.
1. The default key algorithm should be fine.
1. Click Request.
1. From the resulting certificate, in the Domains section, there is a button to "Create records in Route 53". Click it.
1. Follow whatever the steps are here to create the relevant CNAME record.

The deploy also needs to have a VPC to work. The VPC's ID is hard-coded in the common.py file. Currently, we assume that the VPC is in multiple availability zones and has both public and private subnets in each zone.
