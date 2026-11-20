from cdktf_cdktf_provider_aws.cloudwatch_log_group import CloudwatchLogGroup
from constructs import Construct

# Groups created implicitly by the awslogs driver default to never expiring.
DEFAULT_LOG_RETENTION_DAYS = 30


def create_task_log_group(scope: Construct, id: str, *, name: str) -> CloudwatchLogGroup:
    """Manages retention for a log group the awslogs driver already created.

    The driver creates these on first task launch via `awslogs-create-group`, so the
    group is imported rather than created.
    """
    log_group = CloudwatchLogGroup(
        scope,
        id,
        name=name,
        retention_in_days=DEFAULT_LOG_RETENTION_DAYS,
    )
    log_group.import_from(name)
    return log_group
