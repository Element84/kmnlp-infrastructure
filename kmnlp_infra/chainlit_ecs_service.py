import json

from cdktf_cdktf_provider_aws.data_aws_acm_certificate import DataAwsAcmCertificate
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.ecs_service import (
    EcsService,
    EcsServiceLoadBalancer,
    EcsServiceNetworkConfiguration,
)
from cdktf_cdktf_provider_aws.ecs_task_definition import EcsTaskDefinition
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_aws.lb import Lb, LbAccessLogs
from cdktf_cdktf_provider_aws.lb_listener import (
    LbListener,
    LbListenerDefaultAction,
    LbListenerDefaultActionRedirect,
)
from cdktf_cdktf_provider_aws.lb_target_group import (
    LbTargetGroup,
    LbTargetGroupHealthCheck,
)
from cdktf_cdktf_provider_aws.s3_bucket import S3Bucket
from cdktf_cdktf_provider_aws.s3_bucket_policy import S3BucketPolicy
from cdktf_cdktf_provider_aws.s3_bucket_server_side_encryption_configuration import (
    S3BucketServerSideEncryptionConfigurationA,
    S3BucketServerSideEncryptionConfigurationRuleA,
    S3BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultA,
)
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.opensearch import Opensearch
from kmnlp_infra.utils.common import ssm_param_to_https_security_group_ingress
from kmnlp_infra.utils.policies import (
    create_assume_role_policy_for_aws_service,
    create_comprehensive_bedrock_statement,
    create_elb_put_s3_bucket_statement,
    create_policy,
    create_read_all_buckets_statement,
    create_read_s3_bucket_statement,
)

CHAINLIT_PORT = 8000


# FUTURE refactor to use the public alb component
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
    s3_bucket: S3Bucket
    s3_bucket_encryption: S3BucketServerSideEncryptionConfigurationA
    lb_access_logs: LbAccessLogs
    load_balancer: Lb
    redirect_lb_listener: LbListener

    def __init__(
        self, scope: Construct, id: str, config: Config, network: Network, *, name: str, domain: str
    ) -> None:
        super().__init__(scope, id=id)

        self.certificate = DataAwsAcmCertificate(
            self, "certificate", domain=domain, most_recent=True, types=["AMAZON_ISSUED"]
        )

        self.lb_sg = SecurityGroup(
            self,
            "lb_sg",
            name=name,
            description=f"{name} Load balancer security group",
            vpc_id=network.vpc.id,
            ingress=ssm_param_to_https_security_group_ingress(
                "e84-kmnlp-demo-chainlit-allowed-cidrs-dict"
            ),
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"], protocol="-1", from_port=0, to_port=0
                )
            ],
            tags={"Name": name},
        )

        self.s3_bucket = S3Bucket(
            self,
            "access_log_bucket",
            bucket="kmnlp-alb-access-logs",
        )

        self.s3_bucket_encryption = S3BucketServerSideEncryptionConfigurationA(
            self,
            "access_log_bucket_encryption",
            bucket=self.s3_bucket.id,
            rule=[
                S3BucketServerSideEncryptionConfigurationRuleA(
                    apply_server_side_encryption_by_default=S3BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultA(
                        sse_algorithm="AES256"
                    )
                )
            ],
        )

        self.lb_access_logs = LbAccessLogs(
            bucket=self.s3_bucket.bucket,
            enabled=True,
            prefix=None,
        )

        self.bucket_policy = S3BucketPolicy(
            self,
            "access_log_bucket_policy",
            bucket=self.s3_bucket.id,
            policy=create_policy(
                create_elb_put_s3_bucket_statement(
                    self.s3_bucket.bucket, config.aws_account.elb_region_acount_id
                )
            ),
        )

        self.load_balancer = Lb(
            self,
            "load_balancer",
            name=name,
            security_groups=[self.lb_sg.id],
            subnets=network.public_subnet_ids,
            access_logs=self.lb_access_logs,
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


class ChainlitEcsService(Construct):
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
    task_execution_role_policy: IamRolePolicy
    task_execution_role_managed_policy_attachment: IamRolePolicyAttachment
    task_role: IamRole
    task_role_bedrock_policy: IamRolePolicy
    task_role_s3_data_policy: IamRolePolicy
    task_role_s3_all_policy: IamRolePolicy
    task_def: EcsTaskDefinition
    security_group: SecurityGroup
    ecs_service: EcsService
    lb_target_group: LbTargetGroup
    lb_listener: LbListener

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        ecs_cluster: EcsCluster,
        alb: Alb,
        opensearch: Opensearch,
        dask_scheduler_address: str,
    ) -> None:
        super().__init__(scope, id)

        self.image = DataAwsEcrImage(
            self,
            "image",
            repository_name=config.chainlit.image.repository_name,
            image_tag=config.chainlit.image.tag,
        )

        self.task_execution_role = IamRole(
            self,
            "execution_role",
            name="demo-kmnlp-ecs-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        self.task_execution_role_managed_policy_attachment = IamRolePolicyAttachment(
            self,
            "task-execution-role-managed-policy-attachment",
            role=self.task_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )

        self.task_execution_role_policy = IamRolePolicy(
            self,
            "task-execution-role-policy",
            name="CreateLogGroup",
            policy=create_policy(
                {
                    "Action": "logs:CreateLogGroup",
                    "Effect": "Allow",
                    "Resource": "*",
                }
            ),
            role=self.task_execution_role.name,
        )

        self.task_role = IamRole(
            self,
            "task_role",
            name="demo-kmnlp-iam-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        self.task_role_bedrock_policy = IamRolePolicy(
            self,
            "task-role-bedrock-policy",
            name="UseBedrock",
            policy=create_policy(create_comprehensive_bedrock_statement()),
            role=self.task_role.name,
        )

        self.task_role_s3_data_policy = IamRolePolicy(
            self,
            "task-role-s3-data-policy",
            name="ReadS3DataBucket",
            policy=create_policy(create_read_s3_bucket_statement(config.data_bucket)),
            role=self.task_role.name,
        )

        self.task_role_s3_all_policy = IamRolePolicy(
            self,
            "task-role-s3-all-policy",
            name="ReadAllS3Buckets",
            policy=create_policy(create_read_all_buckets_statement()),
            role=self.task_role.name,
        )

        env_vars: dict[str, str] = {
            "DASK_ADDRESS": dask_scheduler_address,
            "GEOCODE_INDEX_HOST": opensearch.host,
            "GEOCODE_INDEX_REGION": config.aws_account.region,
        }

        container_def = {
            "name": "demo-kmnlp-chainlit",
            "cpu": 0,  # doesn't actually mean 0
            "image": self.image.image_uri,
            "environment": [{"name": name, "value": value} for name, value in env_vars.items()],
            "essential": True,
            "volumesFrom": [],
            "memory": config.chainlit.memory_gb * 1024,
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
                    "awslogs-region": config.aws_account.region,
                    "awslogs-create-group": "true",
                    "awslogs-stream-prefix": "demo",
                },
            },
        }

        self.task_def = EcsTaskDefinition(
            self,
            "task_def",
            cpu=str(1024 * config.chainlit.num_cpus),
            execution_role_arn=self.task_execution_role.arn,
            memory=str(config.chainlit.memory_mb),
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
            ssl_policy="ELBSecurityPolicy-TLS13-1-0-2021-06",
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
            desired_count=config.chainlit.num_instances,
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
            triggers={"image_pushed_at": str(self.image.image_pushed_at)},
            timeouts={"create": "5m", "update": "5m"},
        )
