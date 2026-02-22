import json

from cdktf_cdktf_provider_aws.secretsmanager_secret import SecretsmanagerSecret
from cdktf_cdktf_provider_aws.secretsmanager_secret_version import SecretsmanagerSecretVersion
from constructs import Construct


class Secret(Construct):
    """Creates a secret and secret version."""

    secret: SecretsmanagerSecret
    secret_version: SecretsmanagerSecretVersion

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        name: str,
        value: str | dict[str, str],
        description: str | None = None,
    ) -> None:
        super().__init__(scope, id)

        self.secret = SecretsmanagerSecret(
            self,
            "secret",
            description=description,
            name=name,
        )
        value_str = json.dumps(value) if isinstance(value, dict) else value
        self.secret_version = SecretsmanagerSecretVersion(
            self,
            "secret_version",
            secret_id=self.secret.id,
            secret_string=value_str,
        )

    @property
    def arn(self) -> str:
        """Returns the secret arn."""
        return self.secret.arn
