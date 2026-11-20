#!/usr/bin/env python

from pathlib import Path

from cdktf import App, DataTerraformRemoteStateS3, Fn, S3Backend, TerraformOutput, TerraformStack
from cdktf_cdktf_provider_aws.data_aws_eks_cluster import DataAwsEksCluster
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.provider import AwsProvider
from cdktf_cdktf_provider_aws.s3_bucket import S3Bucket
from cdktf_cdktf_provider_kubernetes.data_kubernetes_service import (
    DataKubernetesService,
    DataKubernetesServiceMetadata,
)
from cdktf_cdktf_provider_kubernetes.provider import KubernetesProvider, KubernetesProviderExec
from cdktf_cdktf_provider_null.provider import NullProvider
from cdktf_cdktf_provider_random.provider import RandomProvider
from constructs import Construct

from kmnlp_infra.api_service import Api
from kmnlp_infra.bootstrap import DeployRole
from kmnlp_infra.components.ecr import ImageRepositoryLifecycle
from kmnlp_infra.config import Config
from kmnlp_infra.dask_cluster import DaskCluster
from kmnlp_infra.eks_cluster import EksCluster
from kmnlp_infra.frontend import Frontend
from kmnlp_infra.knowledge_graph_ubl import KnowledgeGraphUBL
from kmnlp_infra.langfuse import Langfuse
from kmnlp_infra.network import Network
from kmnlp_infra.oiss_ingest_ubl import OISSIngestUBL
from kmnlp_infra.opensearch import Opensearch
from kmnlp_infra.utils.common import EKS_CLUSTER_NAME, SCHEDULER_TCP_COMM_PORT, get_env_var

_BOOTSTRAP_STATE_KEY = "bootstrap-tf-state"
_EKS_STATE_KEY = "eks-tf-state"
_DASK_CLUSTER_STATE_KEY = "dask-tf-state"
_MAIN_STACK_STATE_KEY = "kmnlp-tf-state"

_CONFIG_FIlE = get_env_var("CONFIG_FILE")


class BootstrapStack(TerraformStack):
    """A bootstrap stack used to create the conditions necessary for deploy.

    At this moment, it creates just the IAM role necessary for deploying.
    """

    deploy_role: DeployRole
    build_artifacts_bucket: S3Bucket

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)

        AwsProvider(
            self,
            "aws",
            region=config.aws_account.region,
        )
        self.build_artifacts_bucket = S3Bucket(
            self, "build_artifacts_bucket", bucket=config.artifacts_bucket
        )
        self.deploy_role = DeployRole(self, "deploy-role", config)

        TerraformOutput(self, "deploy_role_arn", value=self.deploy_role.git_lab_deploy_role.arn)


class DemoKmnlpEksClusterStack(TerraformStack):
    """Create EKS Cluster for us to deploy our Dask Cluster into using Kubernetes.

    As part of creating the EKS Cluster, install the Dask Helm Chart so that
    Kubernetes will understand terms like `DaskCluster` and `DaskAutoscaler`.

    NOTE: This does not deploy the Dask Cluster. Instead, it provisions the
    infrastructure we need to deploy Kubernetes infrastructure.
    """

    bootstrap_state: DataTerraformRemoteStateS3

    network: Network
    demo_kmnlp_eks_cluster: EksCluster

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)

        AwsProvider(
            self,
            "aws",
            region=config.aws_account.region,
        )
        NullProvider(self, "null-provider")

        self.bootstrap_state = DataTerraformRemoteStateS3(
            self,
            "bootstrap_state",
            bucket=config.terraform.state_bucket,
            key=_BOOTSTRAP_STATE_KEY,
        )
        deploy_role_arn = self.bootstrap_state.get("deploy_role_arn")

        self.network = Network(self, "kmnlp-network", config)

        # Create EKS Cluster
        self.demo_kmnlp_eks_cluster = EksCluster(
            self,
            "demo-kmnlp-eks-cluster",
            config,
            self.network,
            deploy_role_arn=deploy_role_arn.to_string(),
        )


class DaskClusterStack(TerraformStack):
    """Create the Dask clusterin k8s."""

    k8s_cluster: DaskCluster

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)
        AwsProvider(
            self,
            "aws",
            region=config.aws_account.region,
        )

        self.network = Network(self, "kmnlp-network", config)

        cluster = DataAwsEksCluster(self, id_="eks-cluster-data", name=EKS_CLUSTER_NAME)

        KubernetesProvider(
            self,
            "kubernetes",
            host=cluster.endpoint,
            cluster_ca_certificate=Fn.base64decode(cluster.certificate_authority.get(0).data),
            exec=[
                KubernetesProviderExec(
                    api_version="client.authentication.k8s.io/v1beta1",
                    command="aws",
                    args=["eks", "get-token", "--cluster-name", EKS_CLUSTER_NAME],
                )
            ],
        )

        NullProvider(self, "null-provider")

        self.k8s_cluster = DaskCluster(
            self,
            EKS_CLUSTER_NAME,
            config,
            identity_issuer=cluster.identity.get(0).oidc.get(0).issuer,
            network=self.network,
        )


class DemoKmnlpInfraStack(TerraformStack):
    """Create Demo stack to run Chainlit service for the KMNLP project."""

    network: Network
    opensearch: Opensearch
    langfuse: Langfuse
    ecs_cluster: EcsCluster
    api: Api
    frontend: Frontend
    oiss_ingest_ubl: OISSIngestUBL
    image_repository_lifecycle: ImageRepositoryLifecycle
    knowledge_graph_ubl: KnowledgeGraphUBL

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)

        AwsProvider(
            self,
            "aws",
            region=config.aws_account.region,
        )
        RandomProvider(self, "random")
        NullProvider(self, "null-provider")

        self.network = Network(self, "kmnlp-network", config)
        self.opensearch = Opensearch(self, "opensearch", config, self.network)

        # These repositories are created by the application repositories' CI, so only their
        # retention policies are managed here. kmnlp/dask is pushed to by CI but is not
        # referenced by any construct, so it is named explicitly.
        self.image_repository_lifecycle = ImageRepositoryLifecycle(
            self,
            "image-repository-lifecycle",
            repository_names=[
                config.api.image.repository_name,
                config.langfuse.web.image.repository_name,
                config.langfuse.worker.image.repository_name,
                "kmnlp/dask",
            ],
        )

        self.ecs_cluster = EcsCluster(
            self,
            "ecs-cluster",
            name="kmnlp-ecs-cluster",
            # "enabled" is standard Container Insights; "enhanced" adds per-container
            # metrics dimensioned by task ID, so every task launch creates new billable
            # metric series. Valid values are enhanced, enabled and disabled.
            setting=[{"name": "containerInsights", "value": "enabled"}],
        )
        self.langfuse = Langfuse(self, "langfuse", config, self.network, self.ecs_cluster)

        cluster = DataAwsEksCluster(self, id_="eks-cluster-data", name=EKS_CLUSTER_NAME)

        KubernetesProvider(
            self,
            "kubernetes",
            host=cluster.endpoint,
            cluster_ca_certificate=Fn.base64decode(cluster.certificate_authority.get(0).data),
            exec=[
                KubernetesProviderExec(
                    api_version="client.authentication.k8s.io/v1beta1",
                    command="aws",
                    args=["eks", "get-token", "--cluster-name", EKS_CLUSTER_NAME],
                )
            ],
        )

        # Fetch the service data
        dask_scheduler_service = DataKubernetesService(
            self,
            id_="dask-scheduler-service-data_",
            metadata=DataKubernetesServiceMetadata(name="kmnlp-compute-scheduler"),
        )
        dask_scheduler_service_hostname = (
            dask_scheduler_service.status.get(0).load_balancer.get(0).ingress.get(0).hostname
        )

        dask_scheduler_address = (
            f"tcp://{dask_scheduler_service_hostname}:{SCHEDULER_TCP_COMM_PORT}"
        )

        self.api = Api(
            self,
            "api",
            config,
            self.network,
            ecs_cluster=self.ecs_cluster,
            opensearch=self.opensearch,
            dask_scheduler_address=dask_scheduler_address,
        )
        self.frontend = Frontend(self, "frontend", config)

        self.oiss_ingest_ubl = OISSIngestUBL(
            self,
            "oiss-ingest-ubl",
            config,
            self.network,
            ecs_cluster=self.ecs_cluster,
        )
        TerraformOutput(
            self,
            "oiss_ingest_ubl_state_machine_arn",
            value=self.oiss_ingest_ubl.state_machine.arn,
            description=(
                "State machine ARN to use as the Resource Identifier in the OISS process template"
            ),
        )

        self.knowledge_graph_ubl = KnowledgeGraphUBL(
            self,
            "knowledge-graph-ubl",
            config,
            self.network,
            ecs_cluster=self.ecs_cluster,
        )
        TerraformOutput(
            self,
            "knowledge_graph_ubl_state_machine_arn",
            value=self.knowledge_graph_ubl.state_machine.arn,
            description=(
                "State machine ARN for KMNLP Knowledge Graph UBL (use in OISS process template)"
            ),
        )


config = Config.from_config_file(Path(_CONFIG_FIlE))

app = App()
bootstrap_stack = BootstrapStack(app, "bootstrap", config)
eks_stack = DemoKmnlpEksClusterStack(app, "eks_cluster", config)

dask_cluster_stack = DaskClusterStack(app, "dask_cluster", config)

stack = DemoKmnlpInfraStack(app, "kmnlp_infra", config)


S3Backend(
    bootstrap_stack,
    region=config.aws_account.region,
    bucket=config.terraform.state_bucket,
    key=_BOOTSTRAP_STATE_KEY,
    dynamodb_table=config.terraform.lock_table,
)

S3Backend(
    eks_stack,
    region=config.aws_account.region,
    bucket=config.terraform.state_bucket,
    key=_EKS_STATE_KEY,
    dynamodb_table=config.terraform.lock_table,
)

S3Backend(
    dask_cluster_stack,
    region=config.aws_account.region,
    bucket=config.terraform.state_bucket,
    key=_DASK_CLUSTER_STATE_KEY,
    dynamodb_table=config.terraform.lock_table,
)

S3Backend(
    stack,
    region=config.aws_account.region,
    bucket=config.terraform.state_bucket,
    key=_MAIN_STACK_STATE_KEY,
    dynamodb_table=config.terraform.lock_table,
)

app.synth()
