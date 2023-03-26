from aws_cdk import (
    aws_applicationautoscaling as appscaling,
    aws_certificatemanager as acm,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_kms as kms,
    aws_logs as logs,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
    aws_sns as sns,
)
from constructs import Construct

from .structs import (
    AuroraConfig,
    DatabaseConfig,
    ServerlessConfig,
    TaskConfig,
    WebConfig,
)
from .utils import get_instance_memory_mib


class EcsWebService(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cluster: ecs.Cluster,
        web_config: WebConfig,
        db_secret: secretsmanager.Secret,
        alarm_destination_topic: sns.Topic | None = None,
    ) -> None:
        super().__init__(scope, id)

        self.loadbalanced_service = ecs_patterns.ApplicationLoadBalancedFargateService(  # noqa
            self,
            "LoadbalancedService",
            cluster=cluster,
            circuit_breaker=ecs.DeploymentCircuitBreaker(rollback=True),
            capacity_provider_strategies=(
                [
                    ecs.CapacityProviderStrategy(
                        capacity_provider="FARGATE_SPOT",
                        weight=1,
                    )
                ]
                if web_config.use_spot
                else None
            ),
            cpu=web_config.task_config.cpu,
            memory_limit_mib=web_config.task_config.memory,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(  # noqa
                image=ecs.ContainerImage.from_ecr_repository(
                    repository=web_config.task_config.repository,
                    tag=web_config.task_config.tag,
                ),
                container_name=web_config.task_config.container_name,
                container_port=web_config.task_config.container_port,
                command=web_config.task_config.command,
                enable_logging=True,
                log_driver=ecs.LogDriver.aws_logs(stream_prefix="web"),
                secrets={
                    **{
                        key: ecs.Secret.from_secrets_manager(
                            web_config.task_config.secret,
                            key,
                        )
                        for key in web_config.task_config.secret_keys
                    },
                    **{
                        f"RDS_{key.upper()}": ecs.Secret.from_secrets_manager(
                            db_secret,
                            key,
                        )
                        for key in [
                            "host",
                            "port",
                            "username",
                            "password",
                            "dbname",
                        ]
                    },
                },
            ),
            certificate=(
                web_config.https_config
                and acm.Certificate.from_certificate_arn(
                    self,
                    "Certificate",
                    certificate_arn=web_config.https_config.certificate_arn,
                )
            ),
            ssl_policy=(
                web_config.https_config and web_config.https_config.ssl_policy
            ),
            redirect_http=(
                web_config.https_config
                and web_config.https_config.redirect_http
            ),
        )

        if web_config.auto_scaling_config:
            scalable_target = (
                self.loadbalanced_service.service.auto_scale_task_count(
                    min_capacity=web_config.auto_scaling_config.min_capacity,
                    max_capacity=web_config.auto_scaling_config.max_capacity,
                )
            )
            scalable_target.scale_on_cpu_utilization(
                "CpuScaling",
                target_utilization_percent=web_config.auto_scaling_config.cpu_percent,  # noqa
            )
            scalable_target.scale_on_memory_utilization(
                "MemoryScaling",
                target_utilization_percent=web_config.auto_scaling_config.memory_percent,  # noqa
            )

        if alarm_destination_topic:
            self._add_alarm(alarm_destination_topic)

    def _add_alarm(self, topic: sns.Topic) -> None:
        cpu_alarm = cw.Alarm(
            self,
            "CpuUtilizationAlarm",
            metric=self.loadbalanced_service.service.metric_cpu_utilization(),
            evaluation_periods=5,
            datapoints_to_alarm=3,
            threshold=90,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,  # noqa
        )
        cpu_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        cpu_alarm.add_ok_action(cw_actions.SnsAction(topic))
        cpu_alarm.add_insufficient_data_action(cw_actions.SnsAction(topic))

        memory_alarm = cw.Alarm(
            self,
            "MemoryUtilizationAlarm",
            metric=self.loadbalanced_service.service.metric_memory_utilization(),  # noqa
            evaluation_periods=5,
            datapoints_to_alarm=3,
            threshold=90,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,  # noqa
        )
        memory_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        memory_alarm.add_ok_action(cw_actions.SnsAction(topic))
        memory_alarm.add_insufficient_data_action(cw_actions.SnsAction(topic))


class EcsBatchTask(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        cluster: ecs.Cluster,
        task_config: TaskConfig,
        schedule: appscaling.Schedule,
        db_secret: secretsmanager.Secret,
        alarm_destination_topic: sns.Topic | None = None,
    ) -> None:
        super().__init__(scope, id)

        self.scheduled_task = ecs_patterns.ScheduledFargateTask(
            self,
            "ScheduledTask",
            cluster=cluster,
            cpu=task_config.cpu,
            memory_limit_mib=task_config.memory,
            scheduled_fargate_task_image_options=ecs_patterns.ScheduledFargateTaskImageOptions(  # noqa
                image=ecs.ContainerImage.from_ecr_repository(
                    repository=task_config.repository,
                    tag=task_config.tag,
                ),
                secrets={
                    **{
                        key: ecs.Secret.from_secrets_manager(
                            task_config.secret,
                            key,
                        )
                        for key in task_config.secret_keys
                    },
                    **{
                        f"RDS_{key.upper()}": ecs.Secret.from_secrets_manager(
                            db_secret,
                            key,
                        )
                        for key in [
                            "host",
                            "port",
                            "username",
                            "password",
                            "dbname",
                        ]
                    },
                },
                log_driver=ecs.LogDriver.aws_logs(stream_prefix="batch"),
            ),
            schedule=schedule,
        )

        if alarm_destination_topic:
            self._add_alarm(alarm_destination_topic)

    def _add_alarm(self, topic: sns.Topic) -> None:
        failed_invocations_alarm = cw.Alarm(
            self,
            "FailedInvocationsAlarm",
            metric=cw.Metric(
                namespace="AWS/Events",
                metric_name="FailedInvocations",
                dimensions_map={
                    "RuleName": self.scheduled_task.event_rule.rule_name,
                },
            ),
            evaluation_periods=1,
            datapoints_to_alarm=1,
            threshold=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,  # noqa
        )
        failed_invocations_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        failed_invocations_alarm.add_ok_action(cw_actions.SnsAction(topic))
        failed_invocations_alarm.add_insufficient_data_action(
            cw_actions.SnsAction(topic)
        )


class RdsCluster(Construct):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        aurora_config: AuroraConfig,
    ) -> None:
        super().__init__(scope, id)

        self.security_group = ec2.SecurityGroup(
            self,
            "SecurityGroup",
            vpc=vpc,
            security_group_name="allow access to mysql",
        )
        self.security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(3306),
        )

        self.engine = rds.DatabaseClusterEngine.aurora_mysql(
            version=aurora_config.engine_version
        )
        parameters = aurora_config.parameters.copy()
        [
            parameters.setdefault(key, value)
            for key, value in {
                "server_audit_logging": "1",
                "server_audit_logs_upload": "1",
                "general_log": "1",
                "slow_query_log": "1",
                "long_query_time": "3",
            }.items()
        ]
        self.parameter_group = rds.ParameterGroup(
            self,
            "ParameterGroup",
            engine=self.engine,
            parameters=aurora_config.parameters,
        )

        self.key = kms.Key(
            self,
            "Key",
        )


class AuroraServerless(RdsCluster):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        aurora_config: AuroraConfig,
        serverless_config: ServerlessConfig,
        alarm_destination_topic: sns.Topic | None = None,
    ) -> None:
        super().__init__(scope, id, vpc=vpc, aurora_config=aurora_config)

        self.cluster = rds.ServerlessCluster(
            self,
            "Cluster",
            vpc=vpc,
            security_groups=[self.security_group],
            engine=self.engine,
            parameter_group=self.parameter_group,
            scaling=rds.ServerlessScalingOptions(
                auto_pause=serverless_config.auto_pause,
                min_capacity=serverless_config.min_capacity,
                max_capacity=serverless_config.max_capacity,
            ),
            storage_encryption_key=self.key,
            default_database_name=aurora_config.database_name,
        )

        if alarm_destination_topic:
            self._add_alarm(alarm_destination_topic)

    def _add_alarm(self, topic: sns.Topic) -> None:
        pass


class AuroraDatabase(RdsCluster):
    def __init__(
        self,
        scope: Construct,
        id: str,
        vpc: ec2.Vpc,
        aurora_config: AuroraConfig,
        database_config: DatabaseConfig,
        alarm_destination_topic: sns.Topic | None = None,
    ) -> None:
        super().__init__(scope, id, vpc=vpc, aurora_config=aurora_config)

        self.cluster = rds.DatabaseCluster(
            self,
            "Cluster",
            engine=self.engine,
            instance_props=rds.InstanceProps(
                instance_type=database_config.instance_type,
                vpc=vpc,
                security_groups=[self.security_group],
                allow_major_version_upgrade=False,
                auto_minor_version_upgrade=False,
            ),
            instances=database_config.instances,
            instance_update_behaviour=rds.InstanceUpdateBehaviour.ROLLING,
            cloudwatch_logs_exports=[
                "general",
                "error",
                "slowquery",
                "audit",
            ],
            cloudwatch_logs_retention=logs.RetentionDays.ONE_MONTH,
            parameter_group=self.parameter_group,
            storage_encryption_key=self.key,
            default_database_name=aurora_config.database_name,
        )

        if alarm_destination_topic:
            self._add_alarm(
                alarm_destination_topic,
                database_config.instance_type,
            )

    def _add_alarm(
        self, topic: sns.Topic, instance_type: ec2.InstanceType
    ) -> None:
        cpu_alarm = cw.Alarm(
            self,
            "CpuUtilizationAlarm",
            metric=self.cluster.metric_cpu_utilization(),
            evaluation_periods=5,
            datapoints_to_alarm=3,
            threshold=90,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,  # noqa
        )
        cpu_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        cpu_alarm.add_ok_action(cw_actions.SnsAction(topic))
        cpu_alarm.add_insufficient_data_action(cw_actions.SnsAction(topic))

        memory_alarm = cw.Alarm(
            self,
            "FreeableMemoryAlarm",
            metric=self.cluster.metric_freeable_memory(),
            evaluation_periods=5,
            datapoints_to_alarm=3,
            threshold=(
                get_instance_memory_mib(instance_type.to_string()) * 0.1 / 1024
            ),
            comparison_operator=cw.ComparisonOperator.LESS_THAN_OR_EQUAL_TO_THRESHOLD,  # noqa
        )
        memory_alarm.add_alarm_action(cw_actions.SnsAction(topic))
        memory_alarm.add_ok_action(cw_actions.SnsAction(topic))
        memory_alarm.add_insufficient_data_action(cw_actions.SnsAction(topic))
