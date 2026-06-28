# Kubernetes Deployment

This deployment preserves the current single-node DuckDB boundary:

- one application replica
- `Recreate` deployment strategy
- separate ReadWriteOnce data and artifact volumes
- no concurrent maintenance writer

It adds a non-root read-only container filesystem, dropped capabilities,
resource limits, network policy, TLS ingress, and separate startup, liveness,
and readiness probes.

## Prepare

1. Replace the production image name and tag in
   `overlays/production/kustomization.yaml`.
2. Replace `algo.example.edu` in the ConfigMap patch and ingress.
3. Ensure the `ingress-nginx`, `cert-manager`, and `openalgo` namespaces use the
   expected names or update the NetworkPolicy.
4. Provide an OTLP/HTTP collector at the configured endpoint in the
   `observability` namespace, or update the ConfigMap and NetworkPolicy
   together.
5. Create secrets without committing them:

```bash
kubectl -n iimc-trading create secret generic iimc-platform-secrets \
  --from-env-file=deploy/kubernetes/secrets.env
```

## Render And Apply

```bash
kubectl kustomize deploy/kubernetes/overlays/production
kubectl apply -k deploy/kubernetes/overlays/production
```

The pod starts live but intentionally not ready. Bootstrap through the pod:

```bash
kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli create-user admin --role admin

kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli ai-eval --mode configured

kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli retrieval-eval

kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli backup-create

kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli alerts-evaluate
```

Then inspect readiness:

```bash
kubectl -n iimc-trading exec deploy/iimc-platform -- \
  python -m iimc_trading_platform.cli doctor

kubectl -n iimc-trading port-forward deploy/iimc-platform 8000:8000
curl http://127.0.0.1:8000/ready
```

Do not increase replicas while DuckDB is the active transactional store. Apply
the generated PostgreSQL migration and distributed worker design first.
