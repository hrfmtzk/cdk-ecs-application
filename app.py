#!/usr/bin/env python3
import json

import aws_cdk as cdk

from cdk_ecs_application.classes import DeployStep
from cdk_ecs_application.stacks import AppStack, PipelineStack, RepositoryStack
from cdk_ecs_application.structs import EcsClusterConfig, RdsClusterConfig

with open("./config.json") as fp:
    config = json.load(fp)

deploy_step = DeployStep(config["deployStep"])

app = cdk.App()

repository_stack = RepositoryStack(
    app,
    "Repository",
    code_repository_name=config["applicationName"],
    image_repository_name=config["applicationName"],
    image_tag_mutability=config["imageTagMutability"],
)

dev_app_stack = None
if deploy_step in (DeployStep.DEV, DeployStep.STG, DeployStep.PRD):
    _config = config["stageConfig"]["development"]
    dev_app_stack = AppStack(
        app,
        "DevApplication",
        rds_cluster_config=RdsClusterConfig.from_object(_config),
        ecs_cluster_config=EcsClusterConfig.from_object(
            _config,
            repository_stack.image_repository,
            secret=repository_stack.dev_secret,
        ),
    )
    cdk.Tags.of(dev_app_stack).add("Env", "Development")

PipelineStack(
    app,
    "Pipeline",
    code_repository=repository_stack.code_repository,
    branch_name=config["buildTargetBranch"],
    image_repository=repository_stack.image_repository,
    service=(
        dev_app_stack.web_service.loadbalanced_service.service
        if dev_app_stack
        else None
    ),
)

if deploy_step in (DeployStep.STG, DeployStep.PRD):
    _config = config["stageConfig"]["staging"]
    stg_app_stack = AppStack(
        app,
        "StgApplication",
        rds_cluster_config=RdsClusterConfig.from_object(_config),
        ecs_cluster_config=EcsClusterConfig.from_object(
            _config,
            repository_stack.image_repository,
            secret=repository_stack.stg_secret,
        ),
    )
    cdk.Tags.of(stg_app_stack).add("Env", "Staging")

if deploy_step in (DeployStep.PRD,):
    _config = config["stageConfig"]["production"]
    prd_app_stack = AppStack(
        app,
        "PrdApplication",
        rds_cluster_config=RdsClusterConfig.from_object(_config),
        ecs_cluster_config=EcsClusterConfig.from_object(
            _config,
            repository_stack.image_repository,
            secret=repository_stack.prd_secret,
        ),
        backup_target_tag={"Env": "Production"},
        enable_alarm=True,
    )
    cdk.Tags.of(prd_app_stack).add("Env", "Production")

app.synth()
