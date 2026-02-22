from typing import Literal

from cdktf_cdktf_provider_aws.elasticache_parameter_group import (
    ElasticacheParameterGroup,
    ElasticacheParameterGroupParameter,
)
from cdktf_cdktf_provider_aws.elasticache_replication_group import ElasticacheReplicationGroup
from cdktf_cdktf_provider_aws.elasticache_subnet_group import ElasticacheSubnetGroup
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from cdktf_cdktf_provider_random.password import Password
from constructs import Construct

from kmnlp_infra.components.secret import Secret
from kmnlp_infra.network import Network

_PORT = 6379

EvictionPolicyValue = Literal[
    "allkeys-lru",
    "allkeys-lfu",
    "volatile-lru",
    "volatile-lfu",
    "volatile-ttl",
    "volatile-random",
    "allkeys-random",
    "noeviction",
]


class Redis(Construct):
    """Creates an elasticache cluster for redis with proper VPC integration."""

    sg: SecurityGroup
    subnet_group: ElasticacheSubnetGroup
    param_group: ElasticacheParameterGroup | None

    auth_token_value: Password
    auth_token_secret: Secret

    cluster: ElasticacheReplicationGroup

    def __init__(
        self,
        scope: Construct,
        id: str,
        network: Network,
        *,
        name: str,
        node_type: str = "cache.t4g.micro",
        eviction_policy: EvictionPolicyValue | None = None,
    ) -> None:
        super().__init__(scope, id)

        # Security group for Redis
        self.sg = SecurityGroup(
            self,
            "sg",
            name=f"{name}-redis",
            description=f"Security group for the {name} Redis cluster",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    from_port=_PORT,
                    to_port=_PORT,
                    protocol="tcp",
                    cidr_blocks=[network.vpc.cidr_block],
                )
            ],
            egress=[
                SecurityGroupEgress(
                    from_port=0, to_port=0, protocol="-1", cidr_blocks=["0.0.0.0/0"]
                )
            ],
            tags={"Name": f"{name}-redis"},
        )

        # ElastiCache subnet group
        self.subnet_group = ElasticacheSubnetGroup(
            self,
            "subnet_group",
            name=f"{name}-redis",
            subnet_ids=network.private_subnet_ids,
            tags={"Name": f"{name}-redis"},
        )

        if eviction_policy is not None:
            self.param_group = ElasticacheParameterGroup(
                self,
                "param_group",
                name=name,
                family="redis7",
                parameter=[
                    ElasticacheParameterGroupParameter(
                        name="maxmemory-policy", value=eviction_policy
                    )
                ],
            )

        self.auth_token_value = Password(
            self,
            "auth_token_value",
            length=16,
            special=False,
            upper=True,
            lower=True,
            numeric=True,
        )
        self.auth_token_secret = Secret(
            self,
            "auth_token_secret",
            name=f"{name}_redis_auth_token",
            description=f"Authentication token for the {name} redis cluster",
            value={"auth_token": self.auth_token_value.result},
        )

        # Redis cluster
        self.cluster = ElasticacheReplicationGroup(
            self,
            "cluster",
            replication_group_id=name.lower(),
            description=f"Redis cluster for {name}",
            node_type=node_type,
            num_cache_clusters=1,
            port=_PORT,
            subnet_group_name=self.subnet_group.name,
            security_group_ids=[self.sg.id],
            parameter_group_name=self.param_group.name if self.param_group else "default.redis7",
            engine_version="7.1",
            tags={"Name": f"{name}-redis"},
            transit_encryption_enabled=True,
            auth_token=self.auth_token_value.result,
            auth_token_update_strategy="SET",  # noqa: S106
        )

    @property
    def endpoint(self) -> str:
        """The Redis cluster endpoint."""
        return self.cluster.primary_endpoint_address

    @property
    def port(self) -> int:
        """Redis port."""
        return _PORT
