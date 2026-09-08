# Konstruktor Kubernetes Deployment

This directory contains Docker images and Kubernetes Job templates for running Konstruktor in
ephemeral workers. The images are split by toolchain so jobs can use the smallest practical runner.

## Runner Images

| Image | Intended workload | Included tools |
|---|---|---|
| `konstruktor-runner:base` | General tasks without compilation | Python 3.12, Git, OpenHands |
| `konstruktor-runner:python` | Python projects | pytest, Ruff, mypy, Black |
| `konstruktor-runner:node` | Node.js and TypeScript projects | Node.js 22, TypeScript, ESLint, Jest |
| `konstruktor-runner:go` | Go projects | Go 1.24 |
| `konstruktor-runner:full` | Mixed-language projects | Python, Node.js, Go, Rust, Java, Maven |

## Build And Push

Run these commands from the repository root:

```bash
docker build -f deploy/docker/Dockerfile.base -t konstruktor-runner:base .
docker build -f deploy/docker/Dockerfile.python -t konstruktor-runner:python .
docker build -f deploy/docker/Dockerfile.node -t konstruktor-runner:node .
docker build -f deploy/docker/Dockerfile.go -t konstruktor-runner:go .
docker build -f deploy/docker/Dockerfile.full -t konstruktor-runner:full .

docker tag konstruktor-runner:python registry.example.com/konstruktor-runner:python
docker push registry.example.com/konstruktor-runner:python
```

## Create The API Secret

```bash
kubectl create secret generic konstruktor-secrets \
  --from-literal=llm-api-key="$KONSTRUKTOR_LLM_API_KEY"
```

## Run A Job

The Job templates are parameterized for `envsubst`:

```bash
export TASK="Add email validation to the signup form"
export TASK_ID="email-validation-$(date +%s)"
export IMAGE="registry.example.com/konstruktor-runner:python"
export RUNNER_LANG="python"

envsubst < deploy/k8s/job.yaml | kubectl apply -f -
kubectl wait --for=condition=complete job/konstruktor-$TASK_ID --timeout=3600s
kubectl logs job/konstruktor-$TASK_ID -f
```

For GPU jobs, set the advanced template values before applying it:

```bash
export TASK="Optimize model training"
export GPU=true
export GPU_NODE_SELECTOR="
      nodeSelector:
        accelerator: nvidia-a100"
export GPU_TOLERATIONS="
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule"
export GPU_RESOURCE_REQUEST='nvidia.com/gpu: "1"'
export GPU_RESOURCE_LIMIT='nvidia.com/gpu: "1"'

envsubst < deploy/k8s/job-advanced.yaml | kubectl apply -f -
```

## Node Labels

```bash
kubectl label node worker-1 runner=python
kubectl label node worker-2 runner=node
kubectl label node worker-3 runner=go
kubectl label node worker-4 runner=full

kubectl label node gpu-1 accelerator=nvidia-a100
kubectl taint node gpu-1 nvidia.com/gpu=exists:NoSchedule
```

## CronJob Example

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: konstruktor-nightly-lint
spec:
  schedule: "0 3 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: runner
              image: konstruktor-runner:python
              env:
                - name: KONSTRUKTOR_TASK
                  value: "Run all linters and fix failures"
                - name: KONSTRUKTOR_MODE
                  value: "plan"
                - name: KONSTRUKTOR_LLM_API_KEY
                  valueFrom:
                    secretKeyRef:
                      name: konstruktor-secrets
                      key: llm-api-key
                - name: GIT_REPO
                  value: "https://github.com/myorg/myproject.git"
                - name: GIT_REF
                  value: "main"
          restartPolicy: Never
```

## Monitoring

```bash
kubectl get jobs -l app=konstruktor-runner
kubectl logs -l app=konstruktor-runner --tail=50
kubectl describe job konstruktor-<task-id>
```

## Local Container Test

```bash
docker run --rm \
  -e KONSTRUKTOR_TASK="Create hello.py" \
  -e KONSTRUKTOR_MODE="run" \
  -e KONSTRUKTOR_LLM_API_KEY="$KONSTRUKTOR_LLM_API_KEY" \
  -e KONSTRUKTOR_LLM_BASE_URL="https://opencode.ai/zen/go/v1" \
  konstruktor-runner:python
```

For production queues, place an API or UI in front of Redis, Kafka, or NATS and have an operator
create Jobs, track retries, and persist `konstruktor-output` plus plan artifacts to durable storage.
