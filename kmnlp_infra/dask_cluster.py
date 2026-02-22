from cdktf import Fn, LocalExecProvisioner
from cdktf_cdktf_provider_aws.data_aws_ecr_image import DataAwsEcrImage
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_kubernetes.manifest import Manifest
from cdktf_cdktf_provider_kubernetes.service_account import ServiceAccount, ServiceAccountMetadata
from cdktf_cdktf_provider_null.resource import Resource
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.common import (
    EKS_CLUSTER_NAME,
    SCHEDULER_DASHBOARD_PORT,
    SCHEDULER_TCP_COMM_PORT,
)
from kmnlp_infra.utils.policies import (
    create_oidc_access_statement,
    create_policy,
    managed_policy_arn_for_name,
)


# FUTURE: will workers start faster if they don't use fargate?
class DaskCluster(Construct):
    """Create the Demo Kubernetes Cluster."""

    api_image: DataAwsEcrImage
    default_pod_role: IamRole
    default_pod_role_policy_attachment: IamRolePolicyAttachment
    dask_service_account: ServiceAccount
    dask_cluster: Manifest
    dask_autoscaler: Manifest

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        *,
        network: Network,
        identity_issuer: str,
    ) -> None:
        super().__init__(scope, id)

        dask_service_account_name = "dask"

        # Get ECR Image
        self.api_image = DataAwsEcrImage(
            self,
            "image",
            repository_name=config.api.image.repository_name,
            image_tag=config.api.image.tag,
        )

        # Create IAM Role for default Kubernetes Service Account
        self.default_pod_role = IamRole(
            self,
            "dask-fargate-pod-role",
            name="dask-fargate-pod-role",
            assume_role_policy=create_policy(
                create_oidc_access_statement(
                    aws_account_id=network.account_id,
                    k8s_namespace="default",
                    service_account_name=dask_service_account_name,
                    oidc_provider=Fn.replace(
                        identity_issuer,
                        "https://",
                        "",
                    ),
                )
            ),
        )

        self.default_pod_role_policy_attachment = IamRolePolicyAttachment(
            self,
            "default-pod-role-policy-attachment",
            role=self.default_pod_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonS3ReadOnlyAccess"),
        )

        # Annotate default service account with IAM Role
        self.dask_service_account = ServiceAccount(
            self,
            "annotated-dask-service-account",
            metadata=ServiceAccountMetadata(
                name=dask_service_account_name,
                namespace="default",
                annotations={"eks.amazonaws.com/role-arn": self.default_pod_role.arn},
            ),
        )

        image_uri = self.api_image.image_uri

        # Add DaskCluster
        self.dask_cluster = Manifest(
            self,
            "dask_cluster",
            manifest={
                "apiVersion": "kubernetes.dask.org/v1",
                "kind": "DaskCluster",
                "metadata": {"name": "kmnlp-compute", "namespace": "default"},
                "spec": {
                    "worker": {
                        "replicas": 8,
                        "spec": {
                            "serviceAccountName": dask_service_account_name,
                            "containers": [
                                {
                                    "name": "worker",
                                    "image": image_uri,
                                    "imagePullPolicy": "Always",
                                    "args": [
                                        "dask-worker",
                                        "--name",
                                        "$(DASK_WORKER_NAME)",
                                        "--resources",
                                        "MEMORY=16e9",
                                        "--dashboard",
                                        "--dashboard-address",
                                        "8788",
                                    ],
                                    "ports": [
                                        {
                                            "name": "http-dashboard",
                                            "containerPort": 8788,
                                            "protocol": "TCP",
                                        }
                                    ],
                                    "resources": {
                                        "limits": {
                                            "cpu": "8",
                                            "memory": "16G",
                                        },
                                        "requests": {
                                            "cpu": "4",
                                            "memory": "8G",
                                        },
                                    },
                                },
                            ],
                        },
                    },
                    "scheduler": {
                        "spec": {
                            "serviceAccountName": dask_service_account_name,
                            "containers": [
                                {
                                    "name": "scheduler",
                                    "image": image_uri,
                                    "imagePullPolicy": "Always",
                                    "args": ["dask-scheduler"],
                                    "ports": [
                                        {
                                            "name": "tcp-comm",
                                            "containerPort": SCHEDULER_TCP_COMM_PORT,
                                            "protocol": "TCP",
                                        },
                                        {
                                            "name": "http-dashboard",
                                            "containerPort": SCHEDULER_DASHBOARD_PORT,
                                            "protocol": "TCP",
                                        },
                                    ],
                                    "readinessProbe": {
                                        "httpGet": {"port": "http-dashboard", "path": "/health"},
                                        "initialDelaySeconds": 5,
                                        "periodSeconds": 10,
                                    },
                                    "livenessProbe": {
                                        "httpGet": {"port": "http-dashboard", "path": "/health"},
                                        "initialDelaySeconds": 15,
                                        "periodSeconds": 20,
                                    },
                                    "resources": {
                                        "limits": {
                                            "cpu": "2",
                                            "memory": "4G",
                                        },
                                        "requests": {
                                            "cpu": "1",
                                            "memory": "2G",
                                        },
                                    },
                                }
                            ],
                        },
                        "service": {
                            "type": "LoadBalancer",
                            "selector": {
                                "dask.org/cluster-name": "kmnlp-compute",
                                "dask.org/component": "scheduler",
                            },
                            "ports": [
                                {
                                    "name": "tcp-comm",
                                    "protocol": "TCP",
                                    "port": SCHEDULER_TCP_COMM_PORT,
                                    "targetPort": "tcp-comm",
                                },
                                {
                                    "name": "http-dashboard",
                                    "protocol": "TCP",
                                    "port": SCHEDULER_DASHBOARD_PORT,
                                    "targetPort": "http-dashboard",
                                },
                            ],
                        },
                    },
                },
            },
        )

        # DaskAutoscaler manifest
        self.dask_autoscaler = Manifest(
            self,
            "dask_autoscaler",
            manifest={
                "apiVersion": "kubernetes.dask.org/v1",
                "kind": "DaskAutoscaler",
                "metadata": {"name": "kmnlp-compute", "namespace": "default"},
                "spec": {
                    "cluster": "kmnlp-compute",
                    "minimum": config.dask_cluster.min_workers,
                    "maximum": config.dask_cluster.max_workers,
                },
            },
            # Make sure the autoscaler is created after the cluster
            depends_on=[self.dask_cluster],
        )

        # Wait for pods and service to be ready.
        self.wait_resource = Resource(
            self,
            "wait-resource",
            provisioners=[
                LocalExecProvisioner(
                    type="local-exec",
                    command=f"""
                        aws eks update-kubeconfig \
                            --region {config.aws_account.region} \
                            --name {EKS_CLUSTER_NAME} && \
                        kubectl wait --for condition=Ready pods --all --timeout=600s && \
                        kubectl wait --for=jsonpath='{{.status.loadBalancer.ingress}}' \
                            svc/kmnlp-compute-scheduler --timeout=600s
                    """,
                )
            ],
        )
