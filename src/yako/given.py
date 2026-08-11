from __future__ import annotations

import itertools
from collections import ChainMap, OrderedDict
from typing import TYPE_CHECKING, Annotated, Any

from ansible.errors import AnsibleUndefinedVariable
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from yako.assert_check import AssertMode, AssertResult, AssertStmt, FileMode
from yako.utils import not_test

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Self


class MockActionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    result_dict: dict[str, Any] = {}
    changed: bool = False
    check_mode: bool = False

    def gen_action(
        self, original_action_name: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        new_action_name = "yako_mock"
        new_action_name_args = {
            "_mock_task_name": new_action_name,
            "_mock_original_module_name": original_action_name,
            "_mock_consider_changed": self.changed,
            "_mock_result_dict": self.result_dict,
            "_mock_check_mode": self.check_mode,
        }
        return new_action_name, new_action_name_args


def _ensure_custom_action(
    value: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if len(value) != 1:
        raise ValueError("custom_action must have exactly one key-value pair")
    return value


class MockActionCustomConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    custom_action: Annotated[
        dict[str, dict[str, Any]], AfterValidator(_ensure_custom_action)
    ]

    def gen_action(
        self, original_action_name: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        new_action_name = next(iter(self.custom_action.keys()))
        return new_action_name, self.custom_action[new_action_name]


@not_test
class TestCaseAssert(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    value: Any | None = None
    mode: AssertMode = AssertMode.Equal
    msg: str | None = None
    file: FileMode = FileMode.No

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if (
            self.mode
            in (
                AssertMode.IsNone,
                AssertMode.IsNotNone,
                AssertMode.IsTrue,
                AssertMode.IsFalse,
                AssertMode.IsNotTrue,
                AssertMode.IsNotFalse,
            )
            and self.value is not None
        ):
            msg = (
                "expected should not have value in these modes. "
                f"name: {self.name}, mode: {self.mode}"
            )
            raise ValueError(msg)

        if self.file not in (FileMode.No, FileMode.Right):
            msg = (
                "FileMode only supports 'right' or 'no' mode in test case assert. "
                f"name: {self.name}, file: {self.file}"
            )
            raise ValueError(msg)

        if self.file != FileMode.No and self.mode not in (
            AssertMode.Equal,
            AssertMode.NotEqual,
            AssertMode.In,
            AssertMode.NotIn,
        ):
            msg = (
                "File mode is not supported in the mode. "
                "The file mode should be 'no'"
                f"name: {self.name}, file: {self.file}"
            )
            raise ValueError(msg)

        return self

    def check(self, get_actual_value_func: Callable[[str], Any]) -> AssertResult:
        result = None
        try:
            actual_value = get_actual_value_func(self.name)
            result = self._to_assert_stmt(actual_value).check()
        except (KeyError, AnsibleUndefinedVariable) as err:
            result = self._to_var_not_found_result(err)
        except Exception as err:  # noqa: BLE001
            result = self._to_unknown_error_result(err)

        return result

    def _to_assert_stmt(self, actual_value: Any) -> AssertStmt:
        return AssertStmt(
            actual=actual_value,
            expected=self.value,
            mode=self.mode,
            file=self.file,
            msg=self.msg or f"variable name: {self.name}",
        )

    def _to_unknown_error_result(self, err: Exception) -> AssertResult:
        return self._to_error_result(
            f"Unknown error. assert_stmt: {self}, err: {err}, err_type: {type(err)}",
        )

    def _to_var_not_found_result(self, err: Exception) -> AssertResult:
        return self._to_error_result(f"Variable: {self.name} not found, err: {err}")

    def _to_error_result(self, msg: str) -> AssertResult:
        return AssertResult(
            passed=False,
            actual_value=None,
            expected_value=self.value,
            mode=self.mode,
            err_msg=msg,
        )


class TestTaskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    task: str
    extra_vars: dict[str, Any] = Field(default_factory=dict, alias="vars")
    mock: MockActionConfig | MockActionCustomConfig | None = None

    assert_inputs: list[TestCaseAssert] = []
    assert_outputs: list[TestCaseAssert] = []

    should_be_skipped: bool | None = None
    should_be_changed: bool | None = None
    should_fail: bool | None = None


class CopyFileConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    src: str
    dest: str

    @classmethod
    def from_src(cls, src: str) -> Self:
        return cls(src=src, dest=src if not src.endswith("/") else src.rstrip("/"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        if "src" in data and "dest" in data:
            return cls(src=data["src"], dest=data["dest"])
        raise ValidationError("CopyFileConfig dict must have 'src' and 'dest' keys")


def _parse_copy_file_config_list(value: Any) -> list[CopyFileConfig]:
    if not isinstance(value, list):
        raise ValidationError("files must be a list")

    parsed_values = []
    for item in value:
        match item:
            case CopyFileConfig():
                parsed_values.append(item)
            case str():
                parsed_values.append(CopyFileConfig.from_src(item))
            case dict():
                parsed_values.append(CopyFileConfig.from_dict(item))
            case _:
                raise ValidationError(
                    "Each item in files list must be either a string or a dict"
                )
    return parsed_values


@not_test
class TestCaseGiven(BaseModel):
    model_config = ConfigDict(frozen=True)

    files: Annotated[
        list[CopyFileConfig], BeforeValidator(_parse_copy_file_config_list)
    ] = []
    extra_vars: dict[str, Any] = Field(default_factory=dict, alias="vars")
    state: list[TestTaskConfig] = []

    @classmethod
    def from_merge(cls, *givens: Self) -> Self:
        state: OrderedDict[str, TestTaskConfig] = OrderedDict(
            (task_state.task, task_state)
            for task_state in itertools.chain.from_iterable(
                given.state for given in givens
            )
        )

        return cls.model_validate(
            {
                "files": list(
                    itertools.chain.from_iterable(given.files for given in givens)
                ),
                "vars": dict(ChainMap(*(given.extra_vars for given in givens))),
                "state": list(state.values()),
            }
        )
