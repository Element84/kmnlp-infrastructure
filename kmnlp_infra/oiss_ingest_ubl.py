import json
from typing import Any

from cdktf_cdktf_provider_aws.cloudwatch_log_group import CloudwatchLogGroup
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.ecr_lifecycle_policy import EcrLifecyclePolicy
from cdktf_cdktf_provider_aws.ecr_repository import EcrRepository
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.ecs_task_definition import EcsTaskDefinition
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_aws.security_group import SecurityGroup, SecurityGroupEgress
from cdktf_cdktf_provider_aws.sfn_state_machine import SfnStateMachine
from constructs import Construct

from kmnlp_infra.components.ecr import create_lifecycle_policy
from kmnlp_infra.config import Config, IngestUBLConfig
from kmnlp_infra.network import Network
from kmnlp_infra.utils.policies import (
    IAMStatement,
    create_assume_role_policy_for_aws_service,
    create_policy,
    create_read_all_buckets_statement,
)

_CONTAINER_NAME = "kmnlp-ingest"


def _create_ecs_runtask_statement(task_definition_arn: str) -> IAMStatement:
    return {
        "Effect": "Allow",
        "Action": "ecs:RunTask",
        "Resource": task_definition_arn,
    }


def _create_iam_passrole_statement(*role_arns: str) -> IAMStatement:
    return {
        "Effect": "Allow",
        "Action": "iam:PassRole",
        "Resource": list(role_arns),
    }


def _build_state_machine_definition(
    cluster_arn: str,
    task_definition_arn: str,
    subnet_ids: list[str],
    security_group_id: str,
) -> str:
    """Build the ASL definition for the UBL state machine.

    Fire-and-forget: starts the ECS Fargate task and immediately returns
    {"store_url": "", "status": "submitted"}.  The container runs independently
    and its result is not tracked by this state machine.
    """

    def _ecs_state(env_overrides: list[dict[str, str]], next_state: str) -> dict[str, Any]:
        return {
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
                        "AssignPublicIp": "DISABLED",
                    }
                },
                "Overrides": {
                    "ContainerOverrides": [
                        {
                            "Name": _CONTAINER_NAME,
                            "Environment": env_overrides,
                        }
                    ]
                },
            },
            "ResultPath": None,  # discard raw ECS RunTask output
            "Next": next_state,
        }

    glob_state = _ecs_state(
        [
            {"Name": "SOURCE_BUCKET", "Value.$": "$.source_bucket_name"},
            {"Name": "DESTINATION_BUCKET", "Value.$": "$.destination_bucket_name"},
            {"Name": "DESTINATION_PREFIX", "Value.$": "$.destination_prefix"},
            {"Name": "PREFIX_GLOB", "Value.$": "$.prefix_glob"},
        ],
        next_state="Return Submitted",
    )

    paths_state = _ecs_state(
        [
            {"Name": "SOURCE_BUCKET", "Value.$": "$.source_bucket_name"},
            {"Name": "DESTINATION_BUCKET", "Value.$": "$.destination_bucket_name"},
            {"Name": "DESTINATION_PREFIX", "Value.$": "$.destination_prefix"},
            {"Name": "SOURCE_PATHS", "Value.$": "$.source_paths"},
        ],
        next_state="Return Submitted",
    )

    thredds_state = _ecs_state(
        [
            {"Name": "DESTINATION_BUCKET", "Value.$": "$.destination_bucket_name"},
            {"Name": "DESTINATION_PREFIX", "Value.$": "$.destination_prefix"},
            {"Name": "THREDDS_CATALOG_XML_URL", "Value.$": "$.thredds_catalog_xml_url"},
        ],
        next_state="Return Submitted",
    )

    definition = {
        "Comment": "UBL: fire-and-forget ECS task to create icechunk store for a dataset",
        "StartAt": "Choose Source Mode",
        "States": {
            "Choose Source Mode": {
                "Type": "Choice",
                # OISS serializes unset optional inputs as "", not null/absent.
                # Build choice logic against all three forms of "not provided".
                "Choices": [
                    {
                        "And": [
                            {"Variable": "$.thredds_catalog_xml_url", "IsPresent": True},
                            {"Variable": "$.thredds_catalog_xml_url", "IsNull": False},
                            {"Not": {"Variable": "$.thredds_catalog_xml_url", "StringEquals": ""}},
                        ],
                        "Next": "Run ECS Task (Thredds)",
                    },
                    {
                        "And": [
                            {"Variable": "$.prefix_glob", "IsPresent": True},
                            {"Variable": "$.prefix_glob", "IsNull": False},
                            {"Not": {"Variable": "$.prefix_glob", "StringEquals": ""}},
                        ],
                        "Next": "Run ECS Task (Glob)",
                    },
                ],
                "Default": "Run ECS Task (Paths)",
            },
            "Run ECS Task (Thredds)": thredds_state,
            "Run ECS Task (Glob)": glob_state,
            "Run ECS Task (Paths)": paths_state,
            "Return Submitted": {
                "Type": "Pass",
                "Result": {"store_url": "", "status": "submitted"},
                "End": True,
            },
        },
    }

    return json.dumps(definition)


class OISSIngestUBL(Construct):
    """ECS task definition + Step Functions UBL for OISS-triggered icechunk store creation.

    The state machine is EXPRESS so that the OISS DIP workflow can invoke it via
    startSyncExecution.  It starts the ECS Fargate task (fire-and-forget) and immediately
    returns {"store_url": "", "status": "submitted"} — the container runs independently
    with no callback to Step Functions.

    The state machine ARN is the value to use as the Resource Identifier in the OISS
    process template (kmnlp_ingest.py KMNLP_INGEST_ARN).
    """

    ecr_repo: EcrRepository
    task_execution_role: IamRole
    task_role: IamRole
    task_def: EcsTaskDefinition
    security_group: SecurityGroup
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
        super().__init__(scope, id)

        ubl_config: IngestUBLConfig = config.ingest_ubl

        # --- ECR repository ---

        self.ecr_repo = EcrRepository(
            self,
            "ecr_repo",
            name=ubl_config.image.repository_name,
        )

        EcrLifecyclePolicy(
            self,
            "ecr_repo_lifecycle",
            repository=self.ecr_repo.name,
            policy=create_lifecycle_policy(),
        )

        # --- Container image ---

        self.image = DataAwsEcrImage(
            self,
            "image",
            repository_name=self.ecr_repo.name,
            image_tag=ubl_config.image.tag,
            most_recent=ubl_config.image.most_recent,
            depends_on=[self.ecr_repo],
        )

        # --- Log group (shared by task and state machine) ---

        self.log_group = CloudwatchLogGroup(
            self,
            "log_group",
            name="/kmnlp/oiss-ingest-ubl",
            retention_in_days=30,
        )

        # --- Task execution role (ECS control plane: pull image, write logs) ---

        self.task_execution_role = IamRole(
            self,
            "task_execution_role",
            name="kmnlp-ingest-ubl-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        IamRolePolicyAttachment(
            self,
            "task_execution_role_managed_policy",
            role=self.task_execution_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        )

        # --- Task role (container runtime: S3 access) ---

        self.task_role = IamRole(
            self,
            "task_role",
            name="kmnlp-ingest-ubl-task-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("ecs-tasks"),
        )

        IamRolePolicy(
            self,
            "task_role_s3_policy",
            name="S3Access",
            role=self.task_role.name,
            policy=create_policy(
                create_read_all_buckets_statement(),
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject", "s3:DeleteObject"],
                    "Resource": ["arn:aws:s3:::*/*"],
                },
            ),
        )

        # --- Security group (egress-only: S3 via VPC endpoint or NAT) ---

        self.security_group = SecurityGroup(
            self,
            "security_group",
            name="kmnlp-ingest-ubl-ecs",
            description="Security group for the OISS ingest UBL Fargate task",
            vpc_id=network.vpc.id,
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"],
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                )
            ],
            tags={"Name": "kmnlp-ingest-ubl-ecs"},
        )

        # --- ECS task definition ---

        container_def = {  # type: ignore[reportUnknownVariableType]
            "name": _CONTAINER_NAME,
            "image": self.image.image_uri,
            "essential": True,
            "environment": [
                {"name": "LOG_LEVEL", "value": ubl_config.log_level or "INFO"},
            ],  # "argument" env vars injected at runtime via containerOverrides
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
            family="kmnlp-ingest-ubl",
            cpu=str(ubl_config.num_cpus * 1024),
            memory=str(ubl_config.memory_mb),
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
            name="kmnlp-ingest-ubl-sfn-role",
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
                {
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
                },
            ),
        )

        # --- State machine ---

        definition = _build_state_machine_definition(
            cluster_arn=ecs_cluster.arn,
            task_definition_arn=self.task_def.arn,
            subnet_ids=network.private_subnet_ids,
            security_group_id=self.security_group.id,
        )

        self.state_machine = SfnStateMachine(
            self,
            "state_machine",
            name="kmnlp-ingest-ubl",
            type="EXPRESS",  # OISS DIP workflow calls startSyncExecution, which requires EXPRESS
            role_arn=self.state_machine_role.arn,
            definition=definition,
            logging_configuration={
                "level": "ALL",
                "include_execution_data": True,
                "log_destination": f"{self.log_group.arn}:*",
            },
        )
