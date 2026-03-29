# ChatVector Logging Integrations

## Overview

ChatVector uses Python’s `logging` module with optional **structured JSON** output when `LOG_FORMAT=JSON`. In normal operation you get two on-disk log files under `backend/logs/`: **application logs** (`app.log`) and **HTTP access logs** (`access.log`). Application logs include a **request ID** on each record (via `RequestIDFilter`) so you can trace a single API request across log lines. The same log streams are also written to the process **console** (stdout/stderr) for container-friendly collection.

## Log Files

| File | Contents |
|------|----------|
| `backend/logs/app.log` | Application logs: routes, services, DB, ingestion, etc. Includes `request_id` in text format; structured fields in JSON mode. |
| `backend/logs/access.log` | Uvicorn HTTP access logs (method, path, status, timing). Rotates with the same policy as `app.log`. |

Both files use rotating handlers (10 MB per file, 5 backups). Uvicorn loggers (`uvicorn`, `uvicorn.error`, `uvicorn.access`) write to **console and** `access.log`; they do not propagate to the root logger, so application code continues to log only to `app.log` (plus console for warnings/captured output as configured).

## Log Fields Reference

When `LOG_FORMAT=JSON`, each line is a JSON object. Typical keys:

| Field | Description |
|-------|-------------|
| `timestamp` | ISO 8601 UTC timestamp (suffix `Z`). |
| `level` | Log level: `INFO`, `WARNING`, `ERROR`, etc. |
| `logger` | Logger name (module or service identifier). |
| `message` | Human-readable log message. |
| `request_id` | Unique per-request ID for tracing (present on application logs when the request-ID middleware/filter applies). |
| `error_type` | Exception or error category name, when supplied via `extra=` on error logs (e.g. LLM/API paths). |
| `http_code` | HTTP status code, when supplied via `extra=` on API-related errors. |
| `stage` | Ingestion or pipeline stage, when supplied via `extra=` (e.g. document processing). |

If an exception was logged with `exc_info`, an `exception` field may contain the traceback string.

## Enabling JSON Format

Set in your environment or `.env`:

```bash
LOG_FORMAT=JSON
```

Use **JSON** when shipping logs to CloudWatch, DataDog, ELK, or other aggregators that parse structured lines.

## Platform Integrations

### AWS CloudWatch

Tail the log files with **Fluent Bit** in a sidecar (or adjacent service) and ship to a CloudWatch log group.

**docker-compose.yml** (excerpt):

```yaml
services:
  fluent-bit:
    image: amazon/aws-for-fluent-bit:latest
    volumes:
      - ./backend/logs:/logs:ro
      - ./fluent-bit.conf:/fluent-bit/etc/fluent-bit.conf:ro
    depends_on:
      - api
```

**fluent-bit.conf** (minimal):

```ini
[SERVICE]
    Flush         1
    Log_Level     info

[INPUT]
    Name              tail
    Path              /logs/*.log
    Tag               chatvector.*

[OUTPUT]
    Name              cloudwatch_logs
    Match             *
    region            us-east-1
    log_group_name    /chatvector/backend
    log_stream_prefix   from-fluent-bit-
    auto_create_group true
```

Adjust `region`, `log_group_name`, and IAM as needed for your account.

### DataDog

Run the **DataDog Agent** with log collection enabled and mount the host or container directory where `app.log` and `access.log` are written.

**docker-compose.yml** (excerpt):

```yaml
services:
  datadog-agent:
    image: gcr.io/datadoghq/agent:latest
    environment:
      - DD_API_KEY=${DD_API_KEY}
      - DD_LOGS_ENABLED=true
      - DD_LOGS_CONFIG_CONTAINER_COLLECT_ALL=true
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - /proc/:/host/proc/:ro
      - ./backend/logs:/var/log/chatvector:ro
```

Configure a log integration in DataDog (or a `conf.d` file) to tail `/var/log/chatvector/*.log`. Setting **`LOG_FORMAT=JSON`** makes it easier to define facets and pipelines on JSON keys in DataDog.

### ELK Stack

Use **Filebeat** to read the rotated log files and send them to Elasticsearch (directly or via Logstash).

**filebeat.yml** (essential fields):

```yaml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /path/to/backend/logs/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["https://localhost:9200"]
```

Set `paths` to the real mount path of `backend/logs`. With `LOG_FORMAT=JSON`, enable `json` decoding as above for structured documents.

### General / stdout

All uvicorn-related output is also written to **stdout/stderr**. Platforms that only collect container logs—**Kubernetes**, **AWS ECS**, **Google Cloud Run**, etc.—can ingest logs without mounting files. Set **`LOG_FORMAT=JSON`** so each line is a single JSON object for easier parsing in the platform’s log explorer.

## Request ID Tracing

The request-ID middleware assigns a unique ID per HTTP request and attaches it to the logging context. That ID appears in **application** log lines (e.g. in `app.log` and in JSON as `request_id`). Use it in your log platform’s search/filter UI to correlate every log line for one request, from routing through services and database access.
