"""Only three role-variable writes are allowed; no live Space operations."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tools.sync_dataset_repository_ids import EXPECTED_REVISION, KEYS, SyncFailed, sync


@pytest.fixture
def api():
    api = MagicMock()
    api.repo_info.return_value = SimpleNamespace(private=True)
    api.space_info.return_value = SimpleNamespace(private=True, sha=EXPECTED_REVISION)
    values = {
        "HF_HUB_OFFLINE": SimpleNamespace(value="1"),
        "ATK_JOB_EXECUTION_ENABLED": SimpleNamespace(value="false"),
    }
    api.get_space_variables.side_effect = lambda _: dict(values)

    def set_variable(repo_id, key, value):
        assert key in KEYS
        values[key] = SimpleNamespace(value=value)

    api.add_space_variable.side_effect = set_variable
    return api


def test_sync_only_role_ids_after_private_validation(api):
    roles = dict(zip(KEYS, ["fixture/source", "fixture/work", "fixture/output"]))
    result = sync(api, roles, apply=True)
    assert result["applied"] and result["changed_keys"] == list(KEYS)
    assert {c.kwargs["key"] for c in api.add_space_variable.call_args_list} == set(KEYS)
    api.add_space_secret.assert_not_called()
    api.restart_space.assert_not_called()
    api.update_repo_settings.assert_not_called()
    api.create_repo.assert_not_called()


def test_incomplete_ids_or_public_repo_make_no_changes(api):
    with pytest.raises(ValueError):
        sync(api, {KEYS[0]: "fixture/source"}, apply=True)
    api.repo_info.return_value.private = False
    with pytest.raises(ValueError):
        sync(api, {k: "fixture/private" for k in KEYS}, apply=True)
    api.add_space_variable.assert_not_called()


def test_target_revision_change_stops_before_writes(api):
    api.space_info.return_value.sha = "different"
    with pytest.raises(ValueError):
        sync(api, {k: "fixture/private" for k in KEYS}, apply=True)
    api.add_space_variable.assert_not_called()


def test_partial_failure_is_recorded_without_credentials(api):
    api.add_space_variable.side_effect = PermissionError("secret-key-must-not-leak")
    with pytest.raises(SyncFailed) as error:
        sync(api, {k: "fixture/private" for k in KEYS}, apply=True)
    assert error.value.result["attempted_keys"] == [KEYS[0]]
    assert error.value.result["confirmed_keys"] == []
    assert error.value.result["partial_change_possible"]
    assert "secret-key" not in str(error.value)
    assert api.add_space_variable.call_count == 1
