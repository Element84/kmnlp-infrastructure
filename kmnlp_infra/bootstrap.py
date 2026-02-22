from cdktf_cdktf_provider_aws.iam_role import IamRole
from cdktf_cdktf_provider_aws.iam_role_policy import IamRolePolicy
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.utils.policies import (
    IAMStatement,
    create_assume_role_policy_for_role,
    create_comprehensive_bedrock_statement,
    create_policy,
)


class DeployRole(Construct):
    """Create the role that the GitLab deploy task can assume to execute the deploy."""

    git_lab_deploy_role: IamRole
    git_lab_deploy_role_policy: IamRolePolicy

    git_lab_integration_test_role: IamRole
    git_lab_integration_test_role_bedrock_policy: IamRolePolicy
    git_lab_integration_test_role_essentials_policy: IamRolePolicy

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)

        self.git_lab_deploy_role = IamRole(
            self,
            "git-lab-deploy-role",
            name="git-lab-deploy-role",
            assume_role_policy=create_assume_role_policy_for_role(
                config.aws_account.ci_job_role_arn,
                config.aws_account.developers_role_arn,
            ),
        )

        self.git_lab_deploy_role_policy = IamRolePolicy(
            self,
            "git-lab-deploy-role-policy",
            name="DeployEssentialsPolicy",
            policy=create_policy(self.create_git_lab_deploy_role_extra_policies()),
            role=self.git_lab_deploy_role.name,
        )

        self.git_lab_integration_test_role = IamRole(
            self,
            "git-lab-integration-test-role",
            name="git-lab-integration-test-role",
            assume_role_policy=create_assume_role_policy_for_role(
                config.aws_account.ci_job_role_arn
            ),
            max_session_duration=6 * 3600,  # 6 hours
        )

        self.git_lab_integration_test_role_bedrock_policy = IamRolePolicy(
            self,
            "git-lab-integration-test-role-bedrock-policy",
            name="UseBedrock",
            policy=create_policy(create_comprehensive_bedrock_statement()),
            role=self.git_lab_integration_test_role.name,
        )

        self.git_lab_integration_test_role_essentials_policy = IamRolePolicy(
            self,
            "git-lab-integration-test-role-essentials-policy",
            name="IntegrationTestEssentialsPolicy",
            policy=create_policy(self.create_git_lab_integration_test_role_essentials_policy()),
            role=self.git_lab_integration_test_role.name,
        )

    def create_git_lab_deploy_role_extra_policies(self) -> IAMStatement:
        """Return the policy statements needed for the Git Lab Deploy Role to do the full deploy.

        Returns:
            The JSON representation of the access policy
        """
        return {
            "Action": [
                "acm:Describe*",
                "acm:Get*",
                "acm:List*",
                "cloudfront:Get*",
                "cloudfront:List*",
                "cloudfront:CreateInvalidation",
                "dynamodb:DeleteItem",
                "dynamodb:Get*",
                "dynamodb:List*",
                "dynamodb:PutItem",
                "ec2:AuthorizeSecurityGroupEgress",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:CreateSecurityGroup",
                "ec2:CreateTags",
                "ec2:DeleteSecurityGroup",
                "ec2:Describe*",
                "ec2:RevokeSecurityGroupEgress",
                "ec2:RevokeSecurityGroupIngress",
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:Describe*",
                "ecr:Get*",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
                "ecs:*",
                "eks:*",
                "elasticloadbalancing:*",
                "es:*",
                "elasticache:Describe*",
                "elasticache:List*",
                "elasticfilesystem:Describe*",
                "iam:AttachRolePolicy",
                "iam:CreateOpenIDConnectProvider",
                "iam:CreateRole",
                "iam:DeleteOpenIDConnectProvider",
                "iam:DeleteRolePolicy",
                "iam:Get*",
                "iam:List*",
                "iam:PassRole",
                "iam:PutRolePolicy",
                "iam:TagOpenIDConnectProvider",
                "iam:UpdateOpenIDConnectProviderThumbprint",
                "logs:*",
                "rds:Describe*",
                "rds:List*",
                "s3:*",
                "secretsmanager:Describe*",
                "secretsmanager:Get*",
                "servicediscovery:Get*",
                "servicediscovery:List*",
                "ssm:Get*",
            ],
            "Effect": "Allow",
            "Resource": ["*"],
        }

    def create_git_lab_integration_test_role_essentials_policy(self) -> IAMStatement:
        """Return policy statements needed for integration tests, minus invoke model.

        Returns:
            The JSON representation of the access policy
        """
        return {
            "Action": [
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:CompleteLayerUpload",
                "ecr:CreateRepository",
                "ecr:Describe*",
                "ecr:GetAuthorizationToken",
                "ecr:GetDownloadUrlForLayer",
                "ecr:InitiateLayerUpload",
                "ecr:PutImage",
                "ecr:UploadLayerPart",
                "s3:GetObject",
                "s3:ListAllMyBuckets",
                "s3:ListBucket",
                "s3:PutObject",
            ],
            "Effect": "Allow",
            "Resource": ["*"],
        }
