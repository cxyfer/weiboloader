from __future__ import annotations

from hypothesis import given, strategies as st

from weiboloader.exceptions import (
    APISchemaError,
    AuthError,
    CheckpointError,
    InitError,
    RateLimitError,
    TargetError,
    WeiboLoaderException,
    map_exception_to_exit_code,
)

VALID_EXIT_CODES = {0, 1, 2, 3, 5}

ALL_EXCEPTION_CLASSES = [
    WeiboLoaderException,
    AuthError,
    RateLimitError,
    CheckpointError,
    TargetError,
    APISchemaError,
    InitError,
]

_exc_strategy = st.sampled_from(
    [cls("test") for cls in ALL_EXCEPTION_CLASSES]
    + [KeyboardInterrupt(), Exception("generic"), RuntimeError("rt")]
)


@given(exc=_exc_strategy)
def test_exit_code_in_valid_set(exc: BaseException) -> None:
    code = map_exception_to_exit_code(exc)
    assert code in VALID_EXIT_CODES


def test_auth_error_exit_3() -> None:
    assert map_exception_to_exit_code(AuthError()) == 3


def test_init_error_exit_2() -> None:
    assert map_exception_to_exit_code(InitError()) == 2


def test_keyboard_interrupt_exit_5() -> None:
    assert map_exception_to_exit_code(KeyboardInterrupt()) == 5


def test_generic_exception_exit_1() -> None:
    assert map_exception_to_exit_code(Exception("x")) == 1


def test_target_error_exit_1() -> None:
    assert map_exception_to_exit_code(TargetError()) == 1


def test_rate_limit_error_exit_1() -> None:
    assert map_exception_to_exit_code(RateLimitError()) == 1


def test_checkpoint_error_exit_1() -> None:
    assert map_exception_to_exit_code(CheckpointError()) == 1


def test_api_schema_error_exit_1() -> None:
    assert map_exception_to_exit_code(APISchemaError()) == 1


def test_inheritance_chain() -> None:
    for cls in ALL_EXCEPTION_CLASSES:
        assert issubclass(cls, WeiboLoaderException)
    assert issubclass(WeiboLoaderException, Exception)
