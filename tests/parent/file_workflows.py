"""Real browser file operations against the combined image; synthetic data only."""

from pathlib import Path


def unlock(page, token):
    page.get_by_label("Operator token").fill(token)
    page.get_by_role("button", name="Unlock operator access").click()
    page.get_by_role("button", name="Lock operator access").wait_for()


def input_workflow(page, origin, token):
    page.goto(origin + "/inputs", wait_until="networkidle")
    with page.expect_response(
        lambda r: "/inputs/structure/" in r.url and r.status == 200
    ):
        unlock(page, token)
    page.wait_for_load_state("networkidle")
    name = "Synthetic ü space.txt"
    renamed = "Renamed ü space.txt"
    content = b"Synthetic D0 input lifecycle.\n"
    with page.expect_response(lambda r: "/inputs/upload/" in r.url) as upload_response:
        page.locator('input[type="file"]').set_input_files(
            {"name": name, "mimeType": "text/plain", "buffer": content}
        )
    assert upload_response.value.status == 200
    page.get_by_text(name, exact=True).wait_for()
    page.get_by_text(name, exact=True).click()
    with page.expect_download(timeout=30000) as pending:
        page.get_by_role("button", name="Download", exact=True).click()
    download = pending.value
    assert Path(download.path()).read_bytes() == content
    assert download.suggested_filename == name
    page.get_by_role("button", name="Create Folder", exact=True).click()
    page.get_by_label("Folder Name:").fill("Synthetic folder ü")
    page.get_by_role("button", name="Create", exact=True).click()
    page.get_by_text("Synthetic folder ü", exact=True).wait_for()
    page.get_by_text(name, exact=True).click()
    page.get_by_role("button", name="Rename", exact=True).click()
    page.get_by_label("New name", exact=True).fill(renamed)
    with page.expect_response(
        lambda r: (
            r.request.method == "POST"
            and r.url.endswith("/inputs/move")
            and r.status == 200
        )
    ):
        page.get_by_role("button", name="Save name", exact=True).click()
    page.get_by_text(renamed, exact=True).wait_for()
    assert page.get_by_text(name, exact=True).count() == 0
    page.reload(wait_until="networkidle")
    page.get_by_label("Operator token").wait_for()
    unlock(page, token)
    page.get_by_text(renamed, exact=True).wait_for()
    page.get_by_text(renamed, exact=True).click()
    page.get_by_role("button", name="Delete", exact=True).click()
    # Confirmation modal reuses the existing file manager component.
    with page.expect_response(
        lambda r: r.request.method == "DELETE" and r.status == 200
    ):
        page.get_by_role("button", name="Delete", exact=True).last.click()
    page.get_by_role("tree").get_by_text(renamed, exact=True).wait_for(state="detached")
    page.reload(wait_until="networkidle")
    unlock(page, token)
    page.get_by_text("Synthetic folder ü", exact=True).wait_for()
    assert page.get_by_text(renamed, exact=True).count() == 0
    return {
        "upload_list_download_rename_refresh_delete_absence": True,
        "directory_creation": True,
        "download_bytes_match": True,
        "Unicode_spaces": True,
        "refresh_requires_unlock": True,
    }


def config_workflow(page, origin, token):
    """Read bundled YAML, copy, edit, save, refresh, reopen and compare bytes."""
    external_requests = []

    def record_request(request):
        if request.url.startswith(
            ("http://", "https://")
        ) and not request.url.startswith(origin + "/"):
            external_requests.append(request.url.split("?")[0])

    page.on("request", record_request)
    page.goto(origin + "/configs", wait_until="networkidle")
    unlock(page, token)
    page.get_by_role("link", name="synthetic-config", exact=True).click()
    page.get_by_text(
        "Read-only bundled template. Create a private copy to edit.", exact=True
    ).wait_for()
    assert page.get_by_role("button", name="Save", exact=True).is_disabled()
    page.get_by_role("button", name="Create private copy", exact=True).click()
    name = "Private ü configuration.yaml"
    page.get_by_label("Private YAML filename").fill(name)
    page.get_by_role("button", name="Create copy", exact=True).click()
    page.get_by_text(
        "Private editable configuration; saved in private ephemeral storage.",
        exact=True,
    ).wait_for()
    editor = page.get_by_role("textbox", name="Configuration YAML", exact=True)
    editor.wait_for()
    editor.focus()
    page.keyboard.press("Control+a")
    expected = "title: Synthetic ü configuration\nvalue: edited privately\n"
    page.keyboard.insert_text(expected)
    with page.expect_response(
        lambda r: (
            "/configs/content/" in r.url
            and r.request.method == "POST"
            and r.status == 200
        )
    ):
        page.get_by_role("button", name="Save", exact=True).click()
    page.reload(wait_until="networkidle")
    unlock(page, token)
    page.get_by_text(
        "Private editable configuration; saved in private ephemeral storage.",
        exact=True,
    ).wait_for()
    page.goto(origin + "/configs", wait_until="networkidle")
    unlock(page, token)
    page.get_by_text(name, exact=True).dblclick()
    page.get_by_role("textbox", name="Configuration YAML", exact=True).wait_for()
    from urllib.parse import quote

    response = page.request.get(
        origin + "/configs/content/" + quote(name),
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status == 200 and response.body() == expected.encode()
    assert page.get_by_role("button", name="Save", exact=True).is_disabled()
    page.goto(origin + "/configs", wait_until="networkidle")
    unlock(page, token)
    page.get_by_text(name, exact=True).click()
    page.get_by_role("button", name="Create Folder", exact=True).click()
    page.get_by_label("Folder Name:").fill("Synthetic config folder ü")
    page.get_by_role("button", name="Create", exact=True).click()
    page.get_by_text("Synthetic config folder ü", exact=True).wait_for()
    page.remove_listener("request", record_request)
    assert not external_requests, external_requests
    return {
        "bundled_read_only": True,
        "private_copy_edit_save_refresh_reopen": True,
        "exact_bytes": True,
        "Unicode_spaces": True,
        "local_editor_bundle": True,
        "directory_beside_selected_root_file": True,
    }


def preparation_workflow(page, origin, token):
    """Real browser -> local process -> preview -> exact-byte JSONL download."""
    import hashlib
    import json
    from urllib.parse import urlsplit

    page.goto(origin + "/inputs", wait_until="networkidle")
    with page.expect_response(
        lambda r: "/inputs/structure/" in r.url and r.status == 200
    ):
        unlock(page, token)
    page.wait_for_load_state("networkidle")
    name = "Public preparation ü.txt"
    content = ("Public synthetic D0 preparation.\r\n" * 80).encode()
    with page.expect_response(lambda r: "/inputs/upload/" in r.url) as pending:
        page.locator('input[type="file"]').set_input_files(
            {"name": name, "mimeType": "text/plain", "buffer": content}
        )
    assert pending.value.status == 200
    page.get_by_role("tree").get_by_text(name, exact=True).click()
    panel = page.get_by_role("region", name="Dataset preparation")
    panel.get_by_label("Private preparation config").select_option(
        "Private ü configuration.yaml"
    )
    panel.get_by_role("checkbox").check()
    with page.expect_response(
        lambda r: (
            urlsplit(r.url).path == "/pilot/preparation" and r.request.method == "POST"
        )
    ) as submitted:
        panel.get_by_role("button", name="Prepare Dataset", exact=True).click()
    assert submitted.value.status == 202
    job = submitted.value.json()["job_id"]
    page.wait_for_url("**/monitor")
    panel = page.get_by_role("region", name="Dataset preparation")
    panel.get_by_text("COMPLETED", exact=True).wait_for(timeout=20000)
    panel.get_by_role("button", name="Preview JSONL").click()
    panel.get_by_label("JSONL preview").wait_for()
    with page.expect_download() as pending:
        panel.get_by_role("button", name="Download JSONL").click()
    downloaded = Path(pending.value.path()).read_bytes()
    response = page.request.get(
        f"{origin}/pilot/preparation/{job}/download/records.jsonl",
        headers={"Authorization": "Bearer " + token},
    )
    assert response.status == 200 and response.body() == downloaded
    rows = [json.loads(line) for line in downloaded.splitlines()]
    assert len(rows) >= 2
    assert all(
        r["kind"] == "preparation_chunk"
        and r["sensitivity"] == "D0"
        and r["source_sha256"] == hashlib.sha256(content).hexdigest()
        for r in rows
    )
    assert "".join(r["text"] for r in rows) == content.decode().replace("\r\n", "\n")
    page.goto(origin + "/outputs", wait_until="networkidle")
    unlock(page, token)
    page.get_by_role("region", name="Dataset preparation").get_by_text(
        "COMPLETED", exact=True
    ).wait_for()
    return {
        "job_id": job,
        "record_count": len(rows),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "download_sha256": hashlib.sha256(downloaded).hexdigest(),
        "exact_browser_download": True,
        "preview": True,
        "outputs_listing": True,
        "native_generation": "NOT_RUN",
        "model_calls": 0,
    }
