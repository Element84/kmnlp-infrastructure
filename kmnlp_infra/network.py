from typing import NotRequired, TypedDict

import boto3
from cdktf_cdktf_provider_aws.data_aws_caller_identity import DataAwsCallerIdentity
from cdktf_cdktf_provider_aws.data_aws_subnet import DataAwsSubnet
from cdktf_cdktf_provider_aws.data_aws_vpc import DataAwsVpc, DataAwsVpcFilter
from constructs import Construct
from mypy_boto3_ec2.type_defs import SubnetTypeDef, TagTypeDef

from kmnlp_infra.config import Config

_ec2 = boto3.client("ec2")  # pyright: ignore[reportUnknownMemberType]


class _TaggedItem(TypedDict):
    """Acts like a protocol for AWS typed dicts that have a Tags field."""

    Tags: NotRequired[list[TagTypeDef]]


def _get_tags_dict(item: _TaggedItem) -> dict[str, str]:
    return {tag["Key"]: tag["Value"] for tag in item.get("Tags", [])}


def _get_tag_name(item: _TaggedItem, default: str = "unknown") -> str:
    return _get_tags_dict(item).get("Name", default)


def _get_public_and_private_subnet_ids(config: Config) -> tuple[list[str], list[str]]:
    """Returns the public and private subnet ids as a tuple in alphabetical order.

    The order is important to ensure consistent subnet selection for placement of infrastructure.
    """
    vpcs = _ec2.describe_vpcs(
        Filters=[{"Name": "tag:Name", "Values": [config.aws_account.vpc_name]}]
    )["Vpcs"]

    if len(vpcs) != 1:
        raise RuntimeError(
            f"Found unexpected number [{len(vpcs)}] with name [{config.aws_account.vpc_name}]"
        )

    subnets = _ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpcs[0]["VpcId"]]}])[
        "Subnets"
    ]

    public_subnets: list[SubnetTypeDef] = []
    private_subnets: list[SubnetTypeDef] = []

    for subnet in subnets:
        if subnet.get("MapPublicIpOnLaunch", False):
            public_subnets.append(subnet)
        else:
            private_subnets.append(subnet)

    return (
        [s["SubnetId"] for s in sorted(public_subnets, key=_get_tag_name)],
        [s["SubnetId"] for s in sorted(private_subnets, key=_get_tag_name)],
    )


class Network(Construct):
    """Represents AWS network infrastructure configuration.

    This class provides access to VPC, subnet, and AWS account information by querying
    existing AWS resources. It's designed to work with pre-existing network infrastructure
    rather than creating new resources.
    """

    caller_identity: DataAwsCallerIdentity
    vpc: DataAwsVpc

    # Alphabetically ordered list of subnets by name
    private_subnets: list[DataAwsSubnet]

    # Alphabetically ordered list of subnets by name
    public_subnets: list[DataAwsSubnet]

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        """Initialize the Network construct."""
        super().__init__(scope, id)

        self.caller_identity = DataAwsCallerIdentity(self, "caller_identity")

        self.vpc = DataAwsVpc(
            self,
            "demo-kmnlp-chainlit-vpc",
            filter=[DataAwsVpcFilter(name="tag:Name", values=[config.aws_account.vpc_name])],
        )

        public_subnet_ids, private_subnet_ids = _get_public_and_private_subnet_ids(config)

        self.public_subnets = [
            DataAwsSubnet(self, f"public_subnet_{i}", id=subnet_id)
            for i, subnet_id in enumerate(public_subnet_ids)
        ]
        self.private_subnets = [
            DataAwsSubnet(self, f"private_subnet_{i}", id=subnet_id)
            for i, subnet_id in enumerate(private_subnet_ids)
        ]

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
        return [subnet.id for subnet in self.public_subnets]

    @property
    def private_subnet_ids(self) -> list[str]:
        """Get the list of private subnet IDs.

        Returns:
            list[str]: List of private subnet IDs in the VPC
        """
        return [subnet.id for subnet in self.private_subnets]
