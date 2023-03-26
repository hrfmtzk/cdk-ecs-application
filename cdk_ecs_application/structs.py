from dataclasses import dataclass
from typing import Any

from aws_cdk import (
    Duration,
    aws_applicationautoscaling as appscaling,
    aws_ec2 as ec2,
    aws_ecr as ecr,
    aws_elasticloadbalancingv2 as elbv2,
    aws_rds as rds,
    aws_secretsmanager as secretsmanager,
)


@dataclass
class TaskConfig:
    repository: ecr.Repository
    tag: str
    container_name: str
    container_port: int
    secret: secretsmanager.Secret
    secret_keys: list[str]
    cpu: int = 256
    memory: int = 512
    command: list[str] | None = None


@dataclass
class HttpsConfig:
    certificate_arn: str
    ssl_policy: elbv2.SslPolicy = elbv2.SslPolicy.RECOMMENDED
    redirect_http: bool = True


@dataclass
class AutoScalingConfig:
    min_capacity: int = 1
    max_capacity: int = 2
    cpu_percent: int = 70
    memory_percent: int = 70


@dataclass
class WebConfig:
    task_config: TaskConfig
    https_config: HttpsConfig | None = None
    auto_scaling_config: AutoScalingConfig | None = None
    use_spot: bool = False


@dataclass
class BatchConfig:
    batch_name: str
    task_config: TaskConfig
    schedule: appscaling.Schedule


@dataclass
class EcsClusterConfig:
    web_config: WebConfig
    batch_configs: list[BatchConfig]

    @classmethod
    def from_object(
        cls,
        config: dict[str, Any],
        repository: ecr.Repository,
        secret: secretsmanager.Secret,
    ):
        ecs_config = config["ecs"]

        _web_config = ecs_config["web"]
        web_config = WebConfig(
            task_config=TaskConfig(
                repository=repository,
                tag=_web_config["tag"],
                container_name=_web_config["containerName"],
                container_port=_web_config["containerPort"],
                secret=secret,
                secret_keys=_web_config["secretKeys"],
                cpu=_web_config["cpu"],
                memory=_web_config["memory"],
                command=_web_config.get("command"),
            ),
            https_config=(
                HttpsConfig(
                    certificate_arn=_web_config["https"]["certificateArn"],
                    ssl_policy=getattr(
                        elbv2.SslPolicy, _web_config["https"]["sslPolicy"]
                    ),
                    redirect_http=_web_config["https"]["redirectHttp"],
                )
                if "https" in _web_config
                else None
            ),
            auto_scaling_config=(
                AutoScalingConfig(
                    min_capacity=_web_config["autoScaling"]["minCapacity"],
                    max_capacity=_web_config["autoScaling"]["maxCapacity"],
                    cpu_percent=_web_config["autoScaling"]["cpuPercent"],
                    memory_percent=_web_config["autoScaling"]["memoryPercent"],
                )
                if "autoScaling" in _web_config
                else None
            ),
            use_spot=_web_config["useSpot"],
        )

        batch_configs = [
            BatchConfig(
                batch_name=_batch_config["batchName"],
                task_config=TaskConfig(
                    repository=repository,
                    tag=_batch_config["tag"],
                    container_name=_batch_config["containerName"],
                    container_port=_web_config["containerPort"],
                    secret=secret,
                    secret_keys=_batch_config["secretKeys"],
                    cpu=_batch_config["cpu"],
                    memory=_batch_config["memory"],
                    command=_batch_config.get("command"),
                ),
                schedule=appscaling.Schedule.cron(**_batch_config["cron"]),
            )
            for _batch_config in ecs_config["batches"]
        ]

        return cls(
            web_config=web_config,
            batch_configs=batch_configs,
        )


@dataclass
class AuroraConfig:
    engine_version: rds.AuroraMysqlEngineVersion
    parameters: dict[str, str]
    database_name: str


@dataclass
class ServerlessConfig:
    min_capacity: rds.AuroraCapacityUnit
    max_capacity: rds.AuroraCapacityUnit
    auto_pause: Duration | None = None


@dataclass
class DatabaseConfig:
    instance_type: ec2.InstanceType
    instances: int


@dataclass
class RdsClusterConfig:
    aurora_config: AuroraConfig
    serverless_config: ServerlessConfig | None = None
    database_config: DatabaseConfig | None = None

    def __post_init__(self) -> None:
        if (self.serverless_config and self.database_config) or not (
            self.serverless_config or self.database_config
        ):
            raise ValueError(
                "only one of `serverless_config` or `database_config` must be specified"  # noqa
            )

    @classmethod
    def from_object(cls, config: dict[str, Any]):
        rds_config = config["rds"]
        version_number = rds_config["engineVersion"].replace(".", "_")

        aurora_config = AuroraConfig(
            engine_version=getattr(
                rds.AuroraMysqlEngineVersion, f"VER_{version_number}"
            ),
            parameters=rds_config["parameters"],
            database_name=rds_config["databaseName"],
        )

        serverless_config = None
        database_config = None

        if rds_config["serverless"]:
            serverless_config = ServerlessConfig(
                auto_pause=(
                    Duration.minutes(rds_config["autoPauseMinutes"])
                    if "autoPauseMinutes" in rds_config
                    else None
                ),
                min_capacity=getattr(
                    rds.AuroraCapacityUnit, f"ACU_{rds_config['minCapacity']}"
                ),
                max_capacity=getattr(
                    rds.AuroraCapacityUnit, f"ACU_{rds_config['maxCapacity']}"
                ),
            )
        else:
            database_config = DatabaseConfig(
                instance_type=ec2.InstanceType(rds_config["instanceType"]),
                instances=rds_config["instances"],
            )

        return cls(
            aurora_config=aurora_config,
            serverless_config=serverless_config,
            database_config=database_config,
        )
