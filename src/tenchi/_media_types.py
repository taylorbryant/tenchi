"""Internal HTTP media-type classification and matching."""

from __future__ import annotations

from codecs import lookup as lookup_codec
from email.message import Message
from typing import cast


class MediaTypeError(ValueError):
    """A media type Tenchi cannot compare or decode safely."""


def is_json_media_type(value: str) -> bool:
    essence = value.partition(";")[0].strip().casefold()
    return essence == "application/json" or essence.endswith("+json")


def is_text_media_type(value: str) -> bool:
    return value.partition(";")[0].strip().casefold().startswith("text/")


def media_type_parts(value: str) -> tuple[str, dict[str, str]]:
    """Return a normalized media essence and parameter mapping.

    Parameter names and charset values are case-insensitive; other parameter
    values retain their case-sensitive value. The essence is read directly
    instead of using ``email.message``'s ``text/plain`` fallback so malformed
    wire values cannot accidentally satisfy a text contract.
    """
    essence = value.partition(";")[0].strip().casefold()
    message = Message()
    message["content-type"] = value
    raw_parameters = cast(
        list[tuple[object, object]],
        message.get_params(header="content-type") or [],
    )
    parameters: dict[str, str] = {}
    for raw_name, raw_value in raw_parameters[1:]:
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            # ``email.message`` represents RFC 2231 extended values as tuples.
            # Content-Type parameters use HTTP's ordinary token/quoted-string
            # form; rejecting the extended form keeps hostile wire values from
            # escaping as AttributeError while preserving unambiguous matching.
            raise MediaTypeError("extended media type parameters are unsupported")
        name = raw_name.casefold()
        parameters[name] = raw_value.casefold() if name == "charset" else raw_value
    return essence, parameters


def text_charset(value: str) -> str:
    """Return the declared text charset, defaulting to UTF-8."""
    return media_type_parts(value)[1].get("charset", "utf-8")


def validate_media_type(value: str) -> None:
    """Validate parameter syntax and any charset used by a declaration."""
    essence, parameters = media_type_parts(value)
    charset = parameters.get("charset")
    if charset is None and essence.startswith("text/"):
        charset = "utf-8"
    if charset is None:
        return
    try:
        lookup_codec(charset)
    except LookupError as exc:
        raise MediaTypeError(f"unsupported charset {charset!r}") from exc


def media_type_matches(*, declared: str, actual: str | None) -> bool:
    """Whether a wire Content-Type satisfies one declared media type.

    The media essence must match exactly. Every parameter declared by the
    contract must also appear with the same value, while additional wire
    parameters are allowed. This accepts common values such as
    ``application/json; charset=utf-8`` for an ``application/json`` contract
    without weakening explicitly declared parameters.
    """
    if actual is None or not actual.strip():
        return False
    try:
        declared_essence, declared_parameters = media_type_parts(declared)
        actual_essence, actual_parameters = media_type_parts(actual)
    except MediaTypeError:
        return False
    return actual_essence == declared_essence and all(
        actual_parameters.get(name) == value
        for name, value in declared_parameters.items()
    )
