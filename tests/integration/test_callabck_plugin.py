from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from yako.ansible import make_yako_ansible_config
from yako.consts import YAKO_TEST_CONFIG_KEY
from yako.test_case import TestCaseInputConfig

TEST_PLAYBOOK_BASE_DIR = (Path(__file__).parent / "test_callback_plugin").resolve()

if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

PLAYBOOK_DEFAULT_CONTENT = {
    "hosts": "all",
    "any_errors_fatal": True,
    "gather_facts": False,
}


def _make_content_playbook(raw_content: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**PLAYBOOK_DEFAULT_CONTENT, "tasks": raw_content}]


def _search_ansible_playbook() -> Path | None:
    bin_path = Path(sys.executable).parent / "ansible-playbook"
    if bin_path.exists():
        return bin_path

    if default_bin_path := shutil.which("ansible-playbook"):
        return Path(default_bin_path)

    return None


def _run_ansible_playbook(
    *,
    ws_dir: Path,
    playbook_path: Path,
    yako_test_case_path: Path,
    ansible_cfg_path: Path,
    search_file_paths: list[Path],
    extra_args: list[str] | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    ansible_playbook_bin = _search_ansible_playbook()
    if not ansible_playbook_bin:
        raise RuntimeError("ansible-playbook is unavailable.")

    env = {
        "ANSIBLE_CONFIG": str(ansible_cfg_path),
        "ANSIBLE_STDOUT_CALLBACK": "debug",
    }
    search_file_path = ":".join(str(p.resolve()) for p in search_file_paths)
    cmd: tuple[str, ...] = (
        *(
            str(ansible_playbook_bin),
            "-v",
            "--connection=local",
            "--inventory",
            "127.0.0.1,",
            "--limit",
            "127.0.0.1",
            "-e",
            f"yako_workspace_dir={ws_dir.resolve()}",
            "-e",
            f"yako_search_file_path={search_file_path}",
            "-e",
            f"@{yako_test_case_path.resolve()}",
        ),
        *(extra_args or ()),
        str(playbook_path.resolve()),
    )
    logger.debug("Run ansible-playbook command: %s", cmd)

    return subprocess.run(
        cmd,
        env=env,
        cwd=ws_dir,
        check=False,
        encoding="utf8",
        capture_output=capture_output,
    )


def _run_single_test(
    test_case_path: Path,
    extra_roles_path: list[str] | None = None,
    extra_args: list[str] | None = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    raw_test = yaml.safe_load(test_case_path.read_text())[YAKO_TEST_CONFIG_KEY]
    test_case = TestCaseInputConfig.model_validate(raw_test)

    with tempfile.TemporaryDirectory() as raw_tmp_dir:
        ws_dir = Path(raw_tmp_dir).resolve()
        ansible_cfg_path = ws_dir / "ansible.cfg"
        make_yako_ansible_config(
            output_path=ansible_cfg_path, roles_path=extra_roles_path
        )
        logger.debug("Ansible config: %s", ansible_cfg_path.read_text())

        if test_case.tasks:
            # Create a tmp playbook and assign it back
            test_playbook_path = ws_dir / f"{test_case_path.stem}_playbook.yaml"
            test_playbook_path.write_text(
                yaml.dump(_make_content_playbook(test_case.tasks))
            )

            search_file_paths = [ws_dir, test_case_path.parent]

            return _run_ansible_playbook(
                ws_dir=ws_dir,
                playbook_path=test_playbook_path,
                yako_test_case_path=test_case_path,
                ansible_cfg_path=ansible_cfg_path,
                extra_args=extra_args,
                capture_output=capture_output,
                search_file_paths=search_file_paths,
            )

    raise NotImplementedError


def _run_test(
    test_case_file_name: str, search_str: str, is_passed: bool = True
) -> None:
    result = _run_single_test(TEST_PLAYBOOK_BASE_DIR / test_case_file_name)

    msgs = []
    if is_passed and result.returncode:
        msgs.append(f"Return code is {result.returncode}. Expected: 0")
    if search_str not in result.stdout:
        msgs.append(
                "Can not find the string in stdout."
                f"\n\tstr: '{search_str}'"
                f"\n\tstdout: {result.stdout}"
        )

    if os.environ.get("YAKO_DEBUG_INTEGRATION_TEST", "0") == "1":
        print(
            "\n".join(
                (
                    f"Test file: {test_case_file_name}",
                    f"Return code: {result.returncode}",
                    "----- STDOUT -----",
                    result.stdout,
                    "----- STDERR -----",
                    result.stderr,
                    "------------------",
                )
            )
        )

    if msgs:
        # print(f"{msgs=}")
        msg = "\n".join(
            (
                f"Test file failed: {test_case_file_name}",
                *msgs,
                "----- STDOUT -----",
                result.stdout,
                "----- STDERR -----",
                result.stderr,
                "------------------",
            )
        )
        raise AssertionError(msg)


def _list_test_cases(
    base_dir: Path,
    config_key: str = "YAKO_INTERNAL_INTEGRATION_TEST_CONFIG",
) -> list[tuple[str, str, bool]]:
    test_paths = [
        path
        for path in base_dir.iterdir()
        if path.is_file() and path.name.startswith("test") and path.suffix == ".yaml"
    ]

    test_cases = []
    for path in test_paths:
        if path.name == "test_mock_basic.yaml":
            with path.open() as fin:
                raw_config = yaml.safe_load(fin)
                if test_config := raw_config.get(config_key, None):
                    test_cases.append(
                        (
                            path.name,
                            test_config["search_keyword"],
                            test_config.get("is_passed", True),
                        )
                    )

    return sorted(test_cases, key=lambda k: k[0])


@pytest.mark.integration
@pytest.mark.parametrize(
    ("test_case_file_name", "search_str", "is_pass"),
    _list_test_cases(TEST_PLAYBOOK_BASE_DIR),
)
def test_yako_callback(
    test_case_file_name: str, search_str: str, is_pass: bool
) -> None:
    _run_test(test_case_file_name, search_str, is_pass)
