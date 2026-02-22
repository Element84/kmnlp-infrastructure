from pathlib import Path

from cdktf_cdktf_provider_aws.cloudfront_distribution import (
    CloudfrontDistribution,
    CloudfrontDistributionCustomErrorResponse,
    CloudfrontDistributionDefaultCacheBehavior,
    CloudfrontDistributionDefaultCacheBehaviorForwardedValues,
    CloudfrontDistributionDefaultCacheBehaviorForwardedValuesCookies,
    CloudfrontDistributionOrigin,
    CloudfrontDistributionOriginS3OriginConfig,
    CloudfrontDistributionRestrictions,
    CloudfrontDistributionRestrictionsGeoRestriction,
    CloudfrontDistributionViewerCertificate,
)
from cdktf_cdktf_provider_aws.cloudfront_origin_access_identity import (
    CloudfrontOriginAccessIdentity,
)
from cdktf_cdktf_provider_aws.data_aws_acm_certificate import DataAwsAcmCertificate
from cdktf_cdktf_provider_aws.s3_bucket import S3Bucket
from cdktf_cdktf_provider_aws.s3_bucket_policy import S3BucketPolicy
from cdktf_cdktf_provider_null.resource import Resource as NullResource
from constructs import Construct

from kmnlp_infra.config import Config
from kmnlp_infra.utils.policies import create_policy

_CURR_DIR = Path(__file__).parent
_SCRIPT_DIR = _CURR_DIR / ".." / "scripts"
_UPLOAD_FRONTEND_SCRIPT = _SCRIPT_DIR / "upload_frontend.sh"


class Frontend(Construct):
    """Defines the frontend UI website."""

    certificate: DataAwsAcmCertificate
    bucket: S3Bucket
    oai: CloudfrontOriginAccessIdentity
    bucket_policy: S3BucketPolicy
    deploy_frontend: NullResource
    distribution: CloudfrontDistribution
    invalidate_cloudfront_cache: NullResource

    def __init__(self, scope: Construct, id: str, config: Config) -> None:
        super().__init__(scope, id)
        self.certificate = DataAwsAcmCertificate(
            self,
            "certificate",
            domain=config.frontend.domain,
            most_recent=True,
            types=["AMAZON_ISSUED"],
        )

        self.bucket = S3Bucket(self, "bucket", bucket=config.frontend.website_bucket)

        self.oai = CloudfrontOriginAccessIdentity(self, "oai", comment="Frontend for KMNLP")

        self.bucket_policy = S3BucketPolicy(
            self,
            "bucket_policy",
            bucket=self.bucket.bucket,
            policy=create_policy(
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": self.oai.iam_arn},
                    "Action": "s3:GetObject",
                    "Resource": f"{self.bucket.arn}/*",
                }
            ),
        )

        self.deploy_frontend = NullResource(
            self,
            "deploy_frontend",
            depends_on=[self.bucket],
            triggers={
                "artifact": config.frontend.artifact.artifact_s3_uri,
                "api_domain": config.api.domain,
            },
            provisioners=[
                {
                    "type": "local-exec",
                    "command": (
                        f"bash {_UPLOAD_FRONTEND_SCRIPT} "
                        '"$ARTIFACT_S3_URI" "$WEBSITE_BUCKET" "$API_URL"'
                    ),
                    "environment": {
                        "ARTIFACT_S3_URI": config.frontend.artifact.artifact_s3_uri,
                        "WEBSITE_BUCKET": self.bucket.bucket,
                        "API_URL": f"https://{config.api.domain}",
                    },
                }
            ],
        )

        self.distribution = CloudfrontDistribution(
            self,
            "distribution",
            enabled=True,
            comment="KMNLP Frontend",
            aliases=[config.frontend.domain],
            default_root_object="index.html",
            origin=[
                CloudfrontDistributionOrigin(
                    domain_name=self.bucket.bucket_regional_domain_name,
                    origin_id="s3",
                    s3_origin_config=CloudfrontDistributionOriginS3OriginConfig(
                        origin_access_identity=self.oai.cloudfront_access_identity_path
                    ),
                )
            ],
            default_cache_behavior=CloudfrontDistributionDefaultCacheBehavior(
                target_origin_id="s3",
                viewer_protocol_policy="redirect-to-https",
                allowed_methods=["GET", "HEAD", "OPTIONS"],
                cached_methods=["GET", "HEAD"],
                compress=True,
                forwarded_values=CloudfrontDistributionDefaultCacheBehaviorForwardedValues(
                    query_string=False,
                    cookies=CloudfrontDistributionDefaultCacheBehaviorForwardedValuesCookies(
                        forward="none"
                    ),
                ),
                min_ttl=0,
                default_ttl=600,
                max_ttl=3600,
            ),
            custom_error_response=[
                CloudfrontDistributionCustomErrorResponse(
                    error_code=403,
                    response_code=200,
                    response_page_path="/index.html",
                    error_caching_min_ttl=0,
                ),
                CloudfrontDistributionCustomErrorResponse(
                    error_code=404,
                    response_code=200,
                    response_page_path="/index.html",
                    error_caching_min_ttl=0,
                ),
            ],
            viewer_certificate=CloudfrontDistributionViewerCertificate(
                acm_certificate_arn=self.certificate.arn,
                ssl_support_method="sni-only",
            ),
            restrictions=CloudfrontDistributionRestrictions(
                geo_restriction=CloudfrontDistributionRestrictionsGeoRestriction(
                    restriction_type="none"
                )
            ),
            depends_on=[self.deploy_frontend],
            tags={"Name": "kmnlp-frontend"},
        )

        self.invalidate_cloudfront_cache = NullResource(
            self,
            "invalidate_cloudfront_cache",
            depends_on=[self.distribution, self.deploy_frontend],
            triggers={
                "artifact": config.frontend.artifact.artifact_s3_uri,
                "deploy_id": self.deploy_frontend.id,
            },
            provisioners=[
                {
                    "type": "local-exec",
                    "command": (
                        "aws cloudfront create-invalidation --distribution-id "
                        f'"{self.distribution.id}"'
                        " --paths '/*'"
                    ),
                }
            ],
        )
