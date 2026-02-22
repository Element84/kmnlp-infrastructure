from cdktf_cdktf_provider_aws.data_aws_acm_certificate import DataAwsAcmCertificate
from cdktf_cdktf_provider_aws.lb import Lb, LbAccessLogs
from cdktf_cdktf_provider_aws.lb_listener import (
    LbListener,
    LbListenerDefaultAction,
    LbListenerDefaultActionRedirect,
)
from cdktf_cdktf_provider_aws.lb_target_group import (
    LbTargetGroup,
    LbTargetGroupHealthCheck,
)
from cdktf_cdktf_provider_aws.s3_bucket import S3Bucket
from cdktf_cdktf_provider_aws.s3_bucket_policy import S3BucketPolicy
from cdktf_cdktf_provider_aws.s3_bucket_server_side_encryption_configuration import (
    S3BucketServerSideEncryptionConfigurationA,
    S3BucketServerSideEncryptionConfigurationRuleA,
    S3BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultA,
)
from cdktf_cdktf_provider_aws.security_group import (
    SecurityGroup,
    SecurityGroupEgress,
)
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.network import Network
from kmnlp_infra.utils.common import ssm_param_to_https_security_group_ingress
from kmnlp_infra.utils.policies import (
    create_elb_put_s3_bucket_statement,
    create_policy,
)


class PublicAlb(Construct):
    """Creates a public ALB."""

    certificate: DataAwsAcmCertificate
    lb_sg: SecurityGroup
    load_balancer: Lb
    access_log_bucket: S3Bucket
    access_log_bucket_policy: S3BucketPolicy
    access_log_bucket_encryption: S3BucketServerSideEncryptionConfigurationA
    lb_access_logs: LbAccessLogs
    redirect_lb_listener: LbListener

    lb_target_group: LbTargetGroup
    lb_listener: LbListener

    def __init__(
        self,
        scope: Construct,
        id: str,
        config: Config,
        network: Network,
        *,
        name: str,
        domain: str,
        service_port: int,
        health_check_path: str = "/",
    ) -> None:
        super().__init__(scope, id=id)

        self.certificate = DataAwsAcmCertificate(
            self, "certificate", domain=domain, most_recent=True, types=["AMAZON_ISSUED"]
        )

        self.lb_sg = SecurityGroup(
            self,
            "lb_sg",
            name=name,
            description=f"{name} Load balancer security group",
            vpc_id=network.vpc.id,
            ingress=ssm_param_to_https_security_group_ingress(
                "e84-kmnlp-demo-chainlit-allowed-cidrs-dict"
            ),
            egress=[
                SecurityGroupEgress(
                    cidr_blocks=["0.0.0.0/0"], protocol="-1", from_port=0, to_port=0
                )
            ],
            tags={"Name": name},
        )

        self.access_log_bucket = S3Bucket(
            self, "access_log_bucket", bucket=f"{name}-alb-access-logs"
        )

        self.access_log_bucket_encryption = S3BucketServerSideEncryptionConfigurationA(
            self,
            "access_log_bucket_encryption",
            bucket=self.access_log_bucket.id,
            rule=[
                S3BucketServerSideEncryptionConfigurationRuleA(
                    apply_server_side_encryption_by_default=S3BucketServerSideEncryptionConfigurationRuleApplyServerSideEncryptionByDefaultA(
                        sse_algorithm="AES256"
                    )
                )
            ],
        )

        self.lb_access_logs = LbAccessLogs(
            bucket=self.access_log_bucket.bucket, enabled=True, prefix=None
        )

        self.access_log_bucket_policy = S3BucketPolicy(
            self,
            "access_log_bucket_policy",
            bucket=self.access_log_bucket.id,
            policy=create_policy(
                create_elb_put_s3_bucket_statement(
                    self.access_log_bucket.bucket, config.aws_account.elb_region_acount_id
                )
            ),
        )

        self.load_balancer = Lb(
            self,
            "load_balancer",
            name=name,
            security_groups=[self.lb_sg.id],
            subnets=network.public_subnet_ids,
            idle_timeout=300,
        )

        self.lb_target_group = LbTargetGroup(
            self,
            "lb_target_group",
            name=f"{name}-alb",
            port=service_port,
            protocol="HTTP",
            target_type="ip",
            vpc_id=network.vpc.id,
            health_check=LbTargetGroupHealthCheck(
                enabled=True,
                port="traffic-port",
                path=health_check_path,
            ),
        )

        self.lb_listener = LbListener(
            self,
            "lb_listener",
            load_balancer_arn=self.load_balancer.arn,
            certificate_arn=self.certificate.arn,
            port=443,
            protocol="HTTPS",
            ssl_policy="ELBSecurityPolicy-TLS13-1-0-2021-06",
            default_action=[
                LbListenerDefaultAction(
                    type="forward",
                    target_group_arn=self.lb_target_group.arn,
                )
            ],
        )

        self.redirect_lb_listener = LbListener(
            self,
            "redirect_lb_listener",
            load_balancer_arn=self.load_balancer.arn,
            port=80,
            protocol="HTTP",
            default_action=[
                LbListenerDefaultAction(
                    type="redirect",
                    redirect=LbListenerDefaultActionRedirect(
                        port="443", protocol="HTTPS", status_code="HTTP_301"
                    ),
                )
            ],
        )
