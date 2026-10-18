# ChatVector Documentation

ChatVector is an open-source, backend-first RAG engine. Upload a document and ask questions with cited answers. Session follow-ups work by default. Hybrid search, reranking, query rewriting, metadata filters, presets, caching, and webhooks are optional.

It is not a hosted SaaS product, an agent framework, a billing system, or a workflow builder. SDKs are for your server. Do not put API keys in browser code.

Use this site to:

- [Get started locally](getting-started.md) with Docker and your first chat
- Browse the [API reference](api-reference.md) generated from the FastAPI OpenAPI schema
- Integrate with the [Python](sdk/python.md) or [TypeScript](sdk/typescript.md) SDK
- Plan a [production deployment](deployment.md)

## Related resources

- [Repository README](https://github.com/chatvector-ai/chatvector-ai/blob/main/README.md)
- [Architecture overview](https://github.com/chatvector-ai/chatvector-ai/blob/main/ARCHITECTURE.md)
- [Development guide](https://github.com/chatvector-ai/chatvector-ai/blob/main/DEVELOPMENT.md)
- [Roadmap](https://github.com/chatvector-ai/chatvector-ai/blob/main/ROADMAP.md)

## Live API docs (local only)

When the backend runs with `APP_ENV` set to anything other than `production`, interactive Swagger UI and ReDoc are available at:

- http://localhost:8000/docs
- http://localhost:8000/redoc
- http://localhost:8000/openapi.json

These endpoints are disabled in production for security.
