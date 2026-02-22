import json
from typing import Literal, NotRequired, TypedDict


class IAMPrincipalAWS(TypedDict):
    """Defines AWS principals in IAM statements."""

    AWS: str | list[str]


class IAMPrincipalService(TypedDict):
    """Defines service principals in IAM statements."""

    Service: str | list[str]


class IAMPrincipalFederated(TypedDict):
    """Defines federated principals in IAM statements."""

    Federated: str | list[str]


class IAMPrincipalCanonicalUser(TypedDict):
    """Defines canonical user principals in IAM statements."""

    CanonicalUser: str | list[str]


IAMPrincipal = (
    IAMPrincipalAWS
    | IAMPrincipalService
    | IAMPrincipalFederated
    | IAMPrincipalCanonicalUser
    | Literal["*"]
)


class IAMStatement(TypedDict):
    """Defines the structure of an IAM Statement."""

    Effect: Literal["Allow", "Deny"]
    Action: NotRequired[str | list[str]]
    Principal: NotRequired[IAMPrincipal]
    Resource: NotRequired[str | list[str]]
    Condition: NotRequired[dict[str, dict[str, str | list[str]]]]


def create_policy(*statements: IAMStatement) -> str:
    """Return the dictionary representation of an AWS policy as a JSON string.

    Args:
        statements (dict[str, Any]): The dictionary representation of an AWS policy

    Returns:
        The JSON representation of the provided statements dictionary
    """
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": statements,
        }
    )


def create_assume_role_policy_for_role(*role_arn: str) -> str:
    """Return policy allowing the provided role to assume this role.

    Args:
        role_arn (str): The ARN of the role that should be allowed to assume this role

    Returns:
        The JSON representation of the access policy
    """
    return create_policy(
        {
            "Action": "sts:AssumeRole",
            "Principal": {"AWS": list(role_arn)},
            "Effect": "Allow",
        }
    )


def create_assume_role_policy_for_aws_service(service_name: str) -> str:
    """Return policy allowing the provided service name to assume a role.

    Args:
        service_name (str): The name of the AWS service that will be assuming the role

    Returns:
        The JSON representation of an access policy allowing the service to assume the role
    """
    return create_policy(
        {
            "Action": "sts:AssumeRole",
            "Principal": {"Service": f"{service_name}.amazonaws.com"},
            "Effect": "Allow",
        }
    )


def create_comprehensive_bedrock_statement() -> IAMStatement:
    """Return comprehensive policy for all Bedrock model operations.

    Returns:
        The JSON representation of an access policy for comprehensive Bedrock access
    """
    return {
        "Action": [
            "bedrock:InvokeModel",
            "bedrock:InvokeModelWithResponseStream",
        ],
        "Effect": "Allow",
        "Resource": [
            "arn:aws:bedrock:*::foundation-model/*",
            "arn:aws:bedrock:*:*:inference-profile/*",
        ],
    }


def create_read_s3_bucket_statement(bucket_name: str) -> IAMStatement:
    """Return policy for getting all objects in an S3 bucket.

    Args: bucket_name (str): The bucket name that will be accessed

    Returns:
        The JSON representation of an access policy to read all objects in the bucket
    """
    return {
        "Action": ["s3:listBucket", "s3:GetObject"],
        "Effect": "Allow",
        "Resource": [f"arn:aws:s3:::{bucket_name}", f"arn:aws:s3:::{bucket_name}/*"],
    }


def create_read_all_buckets_statement() -> IAMStatement:
    """Return policy for getting all objects in an S3 bucket.

    Args: bucket_name (str): The bucket name that will be accessed

    Returns:
        The JSON representation of an access policy to read all objects in the bucket
    """
    return {
        "Action": ["s3:ListObject", "s3:GetObject"],
        "Effect": "Allow",
        "Resource": ["arn:aws:s3:::*", "arn:aws:s3:::*/*"],
    }


def create_put_s3_bucket_statement(bucket_name: str) -> IAMStatement:
    """Return policy for putting objects in an S3 bucket.

    This method differs from `create_elb_put_s3_bucket_statement` in that it is
    meant for use with resources inside the AWS account where the system is
    being deployed.

    Args:
        bucket_name (str): The bucket name to add the policy to.

    Returns:
        The JSON representation of an access policy to put objects in the bucket
    """
    return {
        "Action": ["s3:PutObject"],
        "Effect": "Allow",
        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
    }


def create_elb_put_s3_bucket_statement(
    bucket_name: str, elb_region_account_id: str
) -> IAMStatement:
    """Return policy for putting objects in an S3 bucket.

    This method differs from `create_put_s3_bucket_statement` in that it allows
    the underlying ELB (owned by AWS, configured for us to use) to write to S3.
    For us, this means the ELB can write its access logs to our S3 bucket.

    Args:
        bucket_name (str): The bucket name to add the policy to.

        elb_region_account_id (str): The account id for the principal that
            should be allowed to put to the bucket.

            n.b. This is not our AWS account, but rather the account
            associated with all ELBs in the AWS region.

    Returns:
        The JSON representation of an access policy to put objects in the bucket
    """
    return {
        "Action": ["s3:PutObject"],
        "Effect": "Allow",
        "Principal": {"AWS": f"arn:aws:iam::{elb_region_account_id}:root"},
        "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
    }


def create_read_secret_statement(secret_arn: str) -> IAMStatement:
    """Allows a secret value to be read."""
    return {
        "Action": "secretsmanager:GetSecretValue",
        "Effect": "Allow",
        "Resource": secret_arn,
    }


def managed_policy_arn_for_name(policy_name: str) -> str:
    """Return the arn for a given policy name."""
    return f"arn:aws:iam::aws:policy/{policy_name}"


def cluster_access_policy_with_name(policy_name: str) -> str:
    """Return the arn for a given policy name."""
    return f"arn:aws:eks::aws:cluster-access-policy/{policy_name}"


def create_oidc_access_statement(
    aws_account_id: str, k8s_namespace: str, service_account_name: str, oidc_provider: str
) -> IAMStatement:
    """Return access policy allowing the k8s service account to assume IAM role."""
    return {
        "Effect": "Allow",
        "Principal": {"Federated": f"arn:aws:iam::{aws_account_id}:oidc-provider/{oidc_provider}"},
        "Action": "sts:AssumeRoleWithWebIdentity",
        "Condition": {
            "StringEquals": {
                f"{oidc_provider}:sub": (
                    f"system:serviceaccount:{k8s_namespace}:{service_account_name}"
                ),
                f"{oidc_provider}:aud": "sts.amazonaws.com",
            }
        },
    }


def create_cloudwatch_put_metric_data_statements() -> IAMStatement:
    """Return acces policy statements allowing cloudwatch:PutMetricData."""
    return {
        "Action": ["cloudwatch:PutMetricData"],
        "Effect": "Allow",
        "Resource": ["*"],
    }


def create_load_balancer_controller_statements() -> list[IAMStatement]:
    """Return the recommended policy for AWS load balancer controller."""
    return [
        {
            "Effect": "Allow",
            "Action": ["iam:CreateServiceLinkedRole"],
            "Resource": "*",
            "Condition": {
                "StringEquals": {"iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"}
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:DescribeAccountAttributes",
                "ec2:DescribeAddresses",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeVpcs",
                "ec2:DescribeVpcPeeringConnections",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInstances",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeTags",
                "ec2:GetCoipPoolUsage",
                "ec2:DescribeCoipPools",
                "elasticloadbalancing:DescribeLoadBalancers",
                "elasticloadbalancing:DescribeLoadBalancerAttributes",
                "elasticloadbalancing:DescribeListeners",
                "elasticloadbalancing:DescribeListenerCertificates",
                "elasticloadbalancing:DescribeSSLPolicies",
                "elasticloadbalancing:DescribeRules",
                "elasticloadbalancing:DescribeTargetGroups",
                "elasticloadbalancing:DescribeTargetGroupAttributes",
                "elasticloadbalancing:DescribeTargetHealth",
                "elasticloadbalancing:DescribeTags",
                "elasticloadbalancing:DescribeTrustStores",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "cognito-idp:DescribeUserPoolClient",
                "acm:ListCertificates",
                "acm:DescribeCertificate",
                "iam:ListServerCertificates",
                "iam:GetServerCertificate",
                "waf-regional:GetWebACL",
                "waf-regional:GetWebACLForResource",
                "waf-regional:AssociateWebACL",
                "waf-regional:DisassociateWebACL",
                "wafv2:GetWebACL",
                "wafv2:GetWebACLForResource",
                "wafv2:AssociateWebACL",
                "wafv2:DisassociateWebACL",
                "shield:GetSubscriptionState",
                "shield:DescribeProtection",
                "shield:CreateProtection",
                "shield:DeleteProtection",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": ["ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress"],
            "Resource": "*",
        },
        {"Effect": "Allow", "Action": ["ec2:CreateSecurityGroup"], "Resource": "*"},
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateTags"],
            "Resource": "arn:aws:ec2:*:*:security-group/*",
            "Condition": {
                "StringEquals": {"ec2:CreateAction": "CreateSecurityGroup"},
                "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
            },
        },
        {
            "Effect": "Allow",
            "Action": ["ec2:CreateTags", "ec2:DeleteTags"],
            "Resource": "arn:aws:ec2:*:*:security-group/*",
            "Condition": {
                "Null": {
                    "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                    "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                }
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:DeleteSecurityGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:CreateLoadBalancer",
                "elasticloadbalancing:CreateTargetGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:CreateListener",
                "elasticloadbalancing:DeleteListener",
                "elasticloadbalancing:CreateRule",
                "elasticloadbalancing:DeleteRule",
            ],
            "Resource": "*",
        },
        {
            "Effect": "Allow",
            "Action": ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
            ],
            "Condition": {
                "Null": {
                    "aws:RequestTag/elbv2.k8s.aws/cluster": "true",
                    "aws:ResourceTag/elbv2.k8s.aws/cluster": "false",
                }
            },
        },
        {
            "Effect": "Allow",
            "Action": ["elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:listener/net/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener/app/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener-rule/net/*/*/*",
                "arn:aws:elasticloadbalancing:*:*:listener-rule/app/*/*/*",
            ],
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:ModifyLoadBalancerAttributes",
                "elasticloadbalancing:SetIpAddressType",
                "elasticloadbalancing:SetSecurityGroups",
                "elasticloadbalancing:SetSubnets",
                "elasticloadbalancing:DeleteLoadBalancer",
                "elasticloadbalancing:ModifyTargetGroup",
                "elasticloadbalancing:ModifyTargetGroupAttributes",
                "elasticloadbalancing:DeleteTargetGroup",
            ],
            "Resource": "*",
            "Condition": {"Null": {"aws:ResourceTag/elbv2.k8s.aws/cluster": "false"}},
        },
        {
            "Effect": "Allow",
            "Action": ["elasticloadbalancing:AddTags"],
            "Resource": [
                "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/net/*/*",
                "arn:aws:elasticloadbalancing:*:*:loadbalancer/app/*/*",
            ],
            "Condition": {
                "StringEquals": {
                    "elasticloadbalancing:CreateAction": ["CreateTargetGroup", "CreateLoadBalancer"]
                },
                "Null": {"aws:RequestTag/elbv2.k8s.aws/cluster": "false"},
            },
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:RegisterTargets",
                "elasticloadbalancing:DeregisterTargets",
            ],
            "Resource": "arn:aws:elasticloadbalancing:*:*:targetgroup/*/*",
        },
        {
            "Effect": "Allow",
            "Action": [
                "elasticloadbalancing:SetWebAcl",
                "elasticloadbalancing:ModifyListener",
                "elasticloadbalancing:AddListenerCertificates",
                "elasticloadbalancing:RemoveListenerCertificates",
                "elasticloadbalancing:ModifyRule",
            ],
            "Resource": "*",
        },
    ]
