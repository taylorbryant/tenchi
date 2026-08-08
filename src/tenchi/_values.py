"""Private semantic value comparison for validated boundary round trips."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from math import isfinite
from typing import cast

from pydantic import BaseModel


def same_python_value(left: object, right: object) -> bool:
    """Compare validated values without allowing Python's cross-type equality."""
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        return isfinite(left) and left == cast(float, right)
    if isinstance(left, BaseModel):
        right_model = cast(BaseModel, right)
        return all(
            same_python_value(getattr(left, name), getattr(right_model, name))
            for name in type(left).model_fields
        ) and same_python_value(left.__pydantic_extra__, right_model.__pydantic_extra__)
    if is_dataclass(left) and not isinstance(left, type):
        return all(
            same_python_value(
                getattr(left, item.name),
                getattr(right, item.name),
            )
            for item in dataclass_fields(left)
        )
    if isinstance(left, Mapping):
        left_mapping = cast(Mapping[object, object], left)
        right_mapping = cast(Mapping[object, object], right)
        if len(left_mapping) != len(right_mapping):
            return False
        remaining = list(right_mapping.items())
        for left_key, left_value in left_mapping.items():
            match = next(
                (
                    (index, right_value)
                    for index, (right_key, right_value) in enumerate(remaining)
                    if same_python_value(left_key, right_key)
                ),
                None,
            )
            if match is None or not same_python_value(left_value, match[1]):
                return False
            remaining.pop(match[0])
        return True
    if isinstance(left, Sequence) and not isinstance(left, str | bytes | bytearray):
        left_sequence = cast(Sequence[object], left)
        right_sequence = cast(Sequence[object], right)
        return len(left_sequence) == len(right_sequence) and all(
            same_python_value(left_item, right_item)
            for left_item, right_item in zip(left_sequence, right_sequence, strict=True)
        )
    if isinstance(left, set | frozenset):
        left_values = cast(set[object] | frozenset[object], left)
        remaining = list(cast(set[object] | frozenset[object], right))
        for left_value in left_values:
            match = next(
                (
                    index
                    for index, right_value in enumerate(remaining)
                    if same_python_value(left_value, right_value)
                ),
                None,
            )
            if match is None:
                return False
            remaining.pop(match)
        return not remaining
    try:
        compared = left == right
    except Exception:
        return False
    return compared is True
