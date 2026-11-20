"""ECS task definition and Step Functions state machine for KMNLP knowledge graph UBL.

This module creates infrastructure to build and upload CRW, NOAA CDR, and dynamical.org
knowledge graphs to S3. It follows the OISS UBL pattern with fire-and-forget execution.

Reuses the ingest-ubl Docker image with a different entrypoint (entrypoint_kg.py).
"""

import json

from cdktf_cdktf_provider_aws.cloudwatch_log_group import CloudwatchLogGroup
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.ecs_task_definition import EcsTaskDefinition
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_aws.security_group import SecurityGroup, SecurityGroupEgress
from cdktf_cdktf_provider_aws.sfn_state_machine import SfnStateMachine
from constructs import Construct

from kmnlp_infra.config import Config, KnowledgeGraphUBLConfig
from kmnlp_infra.network import Network
from kmnlp_infra.utils.policies import (
    IAMStatement,
    create_assume_role_policy_for_aws_service,
    create_comprehensive_bedrock_statement,
    create_policy,
    create_read_all_buckets_statement,
)

_CONTAINER_NAME = "kmnlp-kg-ubl"


def _create_ecs_runtask_statement(task_definition_arn: str) -> IAMStatement:
    """Create IAM statement allowing ECS RunTask for the task definition."""
    return {
        "Effect": "Allow",
        "Action": "ecs:RunTask",
        "Resource": task_definition_arn,
    }


def _create_iam_passrole_statement(*role_arns: str) -> IAMStatement:
    """Create IAM statement allowing PassRole for the specified roles."""
    return {
        "Effect": "Allow",
        "Action": "iam:PassRole",
        "Resource": list(role_arns),
    }


def _create_cloudwatch_logs_statement() -> IAMStatement:
    """Create IAM statement for CloudWatch Logs operations."""
    return {
        "Effect": "Allow",
        "Action": [
            "logs:CreateLogDelivery",
            "logs:GetLogDelivery",
            "logs:UpdateLogDelivery",
            "logs:DeleteLogDelivery",
            "logs:ListLogDeliveries",
            "logs:PutLogEvents",
            "logs:PutResourcePolicy",
            "logs:DescribeResourcePolicies",
            "logs:DescribeLogGroups",
        ],
        "Resource": "*",
    }


def _build_state_machine_definition(
    cluster_arn: str,
    task_definition_arn: str,
    subnet_ids: list[str],
    security_group_id: str,
    output_bucket: str,
    output_prefix: str,
) -> str:
    """Build the ASL definition for the Knowledge Graph UBL state machine.

    Fire-and-forget: starts the ECS Fargate task and immediately returns
    {"status": "submitted", "output_bucket": "...", "output_prefix": "..."}.
    The container runs independently and writes TTL files to S3.

    Args:
        cluster_arn: ARN of the ECS cluster
        task_definition_arn: ARN of the ECS task definition
        subnet_ids: List of subnet IDs for the task
        security_group_id: Security group ID for the task
        output_bucket: S3 bucket for knowledge graph outputs
        output_prefix: S3 prefix/folder for outputs (must end with /)

    Returns:
        JSON string containing the ASL definition
    """
    definition = {
        "Comment": "Build and upload KMNLP knowledge graphs to S3",
        "StartAt": "Run ECS Task",
        "States": {
            "Run ECS Task": {
                "Type": "Task",
                "Resource": "arn:aws:states:::ecs:runTask",
                "Parameters": {
                    "Cluster": cluster_arn,
                    "TaskDefinition": task_definition_arn,
                    "LaunchType": "FARGATE",
                    "NetworkConfiguration": {
                        "AwsvpcConfiguration": {
                            "Subnets": subnet_ids,
                            "SecurityGroups": [security_group_id],
                            "AssignPublicIp": "ENABLED",  # Need internet for dynamical.org
                        }
                    },
                    "Overrides": {
                        "ContainerOverrides": [
                            {
                                "Name": _CONTAINER_NAME,
                                "Command": ["/app/.venv/bin/python", "entrypoint_kg.py"],
                                "Environment": [
                                    {"Name": "OUTPUT_BUCKET", "Value": output_bucket},
                                    {"Name": "OUTPUT_PREFIX", "Value": output_prefix},
                                ],
                            }
                        ]
                    },
                },
                "ResultPath": None,  # discard raw ECS RunTask output
                "Next": "Return Submitted",
            },
            "Return Submitted": {
                "Type": "Pass",
                "Result": {
                    "status": "submitted",
                    "output_bucket": output_bucket,
                    "output_prefix": output_prefix,
                },
                "End": True,
            },
        },
    }

    return json.dumps(definition)


class KnowledgeGraphUBL(Construct):
    """ECS task definition + Step Functions for KMNLP knowledge graph UBL.

    The state machine is EXPRESS so that OISS DIP workflow can invoke it via
    startSyncExecution. It starts the ECS Fargate task (fire-and-forget) and
    immediately returns {"status": "submitted", ...} — the container runs
    independently and writes TTL files to S3.

    Reuses the ingest-ubl Docker image with a Command override to specify
    entrypoint_kg.py instead of entrypoint.py.

    The state machine ARN is the value to use as the Resource Identifier in
    the OISS process template.

    Attributes:
        image: Data source for the container image (from ingest_ubl)
        log_group: CloudWatch log group for task logs
        task_execution_role: IAM role for ECS control plane operations
        task_role: IAM role for container runtime permissions
        security_group: Security group for the Fargate task
        task_def: ECS task definition
        state_machine_role: IAM role for Step Functions execution
        state_machine: Step Functions state machine
    """

    image: DataAwsEcrImage
    log_group: CloudwatchLogGroup
    task_execution_role: IamRole
    task_role: IamRole
    security_group: SecurityGroup
    task_def: EcsTaskDefinition
    state_machine_role: IamRole
    state_machine: SfnStateMachine

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        ecs_cluster: EcsCluster,
    ) -> None:
        """Initialize the Knowledge Graph UBL infrastructure.

        Args:
            scope: The CDK construct scope
            id: Unique identifier for this construct
            config: Application configuration
            network: Network infrastructure (VPC, subnets)
            ecs_cluster: ECS cluster to run tasks in
        """
        super().__init__(scope, id)

        kg_config: KnowledgeGraphUBLConfig = config.knowledge_graph_ubl

        # --- Container image (reuse ingest-ubl image) ---
        self.image = DataAwsEcrImage(
            self,
            "image",
            repository_name=config.ingest_ubl.image.repository_name,
            image_tag=config.ingest_ubl.image.tag,
            most_recent=config.ingest_ubl.image.most_recent,
        )

        # --- Log group ---
        self.log_group = CloudwatchLogGroup(
            self,
            "log_group",
            name="/kmnlp/knowledge-graph-ubl",
            retention_in_days=30,
        )

        # --- Task execution role ---
        self.task_execution_role = IamRole(
            self,
            "task_execution_role",
            name="kmnlp-kg-ubl-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        IamRolePolicyAttachment(
            self,
            "task_execution_role_managed_policy",
            role=self.task_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )

        # --- Task role ---
        self.task_role = IamRole(
            self,
            "task_role",
            name="kmnlp-kg-ubl-task-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        # S3 read/write permissions
        IamRolePolicy(
            self,
            "task_role_s3_policy",
            name="S3Access",
            role=self.task_role.name,
            policy=create_policy(
                create_read_all_buckets_statement(),
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject"],
                    "Resource": [f"arn:aws:s3:::{kg_config.output_bucket}/*"],
                },
            ),
        )

        # Bedrock permissions (for dynamical.org builder)
        IamRolePolicy(
            self,
            "task_role_bedrock_policy",
            name="UseBedrock",
            role=self.task_role.name,
            policy=create_policy(create_comprehensive_bedrock_statement()),
        )

        # --- Security group (needs internet access) ---
        self.security_group = SecurityGroup(
            self,
            "security_group",
            name="kmnlp-kg-ubl-ecs",
            description="Security group for the Knowledge Graph UBL Fargate task",
            vpc_id=network.vpc.id,
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"],
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                )
            ],
            tags={"Name": "kmnlp-kg-ubl-ecs"},
        )

        # --- ECS task definition ---
        container_def = {
            "name": _CONTAINER_NAME,
            "image": self.image.image_uri,
            "essential": True,
            "environment": [
                {"name": "LOG_LEVEL", "value": kg_config.log_level or "INFO"},
                # OUTPUT_BUCKET and OUTPUT_PREFIX passed via containerOverrides at runtime
            ],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": self.log_group.name,
                    "awslogs-region": config.aws_account.region,
                    "awslogs-stream-prefix": "ecs",
                },
            },
        }

        self.task_def = EcsTaskDefinition(
            self,
            "task_def",
            family="kmnlp-kg-ubl",
            cpu=str(kg_config.num_cpus * 1024),
            memory=str(kg_config.memory_mb),
            network_mode="awsvpc",
            requires_compatibilities=["FARGATE"],
            runtime_platform={
                "operating_system_family": "LINUX",
                "cpu_architecture": "X86_64",
            },
            execution_role_arn=self.task_execution_role.arn,
            task_role_arn=self.task_role.arn,
            container_definitions=json.dumps([container_def]),
        )

        # --- State machine role ---
        self.state_machine_role = IamRole(
            self,
            "state_machine_role",
            name="kmnlp-kg-ubl-sfn-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("states"),
        )

        IamRolePolicy(
            self,
            "state_machine_role_policy",
            name="RunEcsTask",
            role=self.state_machine_role.name,
            policy=create_policy(
                _create_ecs_runtask_statement(self.task_def.arn),
                _create_iam_passrole_statement(
                    self.task_role.arn,
                    self.task_execution_role.arn,
                ),
                _create_cloudwatch_logs_statement(),
            ),
        )

        # --- State machine ---
        definition = _build_state_machine_definition(
            cluster_arn=ecs_cluster.arn,
            task_definition_arn=self.task_def.arn,
            subnet_ids=network.public_subnet_ids,  # Need internet access
            security_group_id=self.security_group.id,
            output_bucket=kg_config.output_bucket,
            output_prefix=kg_config.output_prefix,
        )

        self.state_machine = SfnStateMachine(
            self,
            "state_machine",
            name="kmnlp-kg-ubl",
            type="EXPRESS",  # OISS DIP workflow requires EXPRESS
            role_arn=self.state_machine_role.arn,
            definition=definition,
            logging_configuration={
                "level": "ALL",
                "include_execution_data": True,
                "log_destination": f"{self.log_group.arn}:*",
            },
        )
