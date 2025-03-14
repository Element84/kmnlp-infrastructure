import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_ssm.client import SSMClient

import boto3
import yaml
from cdktf_cdktf_provider_aws.data_aws_acm_certificate import DataAwsAcmCertificate
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.ecs_service import (
    EcsService,
    EcsServiceLoadBalancer,
    EcsServiceNetworkConfiguration,
)
from cdktf_cdktf_provider_aws.ecs_task_definition import EcsTaskDefinition
from cdktf_cdktf_provider_aws.iam_role import IamRole, IamRoleInlinePolicy
from cdktf_cdktf_provider_aws.lb import Lb
from cdktf_cdktf_provider_aws.lb_listener import (
    LbListener,
    LbListenerDefaultAction,
    LbListenerDefaultActionRedirect,
)
from cdktf_cdktf_provider_aws.lb_target_group import (
    LbTargetGroup,
    LbTargetGroupHealthCheck,
)
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.common import (
    CLAUDE_3_5_SONNET,
    CLAUDE_3_HAIKU,
    DASK_ADDRESS,
    REGION,
    S3_DATA_BUCKET,
    Network,
    create_assume_role_policy_for_aws_service,
    create_invoke_model_statement,
    create_list_s3_bucket_statement,
    create_policy,
    create_read_s3_bucket_statement,
)

_NUM_CPUS = 8
_MEMORY_GB = 32

CHAINLIT_PORT = 8000

# FUTURE Create this in Terraform or get a persistent one up and running
ZARR_REFERENCE_PATH = (
    "s3://data-c6c22a2e42294c11b52ee7f0c792c071/crw/5km/v3.1/nc/v1.0/daily/sst/zarr_reference.json"
)


def _create_ingress(cidr: str, port: int, desc: str) -> SecurityGroupIngress:
    return SecurityGroupIngress(
        cidr_blocks=[cidr],
        description=desc,
        from_port=port,
        to_port=port,
        protocol="tcp",
    )


class Alb(Construct):
    """Create the ALB for the Chainlit service.

    Pieces include:
        * certificate
        * security group
        * load balancer
        * load balancer listener
    """

    certificate: DataAwsAcmCertificate
    lb_sg: SecurityGroup
    load_balancer: Lb
    redirect_lb_listener: LbListener

    def __init__(
        self, scope: Construct, id: str, network: Network, *, name: str, domain: str
    ) -> None:
        super().__init__(scope, id=id)

        self.certificate = DataAwsAcmCertificate(
            self, "certificate", domain=domain, most_recent=True, types=["AMAZON_ISSUED"]
        )


        boto3_client: SSMClient = boto3.client("ssm", region_name=REGION) # type: ignore[reportUnknownMemberType]
        allowed_cidrs_param_value: str = \
                boto3_client.get_parameter(Name="e84-kmnlp-demo-chainlit-allowed-cidrs-dict")["Parameter"]["Value"]
        parsed_value: dict[str, str] = yaml.safe_load(allowed_cidrs_param_value)

        self.lb_sg = SecurityGroup(
            self,
            "lb_sg",
            name=name,
            description=f"{name} Load balancer security group",
            vpc_id=network.vpc.id,
            ingress=[
                ingress
                for cidr_name, cidr in parsed_value.items()
                for ingress in [
                    _create_ingress(cidr, 443, cidr_name),
                    _create_ingress(cidr, 80, f"{cidr_name} for redirect"),
                ]
            ],
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"], protocol="-1", from_port=0, to_port=0
                )
            ],
            tags={"Name": name},
        )

        self.load_balancer = Lb(
            self,
            "load_balancer",
            name=name,
            security_groups=[self.lb_sg.id],
            subnets=network.public_subnet_ids,
        )

        self.redirect_lb_listener = LbListener(
            self,
            "lb_listener",
            load_balancer_arn=self.load_balancer.arn,
            port=80,
            protocol="HTTP",
            default_action=[
                LbListenerDefaultAction(
                    type="redirect",
                    redirect=LbListenerDefaultActionRedirect(
                        port="443", protocol="HTTPS", status_code="HTTP_301"
                    ),
                )
            ],
        )

class DemoKmnlpChainlitEcsService(Construct):
    """Create the Demo Chainlit ECS Service, running using Fargate.

    Pieces include:
        * Assigning container image
        * Setting env vars and defining container definition
        * Setting security groups
        * Adding load balancers
        * Creating ECS Service
    """

    image: DataAwsEcrImage
    task_execution_role: IamRole
    task_role: IamRole
    task_def: EcsTaskDefinition
    security_group: SecurityGroup
    ecs_service: EcsService
    lb_target_group: LbTargetGroup
    lb_listener: LbListener

    def __init__(
        self,
        scope: Construct,
        id: str,
        network: Network,
        *,
        ecs_cluster: EcsCluster,
        alb: Alb,
    ) -> None:
        super().__init__(scope, id)

        self.image = DataAwsEcrImage(
            self,
            "image",
            repository_name="chainlit-demo/chainlit",
            most_recent=True,
        )

        self.task_execution_role = IamRole(
            self,
            "execution_role",
            name="demo-kmnlp-ecs-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
            ],
            inline_policy=[
                IamRoleInlinePolicy(
                    name="CreateLogGroup",
                    policy=create_policy(
                        {
                            "Action": "logs:CreateLogGroup",
                            "Effect": "Allow",
                            "Resource": "*",
                        }
                    ),
                )
            ],
        )

        self.task_role = IamRole(
            self,
            "task_role",
            name="demo-kmnlp-iam-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
            inline_policy=[
                IamRoleInlinePolicy(
                    name="UseBedrockSonnet",
                    policy=create_policy(create_invoke_model_statement(CLAUDE_3_5_SONNET)),
                ),
                IamRoleInlinePolicy(
                    name="UseBedrockHaiku",  # Needed for natural language to polygon calls
                    policy=create_policy(create_invoke_model_statement(CLAUDE_3_HAIKU)),
                ),
                IamRoleInlinePolicy(
                    name="ListS3Bucket",
                    policy=create_policy(create_list_s3_bucket_statement(S3_DATA_BUCKET)),
                ),
                IamRoleInlinePolicy(
                    name="ReadS3Bucket",
                    policy=create_policy(create_read_s3_bucket_statement(S3_DATA_BUCKET)),
                ),
            ],
        )

        env_vars: dict[str, str] = {
            "DASK_ADDRESS": DASK_ADDRESS,
            "ZARR_REFERENCE_PATH": ZARR_REFERENCE_PATH,
        }

        container_def = {
            "name": "demo-kmnlp-chainlit",
            "cpu": 0,  # doesn't actually mean 0
            "image": self.image.image_uri,
            "environment": [{"name": name, "value": value} for name, value in env_vars.items()],
            "essential": True,
            "volumesFrom": [],
            "memory": _MEMORY_GB * 1024,
            "portMappings": [
                {
                    "containerPort": CHAINLIT_PORT,
                    "hostPort": CHAINLIT_PORT,
                    "protocol": "tcp",
                }
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "demo-kmnlp-chainlit",
                    "awslogs-region": REGION,
                    "awslogs-create-group": "true",
                    "awslogs-stream-prefix": "demo",
                },
            },
        }

        self.task_def = EcsTaskDefinition(
            self,
            "task_def",
            cpu=str(1024 * _NUM_CPUS),
            execution_role_arn=self.task_execution_role.arn,
            memory=str(1024 * _MEMORY_GB),
            network_mode="awsvpc",
            runtime_platform={
                "operating_system_family": "LINUX",
                "cpu_architecture": "X86_64",
            },
            requires_compatibilities=["FARGATE"],
            task_role_arn=self.task_role.arn,
            container_definitions=json.dumps([container_def]),
            family="demo-kmnlp-chainlit-task-family",
        )

        self.security_group = SecurityGroup(
            self,
            "security_group",
            name="demo-kmnlp-chainlit-ecs",
            description=(
                "Security group for the Demo KMNLP Chainlit ECS Service, "
                "allowing ingress from our VPC and all egress."
            ),
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    cidr_blocks=[network.vpc.cidr_block],
                    from_port=CHAINLIT_PORT,
                    to_port=CHAINLIT_PORT,
                    protocol="tcp",
                ),
            ],
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"],
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                )
            ],
            tags={"Name": "demo-kmnlp-chainlit-ecs"},
        )

        self.lb_target_group = LbTargetGroup(
            self,
            "lb_target_group",
            name="demo-kmnlp-target-group",
            port=CHAINLIT_PORT,
            protocol="HTTP",
            target_type="ip",
            vpc_id=network.vpc.id,
            health_check=LbTargetGroupHealthCheck(
                enabled=True,
                port="traffic-port",
            ),
        )

        self.lb_listener = LbListener(
            self,
            "lb_listener",
            load_balancer_arn=alb.load_balancer.arn,
            certificate_arn=alb.certificate.arn,
            port=443,
            protocol="HTTPS",
            ssl_policy="ELBSecurityPolicy-2016-08",
            default_action=[
                LbListenerDefaultAction(
                    type="forward",
                    target_group_arn=self.lb_target_group.arn,
                )
            ],
        )

        self.ecs_service = EcsService(
            self,
            "ecs_service",
            name="demo-kmnlp-ecs-service",
            cluster=ecs_cluster.arn,
            desired_count=1,
            launch_type="FARGATE",
            load_balancer=[
                EcsServiceLoadBalancer(
                    container_name=str(container_def["name"]),
                    container_port=CHAINLIT_PORT,
                    target_group_arn=self.lb_target_group.arn,
                )
            ],
            network_configuration=EcsServiceNetworkConfiguration(
                subnets=network.private_subnet_ids,
                security_groups=[self.security_group.id],
            ),
            wait_for_steady_state=True,
            task_definition=self.task_def.arn,
            force_new_deployment=True,
            triggers={"image_pushed_at": str(self.image.image_pushed_at)},
        )
