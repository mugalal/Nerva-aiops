# M4 — Local Kubernetes setup (kind)

```bash
# 1. Create cluster
kind create cluster --config infrastructure/kind-cluster.yaml

# 2. Build demo images
docker build -t nexus/payment-service:v1 --build-arg VERSION=v1 apps/demo-microservices/payment-service
docker build -t nexus/payment-service:v2 apps/demo-microservices/payment-service

# 3. Load images into kind
kind load docker-image nexus/payment-service:v1 --name nexus
kind load docker-image nexus/payment-service:v2 --name nexus

# 4. Deploy healthy v1
kubectl apply -f apps/demo-microservices/k8s/payment-service.yaml

# 5. Scenario A — bad deployment
kubectl apply -f apps/demo-microservices/k8s/payment-service-v2-faulty.yaml

# 6. Rollback (the M4 remediation path)
kubectl rollout undo deployment/payment-service -n nexus-demo
```

Minikube alternative: `minikube start`, then `minikube image load` instead of `kind load`.
