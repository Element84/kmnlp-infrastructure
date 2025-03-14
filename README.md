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
2. Checkout the code.
3. Create/activate your Python environment of choice.
4. Install uv: `pip install uv`.
5. Install dependencies: `uv pip install -r pyproject.toml`.
6. Install dev dependencies: `uv pip install -r pyproject.toml --extra dev`.
7. Run `pre-commit install` to install pre-commit hooks.
8. Configure your editor for realtime linting:
	- For VS Code:
		- Set the correct Python environment for the workspace via `ctrl+shift+P` > `Python: Select Interpreter`.
		- Install the Pylance and Ruff extensions.
9.  Make changes.
10. Verify linting passes `scripts/lint.sh`.
11. Verify tests pass `scripts/test.sh`.
12. Commit and push your changes.


## Manual Deploy

Assumes you've done the developer setup

1. Copy `.env.template` to `.env` and modify as needed
    - At the very least, you will likely need to un-comment the `AWS_PROFILE` value.
1. Connect as necessary to AWS to get credentials or login with your profile.
1. Start the dask cluster in AWS from the `demo-app` project. (Instructions in that project's README.) NOTE: Do not hit enter when it finishes. It'll tear down the cluster.
1. Once the clsuter starts, copy the tcp address of the scheduler, and paste as the value of `DASK_ADDRESS` in your `.env` file.
1. Run `scripts/deploy.sh`
1. Check to see whether the service is up at https://demo.kmnlp.element84.com/
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
