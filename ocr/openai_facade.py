"""Deliberately limited single-image chat-completions facade."""

import base64
import binascii
import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import OCROptions, SafeError


class ImageURL(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class ImageContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image_url"]
    image_url: ImageURL


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user"]
    content: list[ImageContent] = Field(min_length=1, max_length=1)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["ocr-auto", "ocr-cpu", "ocr-gpu", "ocr-alias"]
    messages: list[Message] = Field(min_length=1, max_length=1)
    stream: Literal[False] = False
    # OCR extension, not an OpenAI parameter. It cannot lower server sensitivity.
    ocr_options: OCROptions = Field(default_factory=OCROptions)


def image_from_request(body, max_bytes):
    url = body.messages[0].content[0].image_url.url
    formats = {
        "data:image/png;base64,": ".png",
        "data:image/jpeg;base64,": ".jpg",
        "data:image/tiff;base64,": ".tiff",
    }
    for prefix, ext in formats.items():
        if url.startswith(prefix):
            payload = url[len(prefix) :]
            if len(payload) > 4 * ((max_bytes + 2) // 3):
                raise SafeError("upload_limit")
            try:
                data = base64.b64decode(payload, validate=True)
            except (ValueError, binascii.Error):
                raise SafeError("invalid_data_url") from None
            if len(data) > max_bytes:
                raise SafeError("upload_limit")
            return data, "page" + ext
    raise SafeError("only_image_data_urls_supported")


def completion(result, model):
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.markdown},
                "finish_reason": "stop",
            }
        ],
        "ocr": result.model_dump(),
    }
