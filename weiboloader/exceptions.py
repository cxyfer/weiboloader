from __future__ import annotations


class WeiboLoaderException(Exception):
    pass


class AuthError(WeiboLoaderException):
    pass


class RateLimitError(WeiboLoaderException):
    pass


class CheckpointError(WeiboLoaderException):
    pass


class TargetError(WeiboLoaderException):
    pass


class APISchemaError(WeiboLoaderException):
    pass


class InitError(WeiboLoaderException):
    pass


_EXIT_CODE_MAP: dict[type[BaseException], int] = {
    AuthError: 3,
    InitError: 2,
}

_DEFAULT_EXIT_CODE = 1
_KEYBOARD_INTERRUPT_EXIT_CODE = 5


def map_exception_to_exit_code(exc: BaseException) -> int:
    if isinstance(exc, KeyboardInterrupt):
        return _KEYBOARD_INTERRUPT_EXIT_CODE
    for exc_type, code in _EXIT_CODE_MAP.items():
        if isinstance(exc, exc_type):
            return code
    return _DEFAULT_EXIT_CODE
