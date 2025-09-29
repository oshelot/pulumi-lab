# webapp.py
# A small, deliberate ComponentResource that stands up:
# - ECR repo (so Pulumi can build/push our image)
# - ECS Cluster + Fargate Service
# - ALB + Target Group + Listener
# - Security Groups, IAM role, and CloudWatch Logs
#
# I kept this intentionally explicit (vs. using awsx shortcuts)
# so the reviewer can see all the moving parts.

import json
from typing import Optional, List

import pulumi
import pulumi_aws as aws
import pulumi_docker as docker
import pulumi_tls as tls


class FargateWebApp(pulumi.ComponentResource):
    """
    FargateWebApp(name, vpc_id, subnet_ids, context_dir, container_port, message_value)

    A thin wrapper to run a single-container web app on ECS Fargate behind an ALB.
    - `message_value` becomes the container env var MESSAGE (pulumi config driven).
    - `context_dir` is the Docker build context for the app (./app in my layout).
    """

    def __init__(
        self,
        name: str,
        *,
        vpc_id: pulumi.Input[str],
        subnet_ids: pulumi.Input[List[str]],
        context_dir: str,
        container_port: int,
        message_value: pulumi.Input[str],
        opts: Optional[pulumi.ResourceOptions] = None,
    ):
        # ComponentResource so all child resources show up under one logical node
        super().__init__("examples:components:FargateWebApp", name, None, opts)

        # --- Container Image: ECR repo + build & push via pulumi-docker ---
        # Why ECR first? docker.Image needs a place to push to.
        repo = aws.ecr.Repository(
            f"{name}-repo",
            image_scanning_configuration=aws.ecr.RepositoryImageScanningConfigurationArgs(
                scan_on_push=True
            ),
            opts=pulumi.ResourceOptions(parent=self),
        )
        repo_url = repo.repository_url

        # Pulumi will fetch an auth token for the account/region automatically.
        auth = aws.ecr.get_authorization_token_output(registry_id=repo.registry_id)

        # Build the image from the local context and push to ECR.
        # Important: we’ll reference the repo *digest* later so ECS updates reliably.
        image = docker.Image(
            f"{name}-image",
            build=docker.DockerBuildArgs(context=context_dir),
            image_name=pulumi.Output.concat(repo_url, ":latest"),
            registry=docker.RegistryArgs(
                server=repo_url.apply(lambda u: u.split("/")[0]),
                username=auth.user_name,
                password=auth.password,
            ),
            opts=pulumi.ResourceOptions(parent=repo),
        )

        # --- Networking: Security Groups for ALB and Service ---
        # ALB SG: open to the world on 80 and 443 for HTTP and HTTPS traffic.
        lb_sg = aws.ec2.SecurityGroup(
            f"{name}-lb-sg",
            vpc_id=vpc_id,
            description="ALB security group",
            ingress=[
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp", from_port=80, to_port=80, cidr_blocks=["0.0.0.0/0"]
                ),
                aws.ec2.SecurityGroupIngressArgs(
                    protocol="tcp", from_port=443, to_port=443, cidr_blocks=["0.0.0.0/0"]
                )
            ],
            egress=[aws.ec2.SecurityGroupEgressArgs(
                protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
            )],
            opts=pulumi.ResourceOptions(parent=self),
        )

        # Service SG: only allow traffic from the ALB on the app port.
        svc_sg = aws.ec2.SecurityGroup(
            f"{name}-svc-sg",
            vpc_id=vpc_id,
            description="Service security group",
            ingress=[aws.ec2.SecurityGroupIngressArgs(
                protocol="tcp",
                from_port=container_port,
                to_port=container_port,
                security_groups=[lb_sg.id],  # tighten to LB source
            )],
            egress=[aws.ec2.SecurityGroupEgressArgs(
                protocol="-1", from_port=0, to_port=0, cidr_blocks=["0.0.0.0/0"]
            )],
            opts=pulumi.ResourceOptions(parent=self),
        )

        # --- ALB + Target Group + Listener ---
        # I’m using target_type=ip so Fargate tasks can register directly.
        lb = aws.lb.LoadBalancer(
            f"{name}-alb",
            load_balancer_type="application",
            security_groups=[lb_sg.id],
            subnets=subnet_ids,  # public subnets for a public ALB
            opts=pulumi.ResourceOptions(parent=self),
        )

        # --- SSL Certificate for HTTPS ---
        # For demo purposes, we'll create a self-signed certificate
        # In production, you'd use ACM with your own domain and DNS validation
        
        # Generate a private key
        private_key = tls.PrivateKey(
            f"{name}-private-key",
            algorithm="RSA",
            rsa_bits=2048,
            opts=pulumi.ResourceOptions(parent=self),
        )
        
        # Create a self-signed certificate
        self_signed_cert = tls.SelfSignedCert(
            f"{name}-self-signed-cert",
            key_algorithm="RSA",
            private_key_pem=private_key.private_key_pem,
            subject=tls.SelfSignedCertSubjectArgs(
                common_name="localhost",
                organization="Demo Org",
            ),
            validity_period_hours=8760,  # 1 year
            allowed_uses=[
                "key_encipherment",
                "digital_signature",
                "server_auth",
            ],
            opts=pulumi.ResourceOptions(parent=self),
        )
        
        # Import the certificate into ACM
        cert = aws.acm.Certificate(
            f"{name}-cert",
            private_key=private_key.private_key_pem,
            certificate_body=self_signed_cert.cert_pem,
            opts=pulumi.ResourceOptions(parent=self),
        )

        tg = aws.lb.TargetGroup(
            f"{name}-tg",
            port=80,                              # ALB side uses 80
            protocol="HTTP",
            target_type="ip",
            vpc_id=vpc_id,
            health_check=aws.lb.TargetGroupHealthCheckArgs(
                path="/", matcher="200-399"
            ),
            opts=pulumi.ResourceOptions(parent=lb),
        )

        # HTTP listener that redirects to HTTPS
        listener = aws.lb.Listener(
            f"{name}-http",
            load_balancer_arn=lb.arn,
            port=80,
            protocol="HTTP",
            default_actions=[aws.lb.ListenerDefaultActionArgs(
                type="redirect",
                redirect=aws.lb.ListenerDefaultActionRedirectArgs(
                    port="443",
                    protocol="HTTPS",
                    status_code="HTTP_301"
                )
            )],
            opts=pulumi.ResourceOptions(parent=lb),
        )

        # HTTPS listener on port 443
        https_listener = aws.lb.Listener(
            f"{name}-https",
            load_balancer_arn=lb.arn,
            port=443,
            protocol="HTTPS",
            ssl_policy="ELBSecurityPolicy-TLS-1-2-2017-01",
            certificate_arn=cert.arn,
            default_actions=[aws.lb.ListenerDefaultActionArgs(
                type="forward", target_group_arn=tg.arn
            )],
            opts=pulumi.ResourceOptions(parent=lb),
        )

        # --- ECS cluster ---
        # One cluster is plenty for this demo. (Capacity handled by Fargate.)
        cluster = aws.ecs.Cluster(
            f"{name}-cluster", opts=pulumi.ResourceOptions(parent=self)
        )

        # --- IAM + Logs ---
        # Execution role is the one ECS uses to pull from ECR / write logs.
        task_exec_role = aws.iam.Role(
            f"{name}-task-exec-role",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Action": "sts:AssumeRole",
                    "Principal": {"Service": "ecs-tasks.amazonaws.com"},
                    "Effect": "Allow"
                }]
            }),
            opts=pulumi.ResourceOptions(parent=self),
        )

        aws.iam.RolePolicyAttachment(
            f"{name}-task-exec-policy",
            role=task_exec_role.name,
            policy_arn="arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
            opts=pulumi.ResourceOptions(parent=task_exec_role),
        )

        # Short log retention to keep the demo tidy.
        log_group = aws.cloudwatch.LogGroup(
            f"{name}-logs",
            retention_in_days=7,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # --- Task Definition ---
        # Two details I care about here:
        # 1) Use image *digest* (image.repo_digest) so task revisions roll when the image changes.
        # 2) Inject MESSAGE env var so I can show pulumi config -> app behavior.
        container_defs = pulumi.Output.all(image.repo_digest, log_group.name).apply(
            lambda args: json.dumps([{
                "name": "app",
                "image": args[0],
                "portMappings": [{"containerPort": container_port}],
                "environment": [{"name": "MESSAGE", "value": str(message_value)}],
                "logConfiguration": {
                    "logDriver": "awslogs",
                    "options": {
                        "awslogs-group": args[1],
                        "awslogs-region": aws.config.region,
                        "awslogs-stream-prefix": "ecs"
                    }
                }
            }])
        )

        task_def = aws.ecs.TaskDefinition(
            f"{name}-task",
            family=f"{name}-task",
            cpu="256",                         # tiny footprint = fast demo
            memory="512",
            network_mode="awsvpc",            # required for Fargate
            requires_compatibilities=["FARGATE"],
            execution_role_arn=task_exec_role.arn,
            container_definitions=container_defs,
            opts=pulumi.ResourceOptions(parent=self),
        )

        # --- ECS Service (Fargate) wired to the ALB ---
        # I’m assigning a public IP for simplicity. For production,
        # I’d put tasks in private subnets and keep only the ALB public.
        service = aws.ecs.Service(
            f"{name}-svc",
            cluster=cluster.arn,
            task_definition=task_def.arn,
            desired_count=1,
            launch_type="FARGATE",
            network_configuration=aws.ecs.ServiceNetworkConfigurationArgs(
                subnets=subnet_ids,
                security_groups=[svc_sg.id],
                assign_public_ip=True,
            ),
            load_balancers=[aws.ecs.ServiceLoadBalancerArgs(
                target_group_arn=tg.arn,
                container_name="app",
                container_port=container_port,
            )],
            # Listeners must exist before service tries to attach to the TG.
            opts=pulumi.ResourceOptions(parent=self, depends_on=[listener, https_listener]),
        )

        # Nice, stable output for the stack: ALB DNS is the “app URL”.
        # Use HTTPS URL since we now support TLS
        self.url = pulumi.Output.concat("https://", lb.dns_name)

        # Always register outputs from a ComponentResource so they flow to the stack.
        self.register_outputs({"url": self.url})
