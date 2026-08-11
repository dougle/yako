# Copyright 2022 Hewlett Packard Enterprise Development LP
from __future__ import annotations

import contextlib
import sys
from importlib.util import module_from_spec, spec_from_file_location
from typing import Any
from unittest.mock import patch

from ansible.errors import AnsibleError
from ansible.module_utils.basic import AnsibleModule
from ansible.plugins.loader import module_loader

MODULE_ARGS = {
    "_mock_task_name": {"type": "str", "required": True},
    "_mock_original_module_name": {"type": "str", "required": True},
    "_mock_consider_changed": {"type": "bool", "required": False, "default": False},
    "_mock_result_dict": {"type": "dict", "required": False},
    "_mock_check_mode": {"type": "bool", "required": False, "default": False},
}

class MockedModuleDoesntInheritAnsibleModule(BaseException):
    pass


def get_module_args(module_name: str) -> dict[str, dict[str, object]]:
    # exec the module to extract the original argument spec from the module
    with (
        patch(
            "ansible.module_utils.basic.AnsibleModule.fail_json", return_value=None
        ) as mock_fail,
        patch(
            "ansible.module_utils.basic.AnsibleModule", return_value=None
        ) as mock_ansible_module,
        patch("ansible.module_utils.basic.AnsibleModule._record_module_result"),
    ):
        module_path = module_loader.find_plugin(module_name)
        spec = spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise AnsibleError("Could not load module " + module_name)
        module = module_from_spec(spec)

        # load the actual module to get the arg spec
        # it will fail but catch it
        with contextlib.suppress(Exception):
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            module.main()

        if len(mock_ansible_module.call_args_list) == 0:
            raise MockedModuleDoesntInheritAnsibleModule(
                "Module to be mocked doesn't inherit AnsibleModule"
            )

        mock_fail.reset_mock()

        kwargs = mock_ansible_module.call_args_list[0].kwargs
        return {
            "argument_spec": kwargs.get("argument_spec", {}),
            "supports_check_mode": kwargs.get("supports_check_mode", False)
        }


def get_module_params() -> dict[str, Any]:
    # exec the module to extract the original argument spec from the module
    with (
        patch("ansible.module_utils.basic.AnsibleModule.fail_json", return_value=None),
        patch("ansible.module_utils.basic.AnsibleModule", return_value=None),
        patch("ansible.module_utils.basic.AnsibleModule._record_module_result"),
        contextlib.suppress(Exception),
    ):
        # load a blank module to get the params
        fake_module = AnsibleModule(bypass_checks=True, argument_spec={})
    return {str(k):v for k,v in fake_module.params.items()}


def run_original_module(
    mock_config: dict[str, Any], params: dict[str, object]
) -> list[Any]:
    mock_ansible_module = patch(
        "ansible.module_utils.basic.AnsibleModule.check_mode",
        return_value=True,
        create=True,
    )
    # run this module but filter out the _mock module from supported_parameters
    # see mock class above
    with (
        patch("ansible.module_utils.basic.AnsibleModule.fail_json") as mock_fail,
        patch(
            "ansible.module_utils.basic._load_params",
            return_value=params.copy(),
        ),
        mock_ansible_module
        if mock_config["_mock_check_mode"]
        else contextlib.nullcontext(),
    ):
        module_path = module_loader.find_plugin(
            mock_config["_mock_original_module_name"]
        )
        spec = spec_from_file_location(
            str(mock_config["_mock_original_module_name"]), module_path
        )
        if spec is None or spec.loader is None:
            return []
        module = module_from_spec(spec)

        # load the actual module to get the arg spec
        # it will fail but catch it
        with contextlib.suppress(Exception):
            sys.modules[str(mock_config["_mock_original_module_name"])] = module
            spec.loader.exec_module(module)

            module.main()

        failures = mock_fail.call_args_list
        mock_fail.reset_mock()
        return failures


def run_module() -> None:
    # defaults
    passed_params = get_module_params()
    all_params = {
        "_mock_check_mode": MODULE_ARGS["_mock_check_mode"]["default"],
        "_mock_original_module_name": "Unknown Module",
        "_mock_task_name": "",
        "_mock_consider_changed": MODULE_ARGS["_mock_consider_changed"]["default"],
    } | passed_params
    original_params = {
        str(k): v for k, v in all_params.items() if not str(k).startswith("_mock_")
    }
    mock_config = {str(k): v for k, v in all_params.items()
                   if str(k).startswith("_mock_")}
    original_module_args = {"argument_spec": {}, "supports_check_mode": False}
    with contextlib.suppress(MockedModuleDoesntInheritAnsibleModule):
        original_module_args |= get_module_args(
            str(mock_config["_mock_original_module_name"])
        ).__dict__

    if (
        mock_config["_mock_check_mode"]
        and not original_module_args["supports_check_mode"]
    ):
        mock_config["_mock_check_mode"] = False

    failures: list[Any] = []
    if mock_config["_mock_check_mode"]:
        failures = run_original_module(mock_config, original_params)

    with patch(
        "ansible.module_utils.basic._load_params", return_value=all_params.copy()
    ):
        combined_arg_spec = MODULE_ARGS | original_module_args["argument_spec"].__dict__

        module = AnsibleModule(
            argument_spec=combined_arg_spec,
            supports_check_mode=True,
        )

        module.log(msg="Yako mock module started")

        # output real module's failures here
        for failure in failures:
            module.fail_json(failure.kwargs["msg"])

        # CHANGED
        result = {
            "changed": all_params["_mock_consider_changed"],
            "msg": f"Yako Mock module called. "
            f"Task name: {all_params['_mock_task_name']}, "
            f"Original module: {all_params['_mock_original_module_name']}",
        }

        print(f"{result=}")

        # RESULT DICT
        with contextlib.suppress(KeyError):
            result |= all_params["_mock_result_dict"].__dict__

        module.exit_json(**result)


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
