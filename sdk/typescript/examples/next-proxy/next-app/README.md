# Next.js proxy routes

Server route handlers that mirror the Fastify example:

| Route | Upstream |
| --- | --- |
| `POST /api/documents` | upload + wait until ready |
| `GET /api/documents/:id` | document status |
| `GET /api/documents/:id/status/stream` | SSE status stream (passthrough) |
| `POST /api/sessions` | create session |
| `POST /api/chat` | non-stream chat |
| `POST /api/chat/stream` | SSE chat stream (passthrough) |

All `/api/*` routes require an `x-user-id` header as a visible stand-in for your
real authentication layer.

`CHATVECTOR_API_KEY` and `CHATVECTOR_BASE_URL` are read only in this Node
process (see `.env.example`). Never expose them via `NEXT_PUBLIC_*`.
