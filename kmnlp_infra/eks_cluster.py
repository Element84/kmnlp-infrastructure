import json

from cdktf import Fn
from cdktf_cdktf_provider_aws.eks_access_entry import EksAccessEntry
from cdktf_cdktf_provider_aws.eks_access_policy_association import (
    EksAccessPolicyAssociation,
    EksAccessPolicyAssociationAccessScope,
)
from cdktf_cdktf_provider_aws.eks_cluster import (
    EksCluster as CDKTFEksCluster,
)
from cdktf_cdktf_provider_aws.eks_cluster import (
    EksClusterAccessConfig,
    EksClusterVpcConfig,
)
from cdktf_cdktf_provider_aws.eks_fargate_profile import (
    EksFargateProfile,
    EksFargateProfileSelector,
)
from cdktf_cdktf_provider_aws.eks_node_group import EksNodeGroup, EksNodeGroupScalingConfig
from cdktf_cdktf_provider_aws.iam_openid_connect_provider import IamOpenidConnectProvider
from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from cdktf_cdktf_provider_aws.iam_role_policy_attachment import IamRolePolicyAttachment
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
    SecurityGroupIngress,
)
from cdktf_cdktf_provider_helm.provider import (
    HelmProvider,
    HelmProviderKubernetes,
    HelmProviderKubernetesExec,
)
from cdktf_cdktf_provider_helm.release import Release
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.common import EKS_CLUSTER_NAME
from kmnlp_infra.utils.policies import (
    cluster_access_policy_with_name,
    create_assume_role_policy_for_aws_service,
    create_assume_role_policy_for_role,
    create_load_balancer_controller_statements,
    create_oidc_access_statement,
    create_policy,
    managed_policy_arn_for_name,
)


class EksCluster(Construct):
    """Create the Demo Kubernetes Cluster."""

    cluster_role: IamRole
    pod_execution_role: IamRole
    pod_execution_role_fargate_policy_attachment: IamRolePolicyAttachment
    pod_execution_role_worker_policy_attachment: IamRolePolicyAttachment
    cluster_policy_attachment: IamRolePolicyAttachment
    service_policy_attachment: IamRolePolicyAttachment
    security_group: SecurityGroup
    cluster: CDKTFEksCluster
    fargate_profile: EksFargateProfile

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        deploy_role_arn: str,
    ) -> None:
        super().__init__(scope, id)

        self.cluster_role = IamRole(
            self,
            "eks-cluster-role",
            name="eks-cluster-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("eks"),
        )

        self.cluster_policy_attachment = IamRolePolicyAttachment(
            self,
            "eks-master-policy-attachment-cluster",
            role=self.cluster_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKSClusterPolicy"),
        )

        self.service_policy_attachment = IamRolePolicyAttachment(
            self,
            "eks-master-policy-attachment-service",
            role=self.cluster_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKSServicePolicy"),
        )

        self.security_group = SecurityGroup(
            self,
            "eks-security-group",
            vpc_id=network.vpc.id,
            ingress=[
                # Need to allow both TCP and UDP. Limited risk allowing all
                # ports because the cluster will use only private subnets.
                SecurityGroupIngress(
                    cidr_blocks=[network.vpc.cidr_block], from_port=0, to_port=0, protocol="-1"
                )
            ],
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"],
                    protocol="-1",
                    from_port=0,
                    to_port=0,
                )
            ],
        )

        self.node_role = IamRole(
            self,
            "k8s-node-role",
            name="k8s-node",
            assume_role_policy=create_assume_role_policy_for_aws_service("ec2"),
        )

        self.node_role_worker_attachment = IamRolePolicyAttachment(
            self,
            "node-policy-worker-attachment",
            role=self.node_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKSWorkerNodePolicy"),
        )

        self.node_role_cni_policy_attachment = IamRolePolicyAttachment(
            self,
            "node-worker-cni-policy-attachment",
            role=self.node_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKS_CNI_Policy"),
        )

        self.node_role_registry_policy_attachment = IamRolePolicyAttachment(
            self,
            "node-worker-registry-policy-attachment",
            role=self.node_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEC2ContainerRegistryReadOnly"),
        )

        # Create EKS Cluster. It will start with just k8s's Core DNS.
        self.cluster = CDKTFEksCluster(
            self,
            "eks-cluster",
            name=EKS_CLUSTER_NAME,
            role_arn=self.cluster_role.arn,
            vpc_config=EksClusterVpcConfig(
                subnet_ids=network.private_subnet_ids,
                security_group_ids=[self.security_group.id],
            ),
            access_config=EksClusterAccessConfig(
                authentication_mode="API", bootstrap_cluster_creator_admin_permissions=True
            ),
        )

        # Add direct access entry for the deploy role (used in CI)
        self.deploy_role_access_entry = EksAccessEntry(
            self,
            "eks-access-entry-ci-deploy-role",
            cluster_name=self.cluster.name,
            principal_arn=deploy_role_arn,
            type="STANDARD",
        )

        self.deploy_role_access_policy_association = EksAccessPolicyAssociation(
            self,
            "eks-access-policy-association-deploy-role",
            cluster_name=self.cluster.name,
            principal_arn=deploy_role_arn,
            policy_arn=cluster_access_policy_with_name("AmazonEKSClusterAdminPolicy"),
            access_scope=EksAccessPolicyAssociationAccessScope(type="cluster"),
            depends_on=[],
        )

        # Add access entry for SSO developers
        self.sso_developers_access_entry = EksAccessEntry(
            self,
            "eks-access-entry-sso-developers",
            cluster_name=self.cluster.name,
            principal_arn=config.aws_account.developers_role_arn,
            type="STANDARD",
        )

        self.sso_developers_access_policy_association = EksAccessPolicyAssociation(
            self,
            "eks-access-policy-association-sso-developers",
            cluster_name=self.cluster.name,
            principal_arn=config.aws_account.developers_role_arn,
            policy_arn=cluster_access_policy_with_name("AmazonEKSClusterAdminPolicy"),
            access_scope=EksAccessPolicyAssociationAccessScope(type="cluster"),
            depends_on=[],
        )

        # Create EKS Cluster Manager role for general cluster administration
        # Allow any user in the AWS account to assume this role for EKS management
        self.eks_cluster_manager_role = IamRole(
            self,
            "eks-cluster-manager-role",
            name="eks-cluster-manager-role",
            assume_role_policy=create_assume_role_policy_for_role(
                f"arn:aws:iam::{network.account_id}:root"
            ),
        )

        # Add access entry for EKS Cluster Manager role
        self.eks_cluster_manager_access_entry = EksAccessEntry(
            self,
            "eks-access-entry-cluster-manager",
            cluster_name=self.cluster.name,
            principal_arn=self.eks_cluster_manager_role.arn,
            type="STANDARD",
        )

        self.eks_cluster_manager_access_policy_association = EksAccessPolicyAssociation(
            self,
            "eks-access-policy-association-cluster-manager",
            cluster_name=self.cluster.name,
            principal_arn=self.eks_cluster_manager_role.arn,
            policy_arn=cluster_access_policy_with_name("AmazonEKSClusterAdminPolicy"),
            access_scope=EksAccessPolicyAssociationAccessScope(type="cluster"),
            depends_on=[],
        )

        # Node group to be used for all system workers. This node group will be
        # created immediately after creation and will immediately be availalbe
        # for scheduling. Once the node group becomes available, it will
        # receive the Core DNS workers from Kubernetes.
        self.node_group = EksNodeGroup(
            self,
            "system-node-group",
            cluster_name=self.cluster.name,
            node_group_name="system",
            node_role_arn=self.node_role.arn,
            subnet_ids=network.private_subnet_ids,
            instance_types=["t3.medium"],
            capacity_type="ON_DEMAND",
            scaling_config=EksNodeGroupScalingConfig(desired_size=2, min_size=1, max_size=4),
            disk_size=20,
            ami_type="BOTTLEROCKET_x86_64",
        )

        # Create an execution role for the Fargate containers.
        self.pod_execution_role = IamRole(
            self,
            "dask-fargate-execution-role",
            name="dask-fargate-execution-role",
            assume_role_policy=create_assume_role_policy_for_aws_service("eks-fargate-pods"),
        )

        self.pod_execution_role_fargate_policy_attachment = IamRolePolicyAttachment(
            self,
            "pod-execution-role-fargate-policy-attachment",
            role=self.pod_execution_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKSFargatePodExecutionRolePolicy"),
        )

        self.pod_execution_role_worker_policy_attachment = IamRolePolicyAttachment(
            self,
            "pod-execution-role-worker-policy-attachment",
            role=self.pod_execution_role.name,
            policy_arn=managed_policy_arn_for_name("AmazonEKSWorkerNodePolicy"),
        )

        # Create Fargate profile. Dask workers and schedulers will be scheduled here.
        self.fargate_profile = EksFargateProfile(
            self,
            "dask-fargate-profile",
            cluster_name=self.cluster.name,
            fargate_profile_name="dask-fargate-profile",
            pod_execution_role_arn=self.pod_execution_role.arn,
            subnet_ids=self.cluster.vpc_config.subnet_ids,
            selector=[
                EksFargateProfileSelector(namespace="default"),
                EksFargateProfileSelector(namespace="dask"),
            ],
        )

        # Create OIDC Provider: a bridge between Kubernetes service accounts
        # and AWS IAM roles.
        self.iam_openid_connect_provider = IamOpenidConnectProvider(
            self,
            "iam-openid-connect-provider",
            url=self.cluster.identity.get(0).oidc.get(0).issuer,
            client_id_list=["sts.amazonaws.com"],
        )

        # Create Helm Provider for the created EKS Cluster
        HelmProvider(
            self,
            "helm",
            kubernetes=HelmProviderKubernetes(
                host=self.cluster.endpoint,
                cluster_ca_certificate=Fn.base64decode(
                    self.cluster.certificate_authority.get(0).data
                ),
                exec=HelmProviderKubernetesExec(
                    api_version="client.authentication.k8s.io/v1beta1",
                    command="aws",
                    args=["eks", "get-token", "--cluster-name", EKS_CLUSTER_NAME],
                ),
            ),
        )

        # Create role for scheduler service's load balancer
        aws_lb_controller_service_account_name = "aws-load-balancer-controller"
        self.aws_lb_controller_role = IamRole(
            self,
            "aws-lb-controller-role",
            name="aws-lb-controller",
            assume_role_policy=create_policy(
                create_oidc_access_statement(
                    aws_account_id=network.account_id,
                    k8s_namespace="kube-system",
                    service_account_name=aws_lb_controller_service_account_name,
                    oidc_provider=Fn.replace(
                        self.cluster.identity.get(0).oidc.get(0).issuer,
                        "https://",
                        "",
                    ),
                )
            ),
            depends_on=[self.iam_openid_connect_provider],
        )

        self.aws_lb_controller_explicit_policy = IamRolePolicy(
            self,
            "aws-load-balancer-controller-explicit-policy",
            name="aws-load-balancer-controller-explicit-policy",
            policy=create_policy(*create_load_balancer_controller_statements()),
            role=self.aws_lb_controller_role.name,
        )

        self.aws_lb_controller_managed_policy = IamRolePolicyAttachment(
            self,
            "aws-load-balancer-controller-managed-policy",
            role=self.aws_lb_controller_role.name,
            policy_arn="arn:aws:iam::aws:policy/ElasticLoadBalancingFullAccess",
        )

        # Add AWS Load Balancer Controller
        self.aws_lb_controller = Release(
            self,
            "aws_load_balancer_controller",
            name="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            chart="aws-load-balancer-controller",
            namespace="kube-system",
            version="1.13.4",
            values=[
                json.dumps(
                    {
                        "clusterName": self.cluster.name,
                        "serviceAccount": {
                            "create": True,
                            "annotations": {
                                "eks.amazonaws.com/role-arn": self.aws_lb_controller_role.arn
                            },
                            "name": aws_lb_controller_service_account_name,
                        },
                        "region": config.aws_account.region,
                        "vpcId": network.vpc.id,
                    }
                )
            ],
            replace=True,
            wait=True,
        )

        # Add DaskCluster Operator to the EKSCluster
        self.dask_operator = Release(
            self,
            "dask-operator-release",
            cleanup_on_fail=True,
            repository="https://helm.dask.org",
            chart="dask-kubernetes-operator",
            create_namespace=True,
            namespace="dask-operator",
            name="dask-kubernetes-operator-prod",
            version="2025.7.0",  # API seems to change. Worth pinning.
            replace=True,
            wait=True,
        )
