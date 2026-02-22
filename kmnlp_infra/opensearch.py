from cdktf_cdktf_provider_aws.cloudwatch_log_group import CloudwatchLogGroup
from cdktf_cdktf_provider_aws.cloudwatch_log_resource_policy import CloudwatchLogResourcePolicy
from cdktf_cdktf_provider_aws.opensearch_domain import (
    OpensearchDomain,
    OpensearchDomainAdvancedSecurityOptions,
    OpensearchDomainAutoTuneOptions,
    OpensearchDomainClusterConfig,
    OpensearchDomainClusterConfigZoneAwarenessConfig,
    OpensearchDomainDomainEndpointOptions,
    OpensearchDomainEbsOptions,
    OpensearchDomainLogPublishingOptions,
    OpensearchDomainOffPeakWindowOptions,
    OpensearchDomainSoftwareUpdateOptions,
    OpensearchDomainVpcOptions,
)
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.policies import create_policy


class Opensearch(Construct):
    """An open search domain as an index for Natural Language Geocoding."""

    security_group: SecurityGroup

    log_group: CloudwatchLogGroup
    log_resource_policy: CloudwatchLogResourcePolicy

    opensearch_domain: OpensearchDomain

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        num_instances: int = 2,
    ) -> None:
        super().__init__(scope, id)

        self.security_group = SecurityGroup(
            self,
            "security_group",
            name="kmnlp-opensearch",
            description="Security group for the opensearch index",
            vpc_id=network.vpc.id,
            ingress=[
                SecurityGroupIngress(
                    cidr_blocks=[network.vpc.cidr_block],
                    description="Ingress from VPC",
                    from_port=443,
                    to_port=443,
                    protocol="tcp",
                )
            ],
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"], protocol="-1", from_port=0, to_port=0
                )
            ],
            tags={"Name": "kmnlp-opensearch"},
        )

        self.log_group = CloudwatchLogGroup(
            self, "log_group", name="kmnlp-opensearch", retention_in_days=365
        )
        self.log_resource_policy = CloudwatchLogResourcePolicy(
            self,
            "log_resource_policy",
            policy_name="AllowOpenSearch",
            policy_document=create_policy(
                {
                    "Effect": "Allow",
                    "Principal": {"Service": ["es.amazonaws.com"]},
                    "Action": ["logs:PutLogEvents", "logs:CreateLogStream"],
                    "Resource": (
                        f"arn:aws:logs:{config.aws_account.region}:{network.account_id}:"
                        f"log-group:{self.log_group.name}:*"
                    ),
                }
            ),
        )

        domain_name = "kmnlp-nlg"

        subnets = network.private_subnets[0:num_instances]
        num_azs = len({s.availability_zone for s in subnets})

        self.opensearch_domain = OpensearchDomain(
            self,
            "opensearch_domain",
            depends_on=[self.log_resource_policy],
            domain_name=domain_name,
            engine_version="OpenSearch_2.19",
            cluster_config=OpensearchDomainClusterConfig(
                dedicated_master_enabled=False,
                instance_type="r7g.large.search",
                instance_count=num_instances,
                zone_awareness_enabled=True,
                zone_awareness_config=OpensearchDomainClusterConfigZoneAwarenessConfig(
                    availability_zone_count=num_azs
                ),
            ),
            domain_endpoint_options=OpensearchDomainDomainEndpointOptions(enforce_https=False),
            access_policies=create_policy(
                {
                    "Effect": "Allow",
                    # Allows access from anywhere but the network access controls who can reach it
                    # The security group allows only VPC access.
                    "Principal": {"AWS": "*"},
                    "Action": "es:*",
                    "Resource": (
                        f"arn:aws:es:{config.aws_account.region}:{network.account_id}:domain/"
                        f"{domain_name}/*"
                    ),
                }
            ),
            advanced_options={
                # See https://docs.aws.amazon.com/opensearch-service/latest/APIReference/API_CreateDomain.html#API_CreateDomain_RequestBody
                "rest.action.multi.allow_explicit_index": "true",
                "indices.fielddata.cache.size": "20",
                "indices.query.bool.max_clause_count": "1024",
            },
            advanced_security_options=OpensearchDomainAdvancedSecurityOptions(
                enabled=False,
                anonymous_auth_enabled=True,
            ),
            auto_tune_options=OpensearchDomainAutoTuneOptions(
                desired_state="ENABLED",
                use_off_peak_window=True,
            ),
            off_peak_window_options=OpensearchDomainOffPeakWindowOptions(enabled=True),
            ebs_options=OpensearchDomainEbsOptions(
                ebs_enabled=True,
                volume_type="gp3",
                volume_size=100,
                iops=3000,
                throughput=125,
            ),
            ip_address_type="ipv4",
            log_publishing_options=[
                OpensearchDomainLogPublishingOptions(
                    log_type="SEARCH_SLOW_LOGS",
                    cloudwatch_log_group_arn=self.log_group.arn,
                )
            ],
            software_update_options=OpensearchDomainSoftwareUpdateOptions(
                auto_software_update_enabled=True
            ),
            vpc_options=OpensearchDomainVpcOptions(
                security_group_ids=[self.security_group.id], subnet_ids=[s.id for s in subnets]
            ),
        )

    @property
    def host(self) -> str:
        """Returns the hostname of the opensearch domain."""
        return self.opensearch_domain.endpoint
