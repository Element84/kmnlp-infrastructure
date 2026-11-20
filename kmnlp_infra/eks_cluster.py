import json

from cdktf import Fn, LocalExecProvisioner
from cdktf_cdktf_provider_aws.data_aws_eks_addon_version import DataAwsEksAddonVersion
from cdktf_cdktf_provider_aws.eks_access_entry import EksAccessEntry
from cdktf_cdktf_provider_aws.eks_access_policy_association import (
    EksAccessPolicyAssociation,
    EksAccessPolicyAssociationAccessScope,
)
from cdktf_cdktf_provider_aws.eks_addon import EksAddon
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
from cdktf_cdktf_provider_null.resource import Resource
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

# EKS upgrades one minor at a time, so reaching a new target takes one apply per hop.
# Versions past standard support bill an extra $0.50/hr.
#
# WARNING: do not go past 1.37 without first confirming AWS has migrated EKS Fargate to
# cgroup v2 -- see the Fargate profile below. After each hop, recycle the Fargate pods
# (`kubectl delete pods --all -n default`); they never pick up a new kubelet in place.
_KUBERNETES_VERSION = "1.36"

# From v3.0.0 the chart version tracks the controller version (v2.x used chart 1.x).
_AWS_LB_CONTROLLER_VERSION = "3.5.0"


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
    addons: list[EksAddon]

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
            version=_KUBERNETES_VERSION,
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
            version=_KUBERNETES_VERSION,
            subnet_ids=network.private_subnet_ids,
            instance_types=config.eks_node_group.instance_types,
            capacity_type="ON_DEMAND",
            scaling_config=EksNodeGroupScalingConfig(
                desired_size=config.eks_node_group.desired_size,
                min_size=config.eks_node_group.min_size,
                max_size=config.eks_node_group.max_size,
            ),
            disk_size=config.eks_node_group.disk_size_gb,
            ami_type="BOTTLEROCKET_x86_64",
        )

        # Adopt the add-ons EKS installs by default so their versions track cluster upgrades.
        # The version data source is keyed to _KUBERNETES_VERSION, so bumping the cluster also
        # re-resolves each add-on to the default build for that version.
        self.addons = [
            EksAddon(
                self,
                f"addon-{addon_name}",
                cluster_name=self.cluster.name,
                addon_name=addon_name,
                addon_version=DataAwsEksAddonVersion(
                    self,
                    f"addon-version-{addon_name}",
                    addon_name=addon_name,
                    kubernetes_version=_KUBERNETES_VERSION,
                ).version,
                resolve_conflicts_on_create="OVERWRITE",
                resolve_conflicts_on_update="OVERWRITE",
                depends_on=[self.node_group],
            )
            for addon_name in ("vpc-cni", "kube-proxy", "coredns")
        ]

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
        #
        # WARNING: Fargate nodes still run cgroup v1 and log a CgroupV1 warning event on every
        # launch. Kubernetes 1.35 made the kubelet refuse to start on cgroup v1 by default; AWS
        # keeps Fargate working via the FailCgroupV1=false override. KEP-5573 removes the cgroup
        # v1 code entirely no earlier than 1.38, at which point that override disappears and every
        # pod matched by this profile stops starting unless AWS has moved Fargate to cgroup v2.
        # Re-check before upgrading past 1.37. Bottlerocket node groups are already cgroup v2.
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

        # Helm never upgrades CRDs on an existing release, so v3 requires applying them directly.
        self.aws_lb_controller_crds = Resource(
            self,
            "aws_load_balancer_controller_crds",
            triggers={"chart_version": _AWS_LB_CONTROLLER_VERSION},
            provisioners=[
                LocalExecProvisioner(
                    type="local-exec",
                    command=f"""
                        aws eks update-kubeconfig \
                            --region {config.aws_account.region} \
                            --name {EKS_CLUSTER_NAME} && \
                        kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/\
aws-load-balancer-controller/v{_AWS_LB_CONTROLLER_VERSION}/helm/aws-load-balancer-controller/\
crds/crds.yaml
                    """,
                )
            ],
            depends_on=[self.cluster],
        )

        # Add AWS Load Balancer Controller
        self.aws_lb_controller = Release(
            self,
            "aws_load_balancer_controller",
            name="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            chart="aws-load-balancer-controller",
            namespace="kube-system",
            version=_AWS_LB_CONTROLLER_VERSION,
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
            depends_on=[self.aws_lb_controller_crds],
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
            version="2026.3.0",  # API seems to change. Worth pinning. Declares kubeVersion >=1.30.
            replace=True,
            wait=True,
        )
