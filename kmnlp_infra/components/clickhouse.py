import json

from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.ecs_service import (
    EcsService,
    EcsServiceNetworkConfiguration,
    EcsServiceServiceRegistries,
)
from cdktf_cdktf_provider_aws.ecs_task_definition import (
    EcsTaskDefinition,
    EcsTaskDefinitionVolume,
    EcsTaskDefinitionVolumeEfsVolumeConfiguration,
)
from cdktf_cdktf_provider_aws.efs_file_system import EfsFileSystem
from cdktf_cdktf_provider_aws.efs_mount_target import EfsMountTarget
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from cdktf_cdktf_provider_aws.service_discovery_private_dns_namespace import (
    ServiceDiscoveryPrivateDnsNamespace,
)
from cdktf_cdktf_provider_aws.service_discovery_service import (
    ServiceDiscoveryService,
    ServiceDiscoveryServiceDnsConfig,
    ServiceDiscoveryServiceDnsConfigDnsRecords,
)
from cdktf_cdktf_provider_random.password import Password
from constructs import Construct

from kmnlp_infra.components.secret import Secret
from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.policies import create_assume_role_policy_for_aws_service, create_policy

_CLICKHOUSE_PORT = 8123
_CLICKHOUSE_NATIVE_PORT = 9000
_NUM_CPUS = 2
_MEMORY_GB = 16


class ClickHouse(Construct):
    """Creates a ClickHouse deployment using ECS Fargate with persistent EFS storage."""

    db_name: str
    username: str

    password_value: Password
    password_secret: Secret

    efs_file_system: EfsFileSystem
    efs_mount_targets: list[EfsMountTarget]
    security_group: SecurityGroup
    efs_security_group: SecurityGroup

    task_role: IamRole
    task_execution_role: IamRole
    task_execution_role_policy_attachment: IamRolePolicyAttachment
    create_logs_policy: IamRolePolicy
    read_secrets_policy: IamRolePolicy

    task_definition: EcsTaskDefinition
    service_discovery_namespace: ServiceDiscoveryPrivateDnsNamespace
    service_discovery_service: ServiceDiscoveryService
    ecs_service: EcsService

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        ecs_cluster: EcsCluster,
        *,
        name: str,
    ) -> None:
        super().__init__(scope, id)
        self.db_name = name
        self.username = name

        self.password_value = Password(
            self,
            "password_value",
            length=16,
            special=False,
            upper=True,
            lower=True,
            numeric=True,
        )
        self.password_secret = Secret(
            self,
            "password_secret",
            name="clickhouse_password_new2",
            value={"username": name, "password": self.password_value.result},
        )

        # Create private DNS namespace for service discovery
        self.service_discovery_namespace = ServiceDiscoveryPrivateDnsNamespace(
            self,
            "service_discovery_namespace",
            name=f"{name}.local",
            vpc=network.vpc.id,
            description=f"Service discovery namespace for {name} ClickHouse",
            tags={"Name": f"{name}-clickhouse-namespace"},
        )

        # Create EFS file system for persistent storage
        self.efs_file_system = EfsFileSystem(
            self,
            "efs_file_system",
            creation_token=f"{name}-clickhouse-efs",
            tags={"Name": f"{name}-clickhouse-efs"},
            throughput_mode="elastic",
        )

        # Security group for EFS
        self.efs_security_group = SecurityGroup(
            self,
            "efs_security_group",
            name=f"{name}-clickhouse-efs-sg",
            description=f"Security group for {name} ClickHouse EFS",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=2049,
                    to_port=2049,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                )
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={"Name": f"{name}-clickhouse-efs-sg"},
        )

        # Create EFS mount targets in each private subnet
        self.efs_mount_targets = []
        for i, subnet_id in enumerate(network.private_subnet_ids):
            mount_target = EfsMountTarget(
                self,
                f"efs_mount_target_{i}",
                file_system_id=self.efs_file_system.id,
                subnet_id=subnet_id,
                security_groups=[self.efs_security_group.id],
            )
            self.efs_mount_targets.append(mount_target)

        # Security group for ClickHouse ECS service
        self.security_group = SecurityGroup(
            self,
            "security_group",
            name=f"{name}-clickhouse-sg",
            description=f"Security group for {name} ClickHouse",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=_CLICKHOUSE_PORT,
                    to_port=_CLICKHOUSE_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                ),
                SecurityGroupIngress(
                    from_port=_CLICKHOUSE_NATIVE_PORT,
                    to_port=_CLICKHOUSE_NATIVE_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                ),
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0,
                    to_port=0,
                    protocol="-1",
                    cidr_blocks=["0.0.0.0/0"],
                )
            ],
            tags={"Name": f"{name}-clickhouse-sg"},
        )

        # Create service discovery service
        self.service_discovery_service = ServiceDiscoveryService(
            self,
            "service_discovery_service",
            name="clickhouse",
            namespace_id=self.service_discovery_namespace.id,
            dns_config=ServiceDiscoveryServiceDnsConfig(
                namespace_id=self.service_discovery_namespace.id,
                dns_records=[
                    ServiceDiscoveryServiceDnsConfigDnsRecords(
                        ttl=60,
                        type="A",
                    ),
                    ServiceDiscoveryServiceDnsConfigDnsRecords(
                        ttl=60,
                        type="SRV",
                    ),
                ],
            ),
            tags={"Name": f"{name}-clickhouse-service"},
        )

        self.task_role = IamRole(
            self,
            "task_role",
            name=f"{name}-clickhouse-task-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        # Task execution role
        self.task_execution_role = IamRole(
            self,
            "task_execution_role",
            name=f"{name}-clickhouse-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        self.task_execution_role_policy_attachment = IamRolePolicyAttachment(
            self,
            "task_execution_role_policy_attachment",
            role=self.task_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )

        self.create_logs_policy = IamRolePolicy(
            self,
            "langfuse_execution_role_logs_policy",
            name="CreateLogGroup",
            role=self.task_execution_role.id,
            policy=create_policy(
                {"Effect": "Allow", "Action": "logs:CreateLogGroup", "Resource": "*"}
            ),
        )

        self.read_secrets_policy = IamRolePolicy(
            self,
            "read_secrets_policy",
            name="ReadSecrets",
            role=self.task_execution_role.id,
            policy=create_policy(
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": self.password_secret.arn,
                }
            ),
        )

        container_def = {
            "name": f"{name}-clickhouse",
            "image": "clickhouse/clickhouse-server:latest",
            "cpu": 0,
            "memory": _MEMORY_GB * 1024,
            "essential": True,
            "portMappings": [
                {
                    "containerPort": _CLICKHOUSE_PORT,
                    "hostPort": _CLICKHOUSE_PORT,
                    "protocol": "tcp",
                },
                {
                    "containerPort": _CLICKHOUSE_NATIVE_PORT,
                    "hostPort": _CLICKHOUSE_NATIVE_PORT,
                    "protocol": "tcp",
                },
            ],
            "mountPoints": [
                {
                    "sourceVolume": "clickhouse-data",
                    "containerPath": "/var/lib/clickhouse",
                    "readOnly": False,
                }
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": f"{name}-clickhouse",
                    "awslogs-region": config.aws_account.region,
                    "awslogs-create-group": "true",
                    "awslogs-stream-prefix": "clickhouse",
                },
            },
            "environment": [
                {"name": "CLICKHOUSE_DB", "value": self.db_name},
                {"name": "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT", "value": "1"},
            ],
            "secrets": [
                {
                    "name": "CLICKHOUSE_USER",
                    "valueFrom": f"{self.password_secret.arn}:username::",
                },
                {
                    "name": "CLICKHOUSE_PASSWORD",
                    "valueFrom": f"{self.password_secret.arn}:password::",
                },
            ],
            # Command override to fix EFS permissions and inject logger config for console logging
            "command": [
                "/bin/sh",
                "-c",
                (
                    "set -x\n"
                    # Ensure clickhouse can write to directory
                    "chown -R 101:101 /var/lib/clickhouse\n"
                    # Configure logging to stdout
                    "cat > /etc/clickhouse-server/config.d/logger.xml <<'EOF'\n"
                    "<clickhouse>\n"
                    "  <logger>\n"
                    "    <level>information</level>\n"
                    "    <console>true</console>\n"
                    "  </logger>\n"
                    "</clickhouse>\n"
                    "EOF\n"
                    # Use the regular startup command
                    "exec /entrypoint.sh"
                ),
            ],
        }

        # Task definition
        self.task_definition = EcsTaskDefinition(
            self,
            "task_definition",
            depends_on=[self.password_secret.secret_version],
            family=f"{name}-clickhouse-task",
            cpu=str(1024 * _NUM_CPUS),
            memory=str(1024 * _MEMORY_GB),
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            execution_role_arn=self.task_execution_role.arn,
            task_role_arn=self.task_role.arn,
            container_definitions=json.dumps([container_def]),
            volume=[
                EcsTaskDefinitionVolume(
                    name="clickhouse-data",
                    efs_volume_configuration=EcsTaskDefinitionVolumeEfsVolumeConfiguration(
                        file_system_id=self.efs_file_system.id,
                        root_directory="/",
                        transit_encryption="ENABLED",
                    ),
                )
            ],
        )

        # ECS Service
        self.ecs_service = EcsService(
            self,
            "ecs_service",
            name=f"{name}-clickhouse-service",
            cluster=ecs_cluster.id,
            task_definition=self.task_definition.arn,
            desired_count=1,
            # Add deployment configuration to force shutdown before startup
            # This is required because it seems like clickhouse can't have multiple instances
            # using the same directory at the same time. We use EFS for storage across containers
            # so this is required which means downtime for clickhouse.
            # This avoids this error:
            # DB::Exception: Cannot lock file /var/lib/clickhouse/status. Another server instance in
            # same directory is already running. (CANNOT_OPEN_FILE)
            deployment_maximum_percent=100,
            deployment_minimum_healthy_percent=0,
            launch_type="FARGATE",
            network_configuration=EcsServiceNetworkConfiguration(
                subnets=network.private_subnet_ids,
                security_groups=[self.security_group.id, self.efs_security_group.id],
                assign_public_ip=False,
            ),
            service_registries=EcsServiceServiceRegistries(
                registry_arn=self.service_discovery_service.arn,
                port=_CLICKHOUSE_PORT,
            ),
            # Add triggers to restart service when secret version changes
            triggers={
                "secret_version": self.password_secret.secret_version.version_id,
            },
            depends_on=[self.password_secret.secret_version, *self.efs_mount_targets],
            wait_for_steady_state=True,
            timeouts={"create": "5m", "update": "5m"},
            enable_execute_command=True,
        )

    @property
    def http_endpoint(self) -> str:
        """The HTTP endpoint for ClickHouse (port 8123) via service discovery."""
        return f"http://clickhouse.{self.service_discovery_namespace.name}:{_CLICKHOUSE_PORT}"

    @property
    def native_endpoint(self) -> str:
        """The native TCP endpoint for ClickHouse (port 9000) via service discovery."""
        return f"clickhouse://clickhouse.{self.service_discovery_namespace.name}:{_CLICKHOUSE_NATIVE_PORT}"

    @property
    def service_discovery_dns_name(self) -> str:
        """The service discovery DNS name for ClickHouse."""
        return f"clickhouse.{self.service_discovery_namespace.name}"

    @property
    def port(self) -> int:
        """HTTP port for ClickHouse."""
        return _CLICKHOUSE_PORT

    @property
    def native_port(self) -> int:
        """Native TCP port for ClickHouse."""
        return _CLICKHOUSE_NATIVE_PORT

    @property
    def secret_arn(self) -> str:
        """ARN to the secret holding username and password."""
        return self.password_secret.arn
