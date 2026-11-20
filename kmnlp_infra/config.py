from pathlib import Path
from textwrap import dedent
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kmnlp_infra.utils.yaml_loader import load_yaml


class ImageRef(BaseModel, frozen=True):
    """Configuration defining an image in ECR.

    Docker image references are formatted like this:

    [registry_url/]namespace/repository[:tag]
    * registry_url is something like 1234567890.dkr.ecr.us-east-1.amazonaws.com
    * namespace is an organization name like "kmnlp"
    * repository identifies the specific image repository like "api"

    This image ref assumes the images are in the same AWS account.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    namespace: str
    repository: str
    tag: str | None = None
    most_recent: bool = False

    @model_validator(mode="after")
    def validate_tag_or_most_recent(self) -> Self:
        """Validates that the tag is set or most_recent is set to true."""
        if not self.tag and not self.most_recent:
            raise ValueError("Either 'tag' must be set or 'most_recent' must be True")
        if self.most_recent and self.tag:
            raise ValueError("'tag' can not be specified when 'most_recent' is True")

        return self

    @property
    def repository_name(self) -> str:
        """Returns the repository name, for example, kmnlp/api."""
        return f"{self.namespace}/{self.repository}"


class S3ArtifactRef(BaseModel, frozen=True):
    """Defines the location of an artifact for deployment on S3.."""

    model_config = ConfigDict(strict=True, extra="forbid")

    artifact_s3_uri: str


class EcsServiceConfig(BaseModel, frozen=True):
    """Configuration for an ECS Service."""

    model_config = ConfigDict(strict=True, extra="forbid")

    num_instances: int = 1
    image: ImageRef
    num_cpus: int
    memory_gb: int

    @property
    def memory_mb(self) -> int:
        """Returns the amount of configured memory in megabytes."""
        return self.memory_gb * 1024


class EcsPublicServiceConfig(EcsServiceConfig, frozen=True):
    """Configuration for an ECS Service."""

    domain: str


class IngestUBLConfig(BaseModel, frozen=True):
    """Configuration for the OISS ingest UBL (ECS task + Step Functions state machine)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    image: ImageRef
    num_cpus: int
    memory_gb: int
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None

    @property
    def memory_mb(self) -> int:
        """Returns the amount of configured memory in megabytes."""
        return self.memory_gb * 1024


class KnowledgeGraphUBLConfig(BaseModel, frozen=True):
    """Configuration for the Knowledge Graph UBL (ECS task + Step Functions state machine)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    num_cpus: int
    memory_gb: int
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] | None = "INFO"
    output_bucket: str
    output_prefix: str

    @property
    def memory_mb(self) -> int:
        """Returns the amount of configured memory in megabytes."""
        return self.memory_gb * 1024


class ResourceConfig(BaseModel, frozen=True):
    """Generic CPU/memory sizing for a container that doesn't need an image or domain."""

    model_config = ConfigDict(strict=True, extra="forbid")

    num_cpus: int
    memory_gb: int

    @property
    def memory_mb(self) -> int:
        """Returns the amount of configured memory in megabytes."""
        return self.memory_gb * 1024


class Langfuse(BaseModel, frozen=True):
    """Configures the frontend for deployment."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    web: EcsPublicServiceConfig
    worker: EcsServiceConfig
    clickhouse: ResourceConfig
    redis_node_type: str


class LiteLLM(BaseModel, frozen=True):
    """Config needed for LiteLLM."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    proxy_url: str


class TerraformConfig(BaseModel, frozen=True):
    """Configures terraform state storage."""

    model_config = ConfigDict(strict=True, extra="forbid")

    state_bucket: str
    deploy_bucket: str
    lock_table: str


class AwsAccountConfig(BaseModel, frozen=True):
    """Configures AWS account details."""

    model_config = ConfigDict(strict=True, extra="forbid")

    account_id: str
    region: str
    vpc_name: str = Field(
        description="Used to customize which VPC resources are deployed to.",
        default="aws-controltower-VPC",
    )
    elb_region_acount_id: str = Field(
        description=dedent(
            """
            The account ID from which the ELB logs will be sent to S3. This is not our account id
            but instead an AWS owned account id. Default value here is for us-east-1.
            See https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html#attach-bucket-policy
            """
        ),
        default="127311923021",
    )

    ci_job_role_arn: str = Field(
        description="The role of the build server that needs to be given access to deploy."
    )
    developers_role_arn: str = Field(
        description="The role given to developers accessing the account."
    )


class Frontend(BaseModel, frozen=True):
    """Configures the frontend for deployment."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    domain: str
    website_bucket: str
    artifact: S3ArtifactRef


class DaskPodResources(BaseModel, frozen=True):
    """CPU/memory requests and limits for a Dask pod container."""

    model_config = ConfigDict(strict=True, extra="forbid")

    cpu_request: int
    cpu_limit: int
    memory_request_gb: int
    memory_limit_gb: int


class DaskClusterConfig(BaseModel, frozen=True):
    """Configuration for the Dask cluster."""

    model_config = ConfigDict(strict=True, extra="forbid")

    min_workers: int
    max_workers: int
    worker: DaskPodResources
    scheduler: DaskPodResources


class OpensearchConfig(BaseModel, frozen=True):
    """Configuration for the Opensearch domain."""

    model_config = ConfigDict(strict=True, extra="forbid")

    instance_type: str
    num_instances: int
    engine_version: str
    volume_size_gb: int
    iops: int
    throughput: int


class EksNodeGroupConfig(BaseModel, frozen=True):
    """Configuration for the EKS system node group."""

    model_config = ConfigDict(strict=True, extra="forbid")

    instance_types: list[str]
    desired_size: int
    min_size: int
    max_size: int
    disk_size_gb: int


class DatabaseConfig(BaseModel, frozen=True):
    """Configuration shared by the Aurora Postgres instances."""

    model_config = ConfigDict(strict=True, extra="forbid")

    instance_class: str


class ApiEnvVars(BaseModel, frozen=True):
    """Application-specific environment variables for the API service."""

    model_config = ConfigDict(strict=True, extra="forbid")

    dask_compute_size_limit_gb: int
    dask_progress_poll_interval_sec: int
    orchestration_agent_max_consecutive_responses: int
    orchestration_agent_max_tokens: int
    algorithm_agent_max_tokens: int


class ApiConfig(EcsPublicServiceConfig, frozen=True):
    """Configuration for the API ECS service."""

    env_vars: ApiEnvVars


class Config(BaseModel, frozen=True):
    """Defines configuration that can be loaded from a YAML file.

    See the yaml_loader for specific supported !commands.
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    terraform: TerraformConfig
    aws_account: AwsAccountConfig
    artifacts_bucket: str = Field(
        description="Bucket containing build artifacts from other projects"
    )
    data_bucket: str
    user_data_bucket: str
    langfuse: Langfuse
    lite_llm: LiteLLM
    api: ApiConfig
    frontend: Frontend
    dask_cluster: DaskClusterConfig
    ingest_ubl: IngestUBLConfig
    opensearch: OpensearchConfig
    eks_node_group: EksNodeGroupConfig
    database: DatabaseConfig
    knowledge_graph_ubl: KnowledgeGraphUBLConfig

    @staticmethod
    def from_config_file(config_file: Path) -> "Config":
        """Loads the config from a specified yaml file."""
        return Config.model_validate(load_yaml(config_file))
