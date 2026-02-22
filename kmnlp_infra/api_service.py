import json

from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.data_aws_secretsmanager_secret import DataAwsSecretsmanagerSecret
from cdktf_cdktf_provider_aws.db_subnet_group import DbSubnetGroup
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
from cdktf_cdktf_provider_aws.rds_cluster import RdsCluster
from cdktf_cdktf_provider_aws.rds_cluster_instance import RdsClusterInstance
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.components.public_alb import PublicAlb
from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.opensearch import Opensearch
from kmnlp_infra.utils.policies import (
    create_assume_role_policy_for_aws_service,
    create_comprehensive_bedrock_statement,
    create_policy,
    create_put_s3_bucket_statement,
    create_read_all_buckets_statement,
    create_read_s3_bucket_statement,
    create_read_secret_statement,
)

_API_PORT = 8000
_DB_PORT = 5432


class _ApiEcsService(Construct):
    """Create the API ECS Service, running using Fargate."""

    langfuse_api_key_secret: DataAwsSecretsmanagerSecret
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

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        ecs_cluster: EcsCluster,
        opensearch: Opensearch,
        dask_scheduler_address: str,
        lb_target_group_arn: str,
    ) -> None:
        super().__init__(scope, id)

        service_config = config.api

        self.langfuse_api_key_secret = DataAwsSecretsmanagerSecret(
            self, "langfuse_api_key_secret", name="langfuse_api_keys"
        )

        self.image = DataAwsEcrImage(
            self,
            "image",
            repository_name=service_config.image.repository_name,
            image_tag=service_config.image.tag,
        )

        self.task_execution_role = IamRole(
            self,
            "execution_role",
            name="api-ecs-execution-role",
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
        self.read_secrets_policy = IamRolePolicy(
            self,
            "read_secrets_policy",
            name="ReadSecrets",
            role=self.task_execution_role.id,
            policy=create_policy(
                create_read_secret_statement(self.langfuse_api_key_secret.arn),
            ),
        )

        self.task_role = IamRole(
            self,
            "task_role",
            name="api-task-role",
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

        # API doesn't need to read these GeoTIFFs. Only create them.
        self.task_role_s3_put_test_geotiff_bucket_policy = IamRolePolicy(
            self,
            "task-role-s3-put-geotiff-policy",
            name="PutS3GeotiffBucket",
            policy=create_policy(create_put_s3_bucket_statement(config.test_geotiff_bucket)),
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
            "DASK_SCHEDULER_ADDRESS": dask_scheduler_address,
            "GEOCODE_INDEX_HOST": opensearch.host,
            "GEOCODE_INDEX_REGION": config.aws_account.region,
            # To enable CORS from the frontend
            "FRONTEND_ENDPOINT": f"https://{config.frontend.domain}",
            "LANGFUSE_HOST": f"https://{config.langfuse.web.domain}",
        }

        container_def = {
            "name": "kmnlp-api",
            "cpu": 0,  # doesn't actually mean 0
            "image": self.image.image_uri,
            "environment": [{"name": name, "value": value} for name, value in env_vars.items()],
            "secrets": [
                {
                    "name": "LANGFUSE_SECRET_KEY",
                    "valueFrom": f"{self.langfuse_api_key_secret.arn}:SECRET_KEY::",
                },
                {
                    "name": "LANGFUSE_PUBLIC_KEY",
                    "valueFrom": f"{self.langfuse_api_key_secret.arn}:PUBLIC_KEY::",
                },
            ],
            "essential": True,
            "volumesFrom": [],
            "memory": service_config.memory_gb * 1024,
            "portMappings": [
                {
                    "containerPort": _API_PORT,
                    "hostPort": _API_PORT,
                    "protocol": "tcp",
                }
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": "kmnlp-api",
                    "awslogs-region": config.aws_account.region,
                    "awslogs-create-group": "true",
                    "awslogs-stream-prefix": "demo",
                    # Matches one of the following:
                    # - 2025-10-23 11:45:26 - llm_agent.agents.tool_only_agent - INFO -...
                    # - INFO: 123.123.123.123:4356 - "GET /health HTTP/1.1" 200 OK
                    "awslogs-multiline-pattern": (
                        "(^\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2}\\s-\\s)|(^INFO:)"
                    ),
                },
            },
        }

        self.task_def = EcsTaskDefinition(
            self,
            "task_def",
            cpu=str(1024 * service_config.num_cpus),
            execution_role_arn=self.task_execution_role.arn,
            memory=str(service_config.memory_mb),
            network_mode="awsvpc",
            runtime_platform={
                "operating_system_family": "LINUX",
                "cpu_architecture": "X86_64",
            },
            requires_compatibilities=["FARGATE"],
            task_role_arn=self.task_role.arn,
            container_definitions=json.dumps([container_def]),
            family="api-task-family",
        )

        self.security_group = SecurityGroup(
            self,
            "security_group",
            name="kmnlp-api-ecs",
            description=(
                "Security group for the API ECS Service, "
                "allowing ingress from our VPC and all egress."
            ),
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    cidr_blocks=[network.vpc.cidr_block],
                    from_port=_API_PORT,
                    to_port=_API_PORT,
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
            tags={"Name": "kmnlp-api-ecs"},
        )

        self.ecs_service = EcsService(
            self,
            "ecs_service",
            name="kmnlp-api",
            cluster=ecs_cluster.arn,
            desired_count=service_config.num_instances,
            launch_type="FARGATE",
            load_balancer=[
                EcsServiceLoadBalancer(
                    container_name=str(container_def["name"]),
                    container_port=_API_PORT,
                    target_group_arn=lb_target_group_arn,
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


class _PostgresDB(Construct):
    """Postgres DB for API service."""

    sg: SecurityGroup
    db_subnet_group: DbSubnetGroup
    api_db_cluster: RdsCluster
    api_db_instance: RdsClusterInstance

    def __init__(self, scope: Construct, id: str, network: Network) -> None:
        super().__init__(scope, id)

        name = "api"

        # Security group for RDS
        self.sg = SecurityGroup(
            self,
            "sg",
            name=f"{name}-db",
            description=f"Security group for the {name} database",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=_DB_PORT,
                    to_port=_DB_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                )
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
                )
            ],
            tags={"Name": f"{name}-db"},
        )

        # DB subnet group
        self.db_subnet_group = DbSubnetGroup(
            self,
            "db_subnet_group",
            name=f"{name}-db",
            subnet_ids=network.private_subnet_ids,
            tags={"Name": f"{name}-db"},
        )

        self.api_db_cluster = RdsCluster(
            scope,
            "kmnlp_api_db_cluster",
            engine="aurora-postgresql",
            cluster_identifier=f"{name}-db",
            database_name=name,
            db_subnet_group_name=self.db_subnet_group.name,
            vpc_security_group_ids=[self.sg.id],
            manage_master_user_password=True,
            master_username="kmnlp_api_prod",
            skip_final_snapshot=True,
            tags={"Name": f"{name}-db"},
        )

        self.api_db_instance = RdsClusterInstance(
            self,
            "db_instance",
            apply_immediately=True,
            identifier="kmnlp-api-db",
            cluster_identifier=self.api_db_cluster.id,
            instance_class="db.t3.medium",
            engine=self.api_db_cluster.engine,
            engine_version=self.api_db_cluster.engine_version,
        )


class Api(Construct):
    """Defines a public API running as an ecs service."""

    api_db: _PostgresDB
    alb: PublicAlb
    ecs_service: _ApiEcsService

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        ecs_cluster: EcsCluster,
        opensearch: Opensearch,
        dask_scheduler_address: str,
    ) -> None:
        super().__init__(scope, id)

        # Crate database
        self.api_db = _PostgresDB(
            scope,
            "kmnlp_api_db",
            network,
        )

        # FUTURE change this to allow specifying a list of allowed ip addresses
        self.alb = PublicAlb(
            self,
            "alb",
            config,
            network,
            name="kmnlp-api",
            domain=config.api.domain,
            service_port=_API_PORT,
            health_check_path="/health",
        )

        self.ecs_service = _ApiEcsService(
            self,
            "ecs_service",
            config,
            network,
            ecs_cluster=ecs_cluster,
            opensearch=opensearch,
            dask_scheduler_address=dask_scheduler_address,
            lb_target_group_arn=self.alb.lb_target_group.arn,
        )
