# __main__.py — deploy the FargateWebApp component (ECR+ECS+ALB)
import pulumi
import pulumi_aws as aws
from webapp import FargateWebApp

cfg = pulumi.Config()
message = cfg.require("message")  # set via: pulumi config set message "..."

# Keep it simple: use the default VPC + its subnets
vpc = aws.ec2.get_vpc(default=True)
subnets = aws.ec2.get_subnets(filters=[aws.ec2.GetSubnetsFilterArgs(
    name="vpc-id", values=[vpc.id]
)])

app = FargateWebApp(
    "hello",
    vpc_id=vpc.id,
    subnet_ids=subnets.ids,
    context_dir="../app",     # Dockerfile/package.json/server.js live here
    container_port=8080,
    message_value=message,   # becomes process.env.MESSAGE in the container
)

pulumi.export("url", app.url)
