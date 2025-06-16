import json
import os
from typing import Any

from cdktf_cdktf_provider_aws.data_aws_caller_identity import DataAwsCallerIdentity
from cdktf_cdktf_provider_aws.data_aws_subnets import (
    DataAwsSubnets,
    DataAwsSubnetsFilter,
)
from cdktf_cdktf_provider_aws.data_aws_vpc import DataAwsVpc, DataAwsVpcFilter
from constructs import Construct


class BuildError(Exception):
    """Represents a build error."""


def get_env_var(name: str, default: str | None = None) -> str:
    """Get environment variable by name with optional default value.

    Args:
        name: Environment variable name to look up
        default: Default value if env var is not found

    Returns:
        str: Value of environment variable

    Raises:
        Exception: If env var is not found and no default is provided
    """
    value = os.getenv(name) or default

    if value is None:
        raise BuildError(f"Env var {name} must be set")
    return value


CLAUDE_3_7_SONNET = "anthropic.claude-3-7-sonnet-20250219-v1:0"
CLAUDE_3_5_SONNET = "anthropic.claude-3-5-sonnet-20240620-v1:0"
CLAUDE_3_HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"

S3_DATA_BUCKET = "data-c6c22a2e42294c11b52ee7f0c792c071"

REGION = get_env_var("REGION")
STATE_BUCKET = get_env_var("STATE_BUCKET")
LOCK_TABLE = get_env_var("LOCK_TABLE")
DEPLOY_BUCKET = get_env_var("DEPLOY_BUCKET")

DASK_ADDRESS = get_env_var("DASK_ADDRESS")

CI_JOB_ROLE_ARN = get_env_var("CI_JOB_ROLE_ARN")

EXPECTED_VPC_NAME = "aws-controltower-VPC"


class Network(Construct):
    """Represents AWS network infrastructure configuration.

    This class provides access to VPC, subnet, and AWS account information by querying
    existing AWS resources. It's designed to work with pre-existing network infrastructure
    rather than creating new resources.
    """

    caller_identity: DataAwsCallerIdentity
    vpc: DataAwsVpc

    private_subnets: DataAwsSubnets
    public_subnets: DataAwsSubnets

    def __init__(self, scope: Construct, id: str) -> None:
        """Initialize the Network construct.

        Args:
            scope: The scope in which to define this construct
            id: The scoped construct ID
        """
        super().__init__(scope, id)

        self.caller_identity = DataAwsCallerIdentity(self, "caller_identity")

        self.vpc = DataAwsVpc(
            self,
            "demo-kmnlp-chainlit-vpc",
            filter=[DataAwsVpcFilter(name="tag:Name", values=[EXPECTED_VPC_NAME])],
        )

        self.private_subnets = DataAwsSubnets(
            self,
            "private_subnets",
            filter=[DataAwsSubnetsFilter(name="vpc-id", values=[self.vpc.id])],
            tags={"Network": "Private"},
        )
        self.public_subnets = DataAwsSubnets(
            self,
            "public_subnets",
            filter=[DataAwsSubnetsFilter(name="vpc-id", values=[self.vpc.id])],
            tags={"Network": "Public"},
        )

    @property
    def account_id(self) -> str:
        """Get the AWS account ID.

        Returns:
            str: The AWS account ID associated with the caller identity
        """
        return self.caller_identity.account_id

    @property
    def public_subnet_ids(self) -> list[str]:
        """Get the list of public subnet IDs.

        Returns:
            list[str]: List of public subnet IDs in the VPC
        """
        return self.public_subnets.ids

    @property
    def private_subnet_ids(self) -> list[str]:
        """Get the list of private subnet IDs.

        Returns:
            list[str]: List of private subnet IDs in the VPC
        """
        return self.private_subnets.ids


def create_policy(*statements: dict[str, Any]) -> str:
    """Return the dictionary representation of an AWS policy as a JSON string.

    Args:
        statements (dict[str, Any]): The dictionary representation of an AWS policy

    Returns:
        The JSON representation of the provided statements dictionary
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": statements,
        }
    )


def create_assume_role_policy_for_role(role_arn: str) -> str:
    """Return policy allowing the provided role to assume this role.

    Args:
        role_arn (str): The ARN of the role that should be allowed to assume this role

    Returns:
        The JSON representation of the access policy
    """
    return create_policy(
        {
            "Action": "sts:AssumeRole",
            "Principal": {"AWS": [role_arn]},
            "Effect": "Allow",
        }
    )


def create_assume_role_policy_for_aws_service(service_name: str) -> str:
    """Return policy allowing the provided service name to assume a role.

    Args:
        service_name (str): The name of the AWS service that will be assuming the role

    Returns:
        The JSON representation of an access policy allowing the service to assume the role
    """
    return create_policy(
        {
            "Action": "sts:AssumeRole",
            "Principal": {"Service": f"{service_name}.amazonaws.com"},
            "Effect": "Allow",
        }
    )


def create_invoke_model_statement(model: str) -> dict[str, Any]:
    """Return policy for invoking a given model resource.

    Args:
        model (str): The model we want to be allowed to invoke

    Returns:
        The JSON representation of an access policy invocation of the provided Bedrock model
    """
    return {
        "Action": "bedrock:InvokeModel",
        "Effect": "Allow",
        "Resource": [
            f"arn:aws:bedrock:*::foundation-model/{model}",
            f"arn:aws:bedrock:{REGION}:*:inference-profile/us.{model}",
        ],
    }


def create_list_s3_bucket_statement(bucket_name: str) -> dict[str, Any]:
    """Return policy for listing objects in an s3 bucket.

    Args: bucket_name (str): The bucket name that will be accessed

    Returns:
        The JSON representation of an access policy to list objects in the bucket
    """
    return {
        "Action": ["s3:ListBucket"],
        "Effect": "Allow",
        "Resource": [f"arn:aws:s3:::{bucket_name}"],
    }


def create_read_s3_bucket_statement(bucket_name: str) -> dict[str, Any]:
    """Return policy for getting all objects in an S3 bucket.

    Args: bucket_name (str): The bucket name that will be accessed

    Returns:
        The JSON representation of an access policy to read all objects in the bucket
    """
    return {
        "Action": ["s3:GetObject"],
        "Effect": "Allow",
        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
    }
