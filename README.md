# Pulumi Option 1 — ECS Fargate (Python)

A minimal, interview-ready deployment of a Node/Express app to **AWS ECS Fargate** behind an **ALB**, with a **Pulumi config** value rendered at `/`.

## What this demonstrates
- Pulumi **config → env var → UI** flow via `message`.
- A compact **ComponentResource** that provisions: **ECR** (image by digest), **ECS** (cluster/task/service), **ALB** (TG/Listener), **Security Groups**, **CloudWatch Logs**, and the **task execution IAM role**.
- Clean, non-Kubernetes “managed container” path (Option 1).

## Architecture
```
Docker build → ECR (digest)
                    ↓
            ECS Task Definition
                    ↓
           ECS Service (Fargate, awsvpc)
ALB (HTTP:80) → Target Group (ip targets) → Tasks
```

## Repository layout (used here)
```
pulumi-ecs-app/
├─ Pulumi.yaml        # main: infra
├─ requirements.txt
├─ app/               # container code
│  ├─ Dockerfile
│  ├─ package.json
│  └─ server.js       # reads process.env.MESSAGE
└─ infra/             # Pulumi program
   ├─ __main__.py     # reads config.message, instantiates component
   └─ webapp.py       # FargateWebApp (ECR/ECS/ALB/SG/Logs/IAM)
```

## Run (high level)
1) Select the stack and set config:
   - `aws:region` (e.g., `us-east-2`)
   - `message` (e.g., `abc123`)
2) Deploy with `pulumi up`.
3) Open the exported `url` (ALB DNS).

## Change the value
Update `message` in Pulumi config and run `pulumi up`. Refresh the page to see the new value.

## Clean up
`pulumi destroy`

## Notes
- Uses the **default VPC** for simplicity.
- Tasks are public for the demo; for production, place tasks in **private subnets** behind a public ALB.
- Task Definition references the image **digest** to ensure rollouts when the image changes.
