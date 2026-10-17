---
title: agent-0
sdk: docker
app_port: 7860
---

# Agent-facing CPU OCR Service

This repository provides an authenticated, public-facing REST API for document recognition using the PP-OCRv6 Small model over ONNX Runtime CPU.

## API Documentation

The OCR service exposes endpoints under `https://Leon4gr45-agent-0.hf.space`. All endpoints except `/health` require authentication using a Bearer token.

### Authentication
Include the `OCR_CPU_AUTH_TOKEN` in the `Authorization` header:

```http
Authorization: Bearer <YOUR_OCR_CPU_AUTH_TOKEN>
```

### Endpoints
- `GET /health` : Public liveness probe.
- `GET /ready` : Authenticated model readiness check.
- `GET /v1/models` : List available OCR models (returns `ocr-cpu`).
- `GET /v1/ocr/capabilities` : Get current hardware constraints and engine capabilities.
- `POST /v1/ocr/page` : Multipart image upload for page OCR (requires `sensitivity=D0`).
- `POST /v1/chat/completions` : Restricted OpenAI-compatible interface for image OCR.
- `GET /openapi.json` : OpenAPI schema.
- `GET /docs` : API documentation.

### Example: Multipart OCR with `httpx` (Python)
```python
import httpx

url = "https://Leon4gr45-agent-0.hf.space/v1/ocr/page"
headers = {"Authorization": "Bearer <YOUR_OCR_CPU_AUTH_TOKEN>"}
data = {"sensitivity": "D0"}
files = {"file": ("document.png", open("document.png", "rb"), "image/png")}

with httpx.Client() as client:
    response = client.post(url, headers=headers, data=data, files=files)
    print(response.json())
```

### Example: OpenAI SDK Integration
```python
import base64
from openai import OpenAI

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

client = OpenAI(
    base_url="https://Leon4gr45-agent-0.hf.space/v1",
    api_key="<YOUR_OCR_CPU_AUTH_TOKEN>"
)

base64_image = encode_image("document.png")

response = client.chat.completions.create(
    model="ocr-cpu",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{base64_image}"}
                }
            ]
        }
    ]
)

print(response.choices[0].message.content)
```

## Agent Integration

To give an agent access to this OCR tool, provide it with the following tool definition:

```json
{
  "name": "recognize_document_page",
  "description": "Perform Optical Character Recognition (OCR) on an image using the agent-0 service. Submit a base64 encoded image to receive the extracted text and structured evidence.",
  "parameters": {
    "type": "object",
    "properties": {
      "image_base64": {
        "type": "string",
        "description": "The base64 encoded image content."
      }
    },
    "required": ["image_base64"]
  }
}
```

## Constraints and Limits
- **Sensitivity:** The public API strictly processes explicitly authorized public documents (`D0`).
- **File size:** Images must not exceed 25 MiB.
- **Resolution:** Maximum of 20,000,000 pixels.
- **Rate limiting:** 60 requests per minute.
- **Sleep mode:** Hugging Face free spaces sleep after inactivity. The first request may take up to a minute to start the environment.
