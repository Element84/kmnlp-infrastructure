from cdktf import Fn, TerraformOutput
from cdktf_cdktf_provider_aws.data_aws_secretsmanager_secret_version import (
    DataAwsSecretsmanagerSecretVersion,
)
from cdktf_cdktf_provider_aws.db_subnet_group import DbSubnetGroup
from cdktf_cdktf_provider_aws.rds_cluster import RdsCluster
from cdktf_cdktf_provider_aws.rds_cluster_instance import RdsClusterInstance
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from constructs import Construct

from kmnlp_infra.network import Network

_DB_PORT = 5432


class PostgresDB(Construct):
    """Instantiates an aurora postgres database and related infrastructure."""

    sg: SecurityGroup
    db_subnet_group: DbSubnetGroup
    master_password: TerraformOutput
    database: RdsCluster
    db_instance: RdsClusterInstance

    def __init__(
        self,
        scope: Construct,
        id: str,
        network: Network,
        *,
        name: str,
        db_url_secret: DataAwsSecretsmanagerSecretVersion,
    ) -> None:
        super().__init__(scope, id)

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

        # How the master password works:
        #   * Fetch the secret.
        #   * Read the value under the key `DATABASE_PASSWORD`.
        #   * Use that value for the master_password_wo in the database.
        #
        # Settting `sensitive=True` in the TerraformOutput ensures that the
        # parsed value doesn't get written to the state or plan.
        #
        # Setting `master_password_wo` raher than `master_password` ensures
        # that the RdsCluster's state does not contain the password.
        #
        # If the password's value is changed, increment the
        # master_password_wo_version value so that Terraform knows to apply
        # changes.
        self.master_password = TerraformOutput(
            self,
            "master_password_output",
            value=Fn.lookup_nested(
                Fn.jsondecode(db_url_secret.secret_string), ['"DATABASE_PASSWORD"']
            ),
            sensitive=True,
        )

        # Aurora PostgreSQL cluster
        self.database = RdsCluster(
            self,
            "database",
            apply_immediately=True,
            cluster_identifier=f"{name}-db",
            engine="aurora-postgresql",
            master_username=name,
            master_password_wo=self.master_password.value,
            master_password_wo_version=1,
            database_name=name,
            db_subnet_group_name=self.db_subnet_group.name,
            vpc_security_group_ids=[self.sg.id],
            skip_final_snapshot=True,
            copy_tags_to_snapshot=True,
            port=_DB_PORT,
            tags={"Name": f"{name}-db"},
        )

        # Aurora cluster instances
        self.db_instance = RdsClusterInstance(
            self,
            "db_instance",
            apply_immediately=True,
            identifier=f"{name}-db",
            cluster_identifier=self.database.id,
            instance_class="db.t3.medium",
            engine=self.database.engine,
            engine_version=self.database.engine_version,
        )

    @property
    def database_name(self) -> str:
        """The name of the database."""
        return self.database.database_name

    @property
    def endpoint(self) -> str:
        """The database endpoint."""
        return self.db_instance.endpoint

    @property
    def port(self) -> int:
        """Db port."""
        return _DB_PORT
