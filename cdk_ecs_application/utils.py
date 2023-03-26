import boto3


def get_instance_memory_mib(instance_type: str) -> int:
    instance_type = ".".join(instance_type.split(".")[-2:])
    client = boto3.client("ec2")
    response = client.describe_instance_types(
        InstanceTypes=[instance_type],
    )
    memory_mib = response["InstanceTypes"][0]["MemoryInfo"]["SizeInMiB"]
    return memory_mib
