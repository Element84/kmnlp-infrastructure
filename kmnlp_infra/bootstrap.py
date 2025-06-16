from typing import Any

from cdktf_cdktf_provider_aws.iam_role import IamRole, IamRoleInlinePolicy
from constructs import Construct

from kmnlp_infra.common import (
    CI_JOB_ROLE_ARN,
    CLAUDE_3_5_SONNET,
    CLAUDE_3_7_SONNET,
    CLAUDE_3_HAIKU,
    create_assume_role_policy_for_role,
    create_invoke_model_statement,
    create_policy,
)


class DeployRole(Construct):
    """Create the role that the GitLab deploy task can assume to execute the deploy."""

    git_lab_deploy_role: IamRole

    git_lab_integration_test_role: IamRole

    def __init__(self, scope: Construct, id: str) -> None:
        super().__init__(scope, id)

        self.git_lab_deploy_role = IamRole(
            self,
            "git-lab-deploy-role",
            name="git-lab-deploy-role",
            assume_role_policy=create_assume_role_policy_for_role(CI_JOB_ROLE_ARN),
            managed_policy_arns=[],
            inline_policy=[
                IamRoleInlinePolicy(
                    name="DeployEssentialsPolicy",
                    policy=create_policy(self.create_git_lab_deploy_role_extra_policies()),
                ),
            ],
        )

        self.git_lab_integration_test_role = IamRole(
            self,
            "git-lab-integration-test-role",
            name="git-lab-integration-test-role",
            assume_role_policy=create_assume_role_policy_for_role(CI_JOB_ROLE_ARN),
            managed_policy_arns=[],
            inline_policy=[
                IamRoleInlinePolicy(
                    name="UseBedrockSonnet35",
                    policy=create_policy(create_invoke_model_statement(CLAUDE_3_5_SONNET)),
                ),
                IamRoleInlinePolicy(
                    name="UseBedrockSonnet37",
                    policy=create_policy(create_invoke_model_statement(CLAUDE_3_7_SONNET)),
                ),
                IamRoleInlinePolicy(
                    name="UseBedrockHaiku",  # Needed for natural language to polygon calls
                    policy=create_policy(create_invoke_model_statement(CLAUDE_3_HAIKU)),
                ),
                IamRoleInlinePolicy(
                    name="IntegrationTestEssentialsPolicy",
                    policy=create_policy(
                        self.create_git_lab_integration_test_role_essentials_policy()
                    ),
                ),
            ],
        )

    def create_git_lab_deploy_role_extra_policies(self) -> dict[str, Any]:
        """Return the policy statements needed for the Git Lab Deploy Role to do the full deploy.

        Returns:
            The JSON representation of the access policy
        """
        return {
            "Action": [
                "acm:DescribeCertificate",
                "acm:GetCertificate",
                "acm:ListCertificates",
                "acm:ListTagsForCertificate",
                "dynamodb:DeleteItem",
                "dynamodb:GetItem",
                "dynamodb:ListTables",
                "dynamodb:PutItem",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateSecurityGroup",
                "ec2:CreateTags",
                "ec2:DeleteSecurityGroup",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeSubnets",
                "ec2:DescribeVpcAttribute",
                "ec2:DescribeVpcs",
                "ec2:RevokeSecurityGroupEgress",
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:DescribeImages",
                "ecr:DescribeRepositories",
                "ecr:GetAuthorizationToken",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
                "ecs:CreateCluster",
                "ecs:CreateService",
                "ecs:DeregisterTaskDefinition",
                "ecs:DescribeClusters",
                "ecs:DescribeServices",
                "ecs:DescribeTaskDefinition",
                "ecs:ListTaskDefinitions",
                "ecs:RegisterTaskDefinition",
                "ecs:UpdateService",
                "elasticloadbalancing:CreateListener",
                "elasticloadbalancing:CreateLoadBalancer",
                "elasticloadbalancing:CreateTargetGroup",
                "elasticloadbalancing:DeleteLoadBalancer",
                "elasticloadbalancing:DeleteTargetGroup",
                "elasticloadbalancing:DescribeListenerAttributes",
                "elasticloadbalancing:DescribeListeners",
                "elasticloadbalancing:DescribeLoadBalancerAttributes",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeTags",
                "elasticloadbalancing:DescribeTargetGroupAttributes",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:ModifyLoadBalancerAttributes",
                "elasticloadbalancing:ModifyTargetGroupAttributes",
                "iam:AttachRolePolicy",
                "iam:CreateRole",
                "iam:DeleteRolePolicy",
                "iam:GetRole",
                "iam:GetRolePolicy",
                "iam:ListAttachedRolePolicies",
                "iam:ListInstanceProfilesForRole",
                "iam:ListRolePolicies",
                "iam:PassRole",
                "iam:PutRolePolicy",
                "s3:GetObject",
                "s3:ListAllMyBuckets",
                "s3:ListBucket",
                "s3:PutObject",
                "ssm:GetParameter",
            ],
            "Effect": "Allow",
            "Resource": ["*"],
        }

    def create_git_lab_integration_test_role_essentials_policy(self) -> dict[str, Any]:
        """Return policy statements needed for integration tests, minus invoke model.

        Returns:
            The JSON representation of the access policy
        """
        return {
            "Action": [
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:GetAuthorizationToken",
                "ecr:GetDownloadUrlForLayer",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
                "s3:GetObject",
                "s3:ListAllMyBuckets",
                "s3:ListBucket",
            ],
            "Effect": "Allow",
            "Resource": ["*"],
        }
