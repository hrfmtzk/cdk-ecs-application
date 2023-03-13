import aws_cdk as core
import aws_cdk.assertions as assertions

from cdk_ecs_application.cdk_ecs_application_stack import CdkEcsApplicationStack

# example tests. To run these tests, uncomment this file along with the example
# resource in cdk_ecs_application/cdk_ecs_application_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = CdkEcsApplicationStack(app, "cdk-ecs-application")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })
