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

Not included yet: KEDA scalers on Temporal task-queue backlog and Kafka lag, PodDisruptionBudgets,
and NetworkPolicies restricting the agent runtime to the Tool Gateway.
