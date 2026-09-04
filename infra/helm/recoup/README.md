# Recoup Helm chart

Deploys Recoup's stateless services. Bring your own PostgreSQL 16 with `pgvector`, Kafka (or
Redpanda), Temporal, Redis and an SMTP endpoint — managed services in production, their own charts
in a dev cluster — and point the chart at them.

```bash
helm upgrade --install recoup infra/helm/recoup \
  --set image.repository=your-org/recoup --set image.tag=0.1.0 \
  --set secrets.existingSecret=recoup-secrets \
  --set config.kafkaBootstrapServers=redpanda:9092 \
  --set config.llm.provider=google_genai --set config.llm.modelStrong=gemini-2.5-pro
```

The Secret must carry `DATABASE_URL`, `JWT_SECRET` and `LLM_API_KEY`. Services that own a schema
run `alembic upgrade head` on start, so a fresh database converges without a separate job. Only
the gateway, the realtime socket and the console are exposed through the ingress; `/internal`
routes are never routed from outside.

## Availability and isolation

`podDisruptionBudgets.enabled` (on by default) renders a `maxUnavailable: 1` budget for every
service running more than one replica. Singletons deliberately get none: a budget on a one-replica
Deployment blocks node drains forever.

`autoscaling.enabled` renders CPU-based HorizontalPodAutoscalers for the services in
`autoscaling.services`. Scaling on queue depth instead (Temporal task-queue backlog, Kafka consumer
lag) needs the KEDA operator and thresholds measured against a real cluster, so it is left to the
operator rather than guessed here.

`networkPolicy.enabled` renders ingress-only policies: a default-deny floor, the ingress controller
reaching the three public services, release-internal traffic between services, and the Tool Gateway
reachable only from the gateway, the orchestrator and evals. That last one is the network mirror of
the in-process rule that agents never touch the ERP or SMTP directly. It is off by default because
the policies do nothing unless the CNI enforces them, and a half-enforced policy reads as
protection without being any. Set `networkPolicy.ingressNamespaceSelector` to your ingress
controller's namespace; leaving it empty admits any namespace.

Egress policies are not included. They need the addresses of Postgres, Kafka, Temporal, Redis and
the LLM endpoint, which differ per environment.
