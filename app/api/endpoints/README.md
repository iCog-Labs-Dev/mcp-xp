## Galaxy API Endpoints

REST API exposing Galaxy workflows, histories, tools, datasets, and invocation tracking via BioBlend.

### Authentication

All endpoints require a JWT bearer token with a `galaxy_api_token` claim.

### WebSocket Support

Long-running operations accept `tracker_id` for real-time status updates over WebSocket.

---

### Workflows (`/workflows`)

Manage Galaxy workflows.

* `GET /workflows`
  List all non-deleted workflows (Redis cached per user).

* `POST /workflows/upload-workflow`
  Upload `.ga` workflow file (async, WebSocket progress).

* `GET /workflows/{workflow_id}/details`
  Retrieve full workflow metadata and step definitions.

* `DELETE /workflows/DELETE`
  Delete workflows by comma-separated IDs (async, cache updated immediately).


### Invocation (`/invocation`)

Track workflow executions and results.

* `GET /invocation`
  List workflow invocations (cached, filterable by workflow/history).

* `GET /invocation/{invocation_id}/result`
  Retrieve invocation status, outputs, and report.

* `GET /invocation/{invocation_id}/invocation_pdf`
  Download invocation report as PDF.

* `DELETE /invocation/DELETE`
  Logical deletion: Since galaxy doesn't offer native invocation deletion suport, we cancel runs, purge datasets, hide from listings.

### Dataset (`/dataset`)

Adopt existing files and objects into Galaxy.

* `POST /dataset/local_import`
  Import local filesystem file into Galaxy (MongoDB cached).

* `POST /dataset/object_import`
  Import MinIO/S3 object into from other services into Galaxy (MongoDB cached).

* `POST /dataset/index_file`
  Trigger asynchronous dataset indexing (e.g. BAM, VCF).