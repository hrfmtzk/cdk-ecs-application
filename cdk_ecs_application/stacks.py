from aws_cdk import (
    Duration,
    SecretValue,
    Stack,
    aws_backup as backup,
    aws_codebuild as codebuild,
    aws_codecommit as codecommit,
    aws_codepipeline as codepipeline,
    aws_codepipeline_actions as cpactions,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_ecs as ecs,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
)
from constructs import Construct

from .constructs import (
    AuroraDatabase,
    AuroraServerless,
    EcsBatchTask,
    EcsWebService,
)
from .structs import EcsClusterConfig, RdsClusterConfig


class RepositoryStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code_repository_name: str,
        image_repository_name: str,
        image_tag_mutability: bool,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.code_repository = codecommit.Repository(
            self,
            "CodeRepository",
            repository_name=code_repository_name,
        )
        self.image_repository = ecr.Repository(
            self,
            "ImageRepository",
            repository_name=image_repository_name,
            image_tag_mutability=(
                ecr.TagMutability.MUTABLE
                if image_tag_mutability
                else ecr.TagMutability.IMMUTABLE
            ),
        )

        self.dev_secret = secretsmanager.Secret(
            self,
            "DevSecret",
            secret_object_value={
                "ENV": SecretValue.unsafe_plain_text("development"),
            },
        )
        self.stg_secret = secretsmanager.Secret(
            self,
            "StgSecret",
            secret_object_value={
                "ENV": SecretValue.unsafe_plain_text("staging"),
            },
        )
        self.prd_secret = secretsmanager.Secret(
            self,
            "PrdSecret",
            secret_object_value={
                "ENV": SecretValue.unsafe_plain_text("production"),
            },
        )


class PipelineStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code_repository: codecommit.Repository,
        branch_name: str,
        image_repository: ecr.Repository,
        service: ecs.FargateService | None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.pipeline = codepipeline.Pipeline(
            self,
            "Pipeline",
        )
        source_artifact = codepipeline.Artifact("source_artifact")
        build_artifact = codepipeline.Artifact("build_artifact")

        self.pipeline.add_stage(
            stage_name="Source",
            actions=[
                cpactions.CodeCommitSourceAction(
                    output=source_artifact,
                    repository=code_repository,
                    branch=branch_name,
                    code_build_clone_output=True,
                    trigger=cpactions.CodeCommitTrigger.EVENTS,
                    action_name="Source",
                ),
            ],
        )

        build_project = codebuild.PipelineProject(
            self,
            "BuildProject",
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_6_0,
            ),
        )
        image_repository.grant_pull_push(build_project)
        self.pipeline.add_stage(
            stage_name="Build",
            actions=[
                cpactions.CodeBuildAction(
                    input=source_artifact,
                    project=build_project,
                    environment_variables={
                        "AWS_ACCOUNT_ID": codebuild.BuildEnvironmentVariable(
                            value=self.account,
                            type=codebuild.BuildEnvironmentVariableType.PLAINTEXT,  # noqa
                        ),
                        "REPOSITORY_NAME": codebuild.BuildEnvironmentVariable(
                            value=image_repository.repository_name,
                            type=codebuild.BuildEnvironmentVariableType.PLAINTEXT,  # noqa
                        ),
                    },
                    outputs=[build_artifact],
                    action_name="Build",
                ),
            ],
        )

        if service:
            self.pipeline.add_stage(
                stage_name="Deploy",
                actions=[
                    cpactions.EcsDeployAction(
                        service=service,
                        deployment_timeout=Duration.minutes(5),
                        input=build_artifact,
                        action_name="Deploy",
                    ),
                ],
            )


class AppStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        rds_cluster_config: RdsClusterConfig,
        ecs_cluster_config: EcsClusterConfig,
        backup_target_tag: dict[str, str] | None = None,
        enable_alarm: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.alarm_destination_topic = (
            sns.Topic(
                self,
                "AlarmDesticationTopic",
            )
            if enable_alarm
            else None
        )

        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
            ],
            max_azs=2,
        )

        self.database = (
            AuroraServerless(
                self,
                "Aurora",
                vpc=self.vpc,
                aurora_config=rds_cluster_config.aurora_config,
                serverless_config=rds_cluster_config.serverless_config,
                alarm_destination_topic=self.alarm_destination_topic,
            )
            if rds_cluster_config.serverless_config
            else AuroraDatabase(
                self,
                "Aurora",
                vpc=self.vpc,
                aurora_config=rds_cluster_config.aurora_config,
                database_config=rds_cluster_config.database_config,
                alarm_destination_topic=self.alarm_destination_topic,
            )
        )

        self.ecs_cluster = ecs.Cluster(
            self,
            "EcsCluster",
            vpc=self.vpc,
            container_insights=True,
            enable_fargate_capacity_providers=True,
        )

        self.web_service = EcsWebService(
            self,
            "WebService",
            cluster=self.ecs_cluster,
            web_config=ecs_cluster_config.web_config,
            db_secret=self.database.cluster.secret,
        )
        self.batch_tasks = {
            batch_config.batch_name: EcsBatchTask(
                self,
                f"BatchTask-{batch_config.batch_name}",
                cluster=self.ecs_cluster,
                task_config=batch_config.task_config,
                schedule=batch_config.schedule,
                db_secret=self.database.cluster.secret,
            )
            for batch_config in ecs_cluster_config.batch_configs
        }

        if backup_target_tag:
            self._add_backup(target_tag=backup_target_tag)

    def _add_backup(self, target_tag: dict[str, str]) -> None:
        self.backup_plan = backup.BackupPlan.daily35_day_retention(
            self,
            "BackupPlan",
        )
        self.backup_plan.add_selection(
            "Selection",
            resources=[
                backup.BackupResource.from_tag(key, value)
                for key, value in target_tag.items()
            ],
        )
