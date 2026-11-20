import json
from collections.abc import Iterable

from cdktf_cdktf_provider_aws.ecr_lifecycle_policy import EcrLifecyclePolicy
from constructs import Construct

# Grace period so a mistakenly re-tagged image can still be recovered.
_UNTAGGED_EXPIRY_DAYS = 7

# Deployed tags are pinned in configs/image_versions/, so this also bounds how far a
# rollback can reach.
_CI_IMAGES_KEPT = 30


def create_lifecycle_policy() -> str:
    """Returns the standard image retention policy for a repository.

    The second rule matches only `ci`-prefixed tags, so manually pushed images
    (`local-*`, version tags such as `3.115.0`) are never expired by image count.
    """
    return json.dumps(
        {
            "rules": [
                {
                    "rulePriority": 1,
                    "description": f"Expire untagged images after {_UNTAGGED_EXPIRY_DAYS} days",
                    "selection": {
                        "tagStatus": "untagged",
                        "countType": "sinceImagePushed",
                        "countUnit": "days",
                        "countNumber": _UNTAGGED_EXPIRY_DAYS,
                    },
                    "action": {"type": "expire"},
                },
                {
                    "rulePriority": 2,
                    "description": f"Keep only the newest {_CI_IMAGES_KEPT} CI images",
                    "selection": {
                        "tagStatus": "tagged",
                        "tagPrefixList": ["ci"],
                        "countType": "imageCountMoreThan",
                        "countNumber": _CI_IMAGES_KEPT,
                    },
                    "action": {"type": "expire"},
                },
            ]
        }
    )


class ImageRepositoryLifecycle(Construct):
    """Applies the standard retention policy to repositories created outside this project.

    The repositories themselves are created by the application repositories' CI, so only
    the policy is managed here. Repositories created by this project should attach
    `create_lifecycle_policy()` directly instead, to avoid two resources targeting one
    repository.
    """

    policies: list[EcrLifecyclePolicy]

    def __init__(self, scope: Construct, id: str, *, repository_names: Iterable[str]) -> None:
        super().__init__(scope, id)

        self.policies = [
            EcrLifecyclePolicy(
                self,
                f"lifecycle_{name.replace('/', '_').replace('-', '_')}",
                repository=name,
                policy=create_lifecycle_policy(),
            )
            for name in repository_names
        ]
