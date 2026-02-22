# kmnlp_infrastructure

Defines the infrastructure for the KM NLP Project.


## Dask Cluster Planning Temporary Notes

Based on https://gist.github.com/jacobtomlinson/ee5ba79228e42bcc9975faf0179c3d1a

Input
* cluster arn

INfrastructure

* IAM roles
  * dask-fargate-execution
  * dask-fargate-task
* Security Groups
  * dask
    * Allow 8786 - 8787 from things that are communicating wiht dask
    * Ephemeral port access from itself?
* Cloud watch log group
* ECS Tasks
  * dask-scheduler
  * dask-worker


Based on dask cloud provider source

1. Create cluster in ECS
2. Create execution role - 1061
   1. ecs-tasks asume role and attached policies
3. Create task role
4. Create cloud watch logs group
5. Create security group
   1. 8786 and 8787 for external
   2. 0 - 65535 from the group itself
6. Creates the scheduler task def
7. Create the worker task def

Scaling up
1. Scheduler task is started
   1. An ecs task is run
2. TODO continue here. It should just be starting the workers


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
