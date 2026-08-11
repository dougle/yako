from __future__ import annotations

import functools

import pytest
from inline_snapshot import Is, snapshot

from tests.unit.plugin.module.utils import run_ansible_module
from yako.plugins.module.yako_mock import run_module

_run_yako_mock_module = functools.partial(run_ansible_module, run_module)


def test_run_yako_mock_module_missing_params() -> None:
    result = _run_yako_mock_module(
        {
            "_mock_original_module_name": "lineinfile",
            "_mock_task_name": "monkey",
            "_mock_consider_changed": False,
        },
        expected_failed=True,
    )
    assert result["failed"]
    assert result["msg"] == snapshot("missing required arguments: path")


@pytest.mark.parametrize(
    "extra_args",
    [
        ({}, "missing required arguments: path"),
        ({"path": "/path/to/file"}, "line is required with state=present"),
        (
            {"path": "/path/to/file", "line": "hello"},
            "Destination /path/to/file does not exist !",
        ),
    ],
)
def test_run_yako_mock_module_check_mode(extra_args: dict) -> None:
    result = _run_yako_mock_module(
        {
            "_mock_original_module_name": "lineinfile",
            "_mock_task_name": "monkey",
            "_mock_consider_changed": False,
            "_mock_check_mode": True,
        }
        | extra_args[0],
        expected_failed=True,
    )

    assert result["failed"]
    assert result["msg"] == Is(extra_args[1])


@pytest.mark.parametrize("consider_changed", [True, False])
def test_run_yako_mock_module_basic(consider_changed: bool) -> None:
    result = _run_yako_mock_module(
        {
            "_mock_original_module_name": "copy",
            "_mock_task_name": "monkey",
            "_mock_consider_changed": consider_changed,
            "src": "/path/to/src",
            "dest": "/path/to/dst",
        }
    )
    assert result["changed"] == consider_changed
    assert result["msg"] == snapshot(
        "Yako Mock module called. Task name: monkey, Original module: copy"
    )


def test_run_yako_mock_module_with_return_value() -> None:
    result = _run_yako_mock_module(
        {
            "_mock_original_module_name": "copy",
            "_mock_task_name": "monkey",
            "_mock_result_dict": {"answer": 42},
            "src": "/path/to/src",
            "dest": "/path/to/dst",
        }
    )

    assert result["answer"] == 42
