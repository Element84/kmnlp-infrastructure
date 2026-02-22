import json

from cdktf_cdktf_provider_aws.data_aws_acm_certificate import DataAwsAcmCertificate
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.data_aws_secretsmanager_secret import DataAwsSecretsmanagerSecret
from cdktf_cdktf_provider_aws.data_aws_secretsmanager_secret_version import (
    DataAwsSecretsmanagerSecretVersion,
)
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
from cdktf_cdktf_provider_aws.s3_bucket import S3Bucket
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.components.clickhouse import ClickHouse
from kmnlp_infra.components.postgres_db import PostgresDB
from kmnlp_infra.components.redis import Redis
from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.common import ssm_param_to_https_security_group_ingress
from kmnlp_infra.utils.policies import (
    create_assume_role_policy_for_aws_service,
    create_cloudwatch_put_metric_data_statements,
    create_comprehensive_bedrock_statement,
    create_policy,
    create_read_secret_statement,
)

_WEB_PORT = 3000
_WORKER_PORT = 3030


class _LangfuseDependencies(Construct):
    """Defines infrastructure for langfuse including ECS services for web and worker."""

    postgres_db: PostgresDB
    db_url_secret: DataAwsSecretsmanagerSecret
    redis: Redis
    clickhouse: ClickHouse
    s3_bucket: S3Bucket

    # This lanfuse image was manually pulled and pushed this way
    # docker pull langfuse/langfuse:3.115.0
    # ACCOUNT_ID=...  # noqa: ERA001
    # REGION=...  # noqa: ERA001
    # IMAGE_NAME="kmnlp/langfuse"  # noqa: ERA001
    # ecr_url="${ACCOUNT_ID:?}.dkr.ecr.${REGION:?}.amazonaws.com"  # noqa: ERA001
    # ecr_tag="${ecr_url}/${IMAGE_NAME:?}"  # noqa: ERA001
    # aws ecr get-login-password |   docker login --username AWS --password-stdin "$ecr_tag"
    # -- tagged it as latest
    # docker tag langfuse/langfuse:3.115.0 "$ecr_tag"
    # -- tagged it with the original version
    # docker tag "$ecr_tag" "$ecr_tag:3.115.0"
    # --- pushed both tags
    # docker push "$ecr_tag"
    # docker push "$ecr_tag:3.115.0"

    langfuse_web_image: DataAwsEcrImage
    # docker pull langfuse/langfuse-worker:3.115.0
    # ACCOUNT_ID=...  # noqa: ERA001
    # REGION=...  # noqa: ERA001
    # IMAGE_NAME="kmnlp/langfuse-worker"  # noqa: ERA001
    # ecr_url="${ACCOUNT_ID:?}.dkr.ecr.${REGION:?}.amazonaws.com"  # noqa: ERA001
    # ecr_tag="${ecr_url}/${IMAGE_NAME:?}"  # noqa: ERA001
    # aws ecr get-login-password |   docker login --username AWS --password-stdin "$ecr_tag"
    # -- tagged it as latest
    # docker tag langfuse/langfuse-worker:3.115.0 "$ecr_tag"
    # -- tagged it with the original version
    # docker tag "$ecr_tag" "$ecr_tag:3.115.0"
    # --- pushed both tags
    # docker push "$ecr_tag"
    # docker push "$ecr_tag:3.115.0"
    langfuse_worker_image: DataAwsEcrImage

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        ecs_cluster: EcsCluster,
    ) -> None:
        super().__init__(scope, id)

        # Create supporting services
        self.db_url_secret = DataAwsSecretsmanagerSecret(
            self, "db_url_secret", name="langfuse_db_url"
        )
        # The secret version is used to actually read the secret value.
        self.db_url_secret_version = DataAwsSecretsmanagerSecretVersion(
            self, "db_url_secret_version", secret_id=self.db_url_secret.id
        )
        self.postgres_db = PostgresDB(
            self,
            "postgres_db",
            network,
            name="langfuse",
            db_url_secret=self.db_url_secret_version,
        )
        self.redis = Redis(self, "redis", network, name="langfuse", eviction_policy="noeviction")
        self.clickhouse = ClickHouse(
            self, "clickhouse", config, network, ecs_cluster, name="langfuse"
        )
        # Create S3 bucket for media and event storage
        self.s3_bucket = S3Bucket(self, "langfuse_bucket", bucket="kmnlp-langfuse-storage")

        self.langfuse_web_image = DataAwsEcrImage(
            self,
            "langfuse_web_image",
            repository_name=config.langfuse.web.image.repository_name,
            image_tag=config.langfuse.web.image.tag,
            most_recent=config.langfuse.web.image.most_recent,
        )
        self.langfuse_worker_image = DataAwsEcrImage(
            self,
            "langfuse_worker_image",
            repository_name=config.langfuse.worker.image.repository_name,
            image_tag=config.langfuse.worker.image.tag,
            most_recent=config.langfuse.worker.image.most_recent,
        )


class _LangfuseLb(Construct):
    cert: DataAwsAcmCertificate
    alb: Lb
    security_group: SecurityGroup
    web_target_group: LbTargetGroup
    https_listener: LbListener
    http_redirect_listener: LbListener

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
    ) -> None:
        super().__init__(scope, id)

        # Get ACM certificate
        self.cert = DataAwsAcmCertificate(self, "langfuse_cert", domain=config.langfuse.web.domain)

        self.security_group = SecurityGroup(
            self,
            "langfuse_web_sg",
            name="langfuse-web-sg",
            description="Security group for Langfuse web service",
            vpc_id=network.vpc.id,
            ingress=ssm_param_to_https_security_group_ingress(
                "e84-kmnlp-internal-allowed-cidrs-dict"
            ),
            egress=[
                SecurityGroupEgress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags={"Name": "langfuse-web-sg"},
        )

        # Create ALB
        self.alb = Lb(
            self,
            "langfuse_alb",
            name="langfuse-alb",
            load_balancer_type="application",
            security_groups=[self.security_group.id],
            subnets=network.public_subnet_ids,
            enable_deletion_protection=False,
        )

        # Create target group for web service
        self.web_target_group = LbTargetGroup(
            self,
            "langfuse_web_tg",
            name="langfuse-web",
            port=_WEB_PORT,
            protocol="HTTP",
            vpc_id=network.vpc.id,
            target_type="ip",
            health_check=LbTargetGroupHealthCheck(
                enabled=True,
                healthy_threshold=2,
                interval=30,
                matcher="200",
                path="/api/public/health",
                port="traffic-port",
                protocol="HTTP",
                timeout=5,
                unhealthy_threshold=2,
            ),
        )

        self.https_listener = LbListener(
            self,
            "langfuse_https_listener",
            load_balancer_arn=self.alb.arn,
            port=443,
            protocol="HTTPS",
            ssl_policy="ELBSecurityPolicy-TLS-1-2-2017-01",
            certificate_arn=self.cert.arn,
            default_action=[
                LbListenerDefaultAction(
                    type="forward",
                    target_group_arn=self.web_target_group.arn,
                )
            ],
        )

        self.http_redirect_listener = LbListener(
            self,
            "langfuse_http_listener",
            load_balancer_arn=self.alb.arn,
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


class Langfuse(Construct):
    """Defines infrastructure for langfuse including ECS services for web and worker."""

    dependencies: _LangfuseDependencies
    lb: _LangfuseLb

    langfuse_secret: DataAwsSecretsmanagerSecret

    web_security_group: SecurityGroup
    worker_security_group: SecurityGroup
    task_role: IamRole
    allow_s3_policy: IamRolePolicy
    allow_bedrock_policy: IamRolePolicy
    allow_cloudwatch_put_metrics_policy: IamRolePolicy

    execution_role: IamRole
    task_exec_policy: IamRolePolicyAttachment
    create_logs_policy: IamRolePolicy
    read_secrets_policy: IamRolePolicy

    web_service: EcsService
    worker_service: EcsService

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        ecs_cluster: EcsCluster,
    ) -> None:
        super().__init__(scope, id)

        self.dependencies = _LangfuseDependencies(
            self, "dependencies", config, network, ecs_cluster
        )
        self.lb = _LangfuseLb(self, "lb", config, network)
        self.langfuse_secret = DataAwsSecretsmanagerSecret(self, "langfuse_secret", name="langfuse")

        self.web_security_group = SecurityGroup(
            self,
            "langfuse_web_sg",
            name="langfuse-web-task-sg",
            description="Security group for Langfuse web service",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=_WEB_PORT,
                    to_port=_WEB_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                    description="Internal access for Langfuse web",
                ),
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags={"Name": "langfuse-web-task-sg"},
        )

        self.worker_security_group = SecurityGroup(
            self,
            "langfuse_worker_sg",
            name="langfuse-worker-sg",
            description="Security group for Langfuse worker service",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=_WORKER_PORT,
                    to_port=_WORKER_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                    description="Internal access for Langfuse worker",
                ),
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                    description="All outbound traffic",
                ),
            ],
            tags={"Name": "langfuse-worker-sg"},
        )

        # Create IAM roles
        self.task_role = IamRole(
            self,
            "langfuse_task_role",
            name="langfuse-task-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        # S3 access policy for media and event storage
        self.allow_s3_policy = IamRolePolicy(
            self,
            "langfuse_s3_policy",
            name="LangfuseS3Access",
            role=self.task_role.id,
            policy=create_policy(
                {
                    "Effect": "Allow",
                    "Action": [
                        "s3:GetObject",
                        "s3:PutObject",
                        "s3:DeleteObject",
                        "s3:ListBucket",
                    ],
                    "Resource": [
                        self.dependencies.s3_bucket.arn,
                        f"{self.dependencies.s3_bucket.arn}/*",
                    ],
                },
            ),
        )

        self.allow_bedrock_policy = IamRolePolicy(
            self,
            "langfuse_bedrock_policy",
            name="LangfuseBedrockAccess",
            role=self.task_role.id,
            policy=create_policy(create_comprehensive_bedrock_statement()),
        )

        self.allow_cloudwatch_put_metrics_policy = IamRolePolicy(
            self,
            "langfuse_cloudwatch_put_metrics_policy",
            name="LangfuseCloudwatchPutMetrics",
            role=self.task_role.id,
            policy=create_policy(create_cloudwatch_put_metric_data_statements()),
        )

        self.execution_role = IamRole(
            self,
            "langfuse_execution_role",
            name="langfuse-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )
        self.task_exec_policy = IamRolePolicyAttachment(
            self,
            "langfuse_execution_role_policy",
            role=self.execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )
        self.read_secrets_policy = IamRolePolicy(
            self,
            "read_secrets_policy",
            name="ReadSecrets",
            role=self.execution_role.id,
            policy=create_policy(
                create_read_secret_statement(self.langfuse_secret.arn),
                create_read_secret_statement(self.dependencies.db_url_secret.arn),
                create_read_secret_statement(self.dependencies.clickhouse.secret_arn),
                create_read_secret_statement(self.dependencies.redis.auth_token_secret.arn),
            ),
        )

        self.create_logs_policy = IamRolePolicy(
            self,
            "langfuse_execution_role_logs_policy",
            name="CreateLogGroup",
            role=self.execution_role.id,
            policy=create_policy(
                {"Effect": "Allow", "Action": "logs:CreateLogGroup", "Resource": "*"}
            ),
        )

        # Create ECS services
        self.web_service = self._create_web_service(config, network, ecs_cluster)
        self.worker_service = self._create_worker_service(config, network, ecs_cluster)

    def _get_common_task_secrets(self) -> list[dict[str, str]]:
        clickhouse = self.dependencies.clickhouse
        return [
            {
                "name": "DATABASE_URL",
                "valueFrom": f"{self.dependencies.db_url_secret.arn}:DATABASE_URL::",
            },
            {
                "name": "CLICKHOUSE_USER",
                "valueFrom": f"{clickhouse.secret_arn}:username::",
            },
            {
                "name": "CLICKHOUSE_PASSWORD",
                "valueFrom": f"{clickhouse.secret_arn}:password::",
            },
            {
                "name": "SALT",
                "valueFrom": f"{self.langfuse_secret.arn}:salt::",
            },
            {
                "name": "ENCRYPTION_KEY",
                "valueFrom": f"{self.langfuse_secret.arn}:encryption_key::",
            },
            {
                "name": "NEXTAUTH_SECRET",
                "valueFrom": f"{self.langfuse_secret.arn}:next_auth_secret::",
            },
            {
                "name": "REDIS_AUTH",
                "valueFrom": f"{self.dependencies.redis.auth_token_secret.arn}:auth_token::",
            },
        ]

    def _get_common_environment_variables(self, config: Config) -> dict[str, str]:
        """Get common environment variables for both web and worker services."""
        return {
            # Redis configuration
            "REDIS_HOST": self.dependencies.redis.endpoint,
            "REDIS_PORT": str(self.dependencies.redis.port),
            "REDIS_TLS_ENABLED": "true",
            # ClickHouse configuration
            "CLICKHOUSE_URL": self.dependencies.clickhouse.http_endpoint,
            "CLICKHOUSE_MIGRATION_URL": self.dependencies.clickhouse.native_endpoint,
            "CLICKHOUSE_CLUSTER_ENABLED": "false",
            # S3 configuration for AWS (instead of MinIO)
            "LANGFUSE_USE_AZURE_BLOB": "false",
            "LANGFUSE_S3_EVENT_UPLOAD_BUCKET": self.dependencies.s3_bucket.bucket,
            "LANGFUSE_S3_MEDIA_UPLOAD_BUCKET": self.dependencies.s3_bucket.bucket,
            "LANGFUSE_S3_BATCH_EXPORT_BUCKET": self.dependencies.s3_bucket.bucket,
            "LANGFUSE_S3_EVENT_UPLOAD_REGION": config.aws_account.region,
            "LANGFUSE_S3_MEDIA_UPLOAD_REGION": config.aws_account.region,
            "LANGFUSE_S3_BATCH_EXPORT_REGION": config.aws_account.region,
            "LANGFUSE_S3_EVENT_UPLOAD_FORCE_PATH_STYLE": "false",
            "LANGFUSE_S3_MEDIA_UPLOAD_FORCE_PATH_STYLE": "false",
            "LANGFUSE_S3_BATCH_EXPORT_ENABLED": "true",
            "LANGFUSE_S3_EVENT_UPLOAD_PREFIX": "events/",
            "LANGFUSE_S3_MEDIA_UPLOAD_PREFIX": "media/",
            "LANGFUSE_S3_BATCH_EXPORT_PREFIX": "exports/",
            "LANGFUSE_S3_BATCH_EXPORT_FORCE_PATH_STYLE": "false",
            # Features
            "TELEMETRY_ENABLED": "false",
            "LANGFUSE_ENABLE_EXPERIMENTAL_FEATURES": "true",
            # CloudWatch metrics
            "ENABLE_AWS_CLOUDWATCH_METRIC_PUBLISHING": "true",
        }

    def _create_web_service(
        self, config: Config, network: Network, ecs_cluster: EcsCluster
    ) -> EcsService:
        """Create ECS service for Langfuse web interface."""
        # Environment variables specific to web service
        web_env_vars = self._get_common_environment_variables(config)
        web_env_vars["NEXTAUTH_URL"] = f"https://{config.langfuse.web.domain}"

        # Create task definition
        web_task_definition = EcsTaskDefinition(
            self,
            "langfuse_web_task",
            depends_on=[self.dependencies.clickhouse.password_secret.secret_version],
            family="langfuse-web",
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            cpu=str(1024 * config.langfuse.web.num_cpus),
            memory=str(config.langfuse.web.memory_mb),
            execution_role_arn=self.execution_role.arn,
            task_role_arn=self.task_role.arn,
            runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
            container_definitions=json.dumps(
                [
                    {
                        "name": "langfuse-web",
                        "image": self.dependencies.langfuse_web_image.image_uri,
                        "essential": True,
                        "portMappings": [{"containerPort": _WEB_PORT, "protocol": "tcp"}],
                        "environment": [{"name": k, "value": v} for k, v in web_env_vars.items()],
                        "secrets": self._get_common_task_secrets(),
                        "logConfiguration": {
                            "logDriver": "awslogs",
                            "options": {
                                "awslogs-group": "langfuse-web",
                                "awslogs-region": config.aws_account.region,
                                "awslogs-create-group": "true",
                                "awslogs-stream-prefix": "ecs",
                            },
                        },
                    }
                ]
            ),
        )

        # Create ECS service
        return EcsService(
            self,
            "langfuse_web_service",
            name="langfuse-web",
            cluster=ecs_cluster.id,
            task_definition=web_task_definition.arn,
            desired_count=1,
            launch_type="FARGATE",
            platform_version="LATEST",
            network_configuration=EcsServiceNetworkConfiguration(
                subnets=network.private_subnet_ids,
                security_groups=[self.web_security_group.id],
                assign_public_ip=False,
            ),
            load_balancer=[
                EcsServiceLoadBalancer(
                    target_group_arn=self.lb.web_target_group.arn,
                    container_name="langfuse-web",
                    container_port=_WEB_PORT,
                )
            ],
            wait_for_steady_state=True,
            timeouts={"create": "5m", "update": "5m"},
            depends_on=[self.lb.alb],
        )

    def _create_worker_service(
        self, config: Config, network: Network, ecs_cluster: EcsCluster
    ) -> EcsService:
        """Create ECS service for Langfuse background worker."""
        # Environment variables for worker service
        worker_env_vars = self._get_common_environment_variables(config)

        # Create task definition
        worker_task_definition = EcsTaskDefinition(
            self,
            "langfuse_worker_task",
            family="langfuse-worker",
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            cpu=str(1024 * config.langfuse.worker.num_cpus),
            memory=str(config.langfuse.worker.memory_mb),
            execution_role_arn=self.execution_role.arn,
            task_role_arn=self.task_role.arn,
            runtime_platform={"cpu_architecture": "ARM64", "operating_system_family": "LINUX"},
            container_definitions=json.dumps(
                [
                    {
                        "name": "langfuse-worker",
                        "image": self.dependencies.langfuse_worker_image.image_uri,
                        "essential": True,
                        "portMappings": [{"containerPort": _WORKER_PORT, "protocol": "tcp"}],
                        "environment": [
                            {"name": k, "value": v} for k, v in worker_env_vars.items()
                        ],
                        "secrets": self._get_common_task_secrets(),
                        "logConfiguration": {
                            "logDriver": "awslogs",
                            "options": {
                                "awslogs-group": ("langfuse-worker"),
                                "awslogs-region": config.aws_account.region,
                                "awslogs-create-group": "true",
                                "awslogs-stream-prefix": "ecs",
                            },
                        },
                    }
                ]
            ),
        )

        # Create ECS service
        return EcsService(
            self,
            "langfuse_worker_service",
            name="langfuse-worker",
            cluster=ecs_cluster.id,
            task_definition=worker_task_definition.arn,
            desired_count=1,
            launch_type="FARGATE",
            platform_version="LATEST",
            network_configuration=EcsServiceNetworkConfiguration(
                subnets=network.private_subnet_ids,
                security_groups=[self.worker_security_group.id],
                assign_public_ip=False,
            ),
            wait_for_steady_state=True,
            timeouts={"create": "5m", "update": "5m"},
        )
