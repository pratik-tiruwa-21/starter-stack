# ClawdContext OS — Kubernetes / OpenShift Deployment

## Quick Start

```bash
# 1. Create namespace
kubectl apply -f 00-namespace.yaml

# 2. Configure secrets (edit first!)
cp 01-secrets.yaml 01-secrets-local.yaml
# Edit 01-secrets-local.yaml with your actual base64-encoded values
kubectl apply -f 01-secrets-local.yaml

# 3. Deploy everything
kubectl apply -f .

# Or use Kustomize:
kubectl apply -k .
```

## OpenShift

```bash
# Use OpenShift overlay (adds Route + SCC)
kubectl apply -k overlays/openshift/
```

## Architecture

```
                    ┌─────────────────────┐
                    │   Ingress / Route   │
                    └─────────┬───────────┘
                              │ :443
                    ┌─────────▼───────────┐
                    │   Dashboard (nginx)  │ :3000
                    │   Reverse Proxy      │
                    └────┬──┬──┬──┬──┬────┘
         ┌───────────────┘  │  │  │  └──────────────┐
         ▼                  ▼  │  ▼                  ▼
   ┌───────────┐   ┌──────────┐│┌──────────┐  ┌───────────┐
   │AgentProxy │   │ Scanner  │││ Recorder │  │  Replay   │
   │  :8400    │   │  :8401   │││  :8402   │  │  :8404    │
   └───────────┘   └──────────┘│└──────────┘  └───────────┘
                               ▼
                        ┌────────────┐
                        │  OpenClaw  │ :8403
                        │ (DeepSeek) │
                        └──┬─────┬──┘
                     ┌─────┘     └─────┐
                     ▼                 ▼
              ┌────────────┐    ┌────────────┐
              │ CodeRunner  │    │  Memory    │ :8405
              │  :8406     │    │  Service   │
              └────────────┘    └──┬─────────┘
                                   │
                                   ▼
                              ┌─────────┐
                              │ Qdrant  │ :6333
                              └─────────┘
```

## Services

| Service | Port | Layer | Image |
|---|---|---|---|
| agent-proxy | 8400 | 4 | `ghcr.io/yaamwebsolutions/ccos-agent-proxy` |
| scanner-api | 8401 | 1 | `ghcr.io/yaamwebsolutions/ccos-scanner-api` |
| flight-recorder | 8402 | 5 | `ghcr.io/yaamwebsolutions/ccos-flight-recorder` |
| openclaw | 8403 | agent | `ghcr.io/yaamwebsolutions/ccos-openclaw` |
| replay-engine | 8404 | 5-6 | `ghcr.io/yaamwebsolutions/ccos-replay-engine` |
| memory-service | 8405 | memory | `ghcr.io/yaamwebsolutions/ccos-memory-service` |
| code-runner | 8406 | sandbox | `ghcr.io/yaamwebsolutions/ccos-code-runner` |
| qdrant | 6333 | memory | `qdrant/qdrant:v1.12.6` |
| dashboard | 3000 | 0 | `ghcr.io/yaamwebsolutions/ccos-dashboard` |

## Customization

### Image Registry

Update image references in `kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/yaamwebsolutions/ccos-agent-proxy
    newName: your-registry.example.com/ccos-agent-proxy
    newTag: v1.0.0
```

### Resource Limits

Each Deployment has conservative defaults. Tune `resources.requests` and `resources.limits` based on your workload.

### TLS

The Ingress uses `tls` with a secret name. Create the TLS secret:

```bash
kubectl create secret tls ccos-tls \
  --cert=path/to/cert.pem \
  --key=path/to/key.pem \
  -n ccos
```

### Persistent Storage

All PVCs use `ReadWriteOnce`. For multi-replica deployments, switch to `ReadWriteMany` with a shared storage class.
