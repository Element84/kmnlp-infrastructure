#!/usr/bin/env python
from cdktf import App, S3Backend, TerraformStack
from cdktf_cdktf_provider_aws.ecs_cluster import EcsCluster
from cdktf_cdktf_provider_aws.provider import AwsProvider
from constructs import Construct

from kmnlp_infra.bootstrap import DeployRole
from kmnlp_infra.chainlit_ecs import (
    Alb,
    DemoKmnlpChainlitEcsService,
)
from kmnlp_infra.common import (
    LOCK_TABLE,
    REGION,
    STATE_BUCKET,
    Network,
)


class BootstrapStack(TerraformStack):
    """A bootstrap stack used to create the conditions necessary for deploy.

    At this moment, it creates just the IAM role necessary for deploying.
    """

    deploy_role: DeployRole

    def __init__(
        self,
        scope: Construct,
        id: str,
    ) -> None:
        super().__init__(scope, id)

        AwsProvider(
            self,
            "aws",
            region=REGION,
        )

        self.deploy_role = DeployRole(self, "deploy-role")


class DemoKmnlpInfraStack(TerraformStack):
    """Create Demo stack to run Chainlit service for the KMNLP project.

    The service runs in the Elastic Container Service running as Fargate tasks.
    """

    alb: Alb
    ecs_service: DemoKmnlpChainlitEcsService
    ecs_cluster: EcsCluster
    network: Network

    def __init__(
        self,
        scope: Construct,
        id: str,
    ) -> None:
        super().__init__(scope, id)

        AwsProvider(
            self,
            "aws",
            region=REGION,
        )

        self.network = Network(self, id="kmnlp-network")

        self.alb = Alb(
            self,
            id="alb",
            network=self.network,
            name="demo-kmnlp-chainlit-alb",
            domain="demo.kmnlp.element84.com",
        )

        self.ecs_cluster = EcsCluster(self, "ecs-cluster", name="kmnlp-ecs-cluster")

        self.ecs_service = DemoKmnlpChainlitEcsService(
            self,
            id="ecs_service",
            network=self.network,
            ecs_cluster=self.ecs_cluster,
            alb=self.alb,
        )


app = App()
bootstrap_stack = BootstrapStack(app, "kmnlp_bootstrap")
stack = DemoKmnlpInfraStack(app, "kmnlp_infra")

S3Backend(
    bootstrap_stack,
    region=REGION,
    bucket=STATE_BUCKET,
    key="bootstrap-tf-state",
    dynamodb_table=LOCK_TABLE,
)

S3Backend(
    stack,
    region=REGION,
    bucket=STATE_BUCKET,
    key="kmnlp-tf-state",
    dynamodb_table=LOCK_TABLE,
)

app.synth()
