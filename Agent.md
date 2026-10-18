## 1. Deployment Configuration

### Target Space
- **Profile:** `Leon4gr45`
- **Space:** `agent-0`
- **Full Identifier:** `Leon4gr45/agent-0`
- **Frontend Port:** `7860` (mandatory for all Hugging Face Spaces)

### Deployment Method
Choose the correct SDK based on the app type based on the codebase language:

- **Gradio SDK** — for Gradio applications
- **Streamlit SDK** — for Streamlit applications
- **Docker SDK** — for all other applications (recommended default for flexibility)

### HF Token
- The environment variable **`$HF_TOKEN` will always be provided at execution time**.
- Never hardcode the token. Always read it from the environment.
- All monitoring and log‑streaming commands rely on `$HF_TOKEN`.

### Required Files
- `Dockerfile` (or `app.py` for Gradio/Streamlit SDKs)
- `README.md` with Hugging Face YAML frontmatter:
  ```yaml
  ---
  title: <APP NAME>
  sdk: docker | gradio | streamlit
  app_port: 7860
  ---
  ```
- `.hfignore` to exclude unnecessary files
- This `Agent.md` file (must be committed before deployment)

---

## 2. API Exposure and Documentation

### Mandatory Endpoints
Every deployment **must** expose:

- **`/health`**
  - Returns HTTP 200 when the app is ready.
  - Required for Hugging Face to transition the Space from *starting* → *running*.

- **`/api-docs`**
  - Documents **all** available API endpoints.
  - Must be reachable at:
    `https://HF_PROFILE-agent-0.hf.space/api-docs`

### Functional Endpoints

### /health
- Method: GET
- Purpose: Health Check

### /readiness
- Method: GET
- Purpose: Readiness Check

### /pipelines/run
- Method: POST
- Purpose: Queue a dataset generation pipeline for execution.

### /jobs
- Method: GET
- Purpose: List jobs from HF_WORK_REPO

### /jobs/{job_id}/manifest
- Method: GET
- Purpose: Inspect job manifest

### /jobs/{job_id}/checkpoints
- Method: GET
- Purpose: Inspect job checkpoints

### /jobs/{job_id}/outputs
- Method: GET
- Purpose: Inspect job output references

### /jobs/{job_id}/resume
- Method: POST
- Purpose: Explicitly resume a job from remote state

### /jobs/{job_id}/fork
- Method: POST
- Purpose: Fork an existing job run into a new durable job

### /pipelines/available
- Method: GET
- Purpose: Get available pipeline aliases from super_config.yaml.

### /configs/aliases
- Method: GET
- Purpose: Get available config file aliases from super_config.yaml.

### /tasks/queue
- Method: GET
- Purpose: Get lists of pending and scheduled tasks.

### /tasks/{task_id}/status
- Method: GET
- Purpose: Get the status of a pipeline run.

### /tasks/{task_id}/parameters
- Method: GET
- Purpose: Get the parameters a task was executed with.

### /tasks/{task_id}/interrupt
- Method: POST
- Purpose: Interrupt a running pipeline task subprocess or revoke a pending task.

### /tasks/{task_id}/logs
- Method: GET
- Purpose: Get logs for a specific task.

### /logs
- Method: DELETE
- Purpose: Clear all task log files.

### /tasks/{task_id}/outputs/download
- Method: GET
- Purpose: Download output directory for a task.

### /outputs/structure/{relative_path}
- Method: GET
- Purpose: Get structure of a path within the outputs directory.

### /inputs/structure/{relative_path}
- Method: GET
- Purpose: Get structure of a path within the inputs directory.

### /configs/structure/{relative_path}
- Method: GET
- Purpose: Get structure of a path within the configs directory.

---

## 3. Deployment Workflow

Precondition: Use the huggingface hub cli hf to check that the space is empty of files nd delete any which are still in there and not belonging to the project to be uploaded

### Standard Deployment Command
After any code change, run:

```bash
hf upload Leon4gr45/agent-0 --repo-type=space
```

Scan build and run logs # Get build logs (SSE) curl -N -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/Leon4gr45/agent-0/logs/build"

Get run logs (SSE) once the build logs succeed
curl -N -H "Authorization: Bearer $HF_TOKEN" "https://huggingface.co/api/spaces/Leon4gr45/agent-0/logs/run"

after 300 seconds to see if the deployment has been successful, and if not, fix the errors of deployment, and redeploy and monitor in a cycle until the space is running and reacts to the api endpoints you created.
