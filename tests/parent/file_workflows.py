"""Real browser file operations against the combined image; synthetic data only."""

from pathlib import Path


def unlock(page, token):
    page.get_by_label("Operator token").fill(token)
    page.get_by_role("button", name="Unlock operator access").click()
    page.get_by_role("button", name="Lock operator access").wait_for()


def input_workflow(page, origin, token):
    page.goto(origin + "/inputs", wait_until="networkidle")
    unlock(page, token)
    name = "Synthetic ü space.txt"
    renamed = "Renamed ü space.txt"
    content = b"Synthetic D0 input lifecycle.\n"
    with page.expect_response(lambda r: "/inputs/upload/" in r.url and r.status == 200):
        page.locator('input[type="file"]').set_input_files(
            {"name": name, "mimeType": "text/plain", "buffer": content}
        )
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
    page.get_by_text(renamed, exact=True).wait_for(state="detached")
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
