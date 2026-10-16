"""Explicit opt-in Augmentoolkit bridge; original evidence remains classified."""

from .schemas import SafeError


def anonymization_handoff(result, tagger):
    """tagger receives text and returns {'text': ..., 'tags': [...]}.

    No implicit fallback to the repository's fail-open anonymizer. The caller owns
    review and release; the original OCR result is not mutated or declassified.
    """
    pages = []
    for p in result.pages:
        try:
            tagged = tagger(p.text)
        except Exception:
            raise SafeError("anonymization_failed") from None
        if (
            not isinstance(tagged, dict)
            or not isinstance(tagged.get("text"), str)
            or not isinstance(tagged.get("tags"), list)
        ):
            raise SafeError("invalid_anonymization_result")
        pages.append(
            dict(
                page_index=p.page_index,
                page_id=p.page_id,
                original_sensitivity=p.sensitivity,
                source=p.source,
                engine=p.engine,
                provider_destination=p.provider_destination,
                anonymized_text=tagged["text"],
                tags=tagged["tags"],
                release_review_required=True,
            )
        )
    return dict(
        schema_version="ocr-anonymization-handoff/v1",
        sensitivity=result.sensitivity,
        pages=pages,
        original_evidence_retained_by_caller=True,
        anonymous=False,
    )


async def augmentoolkit_extract(path, client, token, *, sensitivity="D2"):
    """Trusted CLI/client filesystem only. Uses the private dispatcher, not remote model API."""
    import asyncio
    import json
    from pathlib import Path

    path = Path(path)
    with path.open("rb") as f:
        r = await client.post(
            "/v1/ocr/jobs",
            headers={"Authorization": "Bearer " + token},
            files={"file": (path.name, f)},
            data={"options": json.dumps({"sensitivity": sensitivity})},
        )
    r.raise_for_status()
    job = r.json()
    for _ in range(300):
        r = await client.get(job["status_url"], headers={"Authorization": "Bearer " + token})
        r.raise_for_status()
        if r.json()["status"] in ("completed", "partial", "failed", "cancelled"):
            break
        await asyncio.sleep(1)
    else:
        raise SafeError("client_poll_timeout")
    r = await client.get(job["result_url"], headers={"Authorization": "Bearer " + token})
    r.raise_for_status()
    return r.json()  # Classified evidence; never silently invoke anonymization/publication.
