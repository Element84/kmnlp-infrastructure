import os

import boto3
import yaml
from cdktf_cdktf_provider_aws.security_group import SecurityGroupIngress


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


# Dask Ports
SCHEDULER_TCP_COMM_PORT = 8786
SCHEDULER_DASHBOARD_PORT = 8787

EKS_CLUSTER_NAME = "dask-cluster"


_ssm = boto3.client("ssm")  # type: ignore[reportUnknownMemberType]


def get_ssm_param(name: str) -> str:
    """Retrieves an ssm param value."""
    return _ssm.get_parameter(Name=name)["Parameter"]["Value"]


def ssm_param_to_https_security_group_ingress(param_name: str) -> list[SecurityGroupIngress]:
    """Creates 443/80 security group ingress rules based on CIDRs listed in an SSM param."""
    parsed_value: dict[str, str] = yaml.safe_load(get_ssm_param(param_name))

    def _create_ingress(cidr: str, port: int, desc: str) -> SecurityGroupIngress:
        return SecurityGroupIngress(
            cidr_blocks=[cidr],
            description=desc,
            from_port=port,
            to_port=port,
            protocol="tcp",
        )

    return [
        ingress
        for cidr_name, cidr in parsed_value.items()
        for ingress in [
            _create_ingress(cidr, 443, cidr_name),
            _create_ingress(cidr, 80, f"{cidr_name} for redirect"),
        ]
    ]
