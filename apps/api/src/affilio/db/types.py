"""Reusable column type helpers."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Store a StrEnum as VARCHAR(32) with a CHECK constraint named `ck_<table>_<name>`.

    Native PostgreSQL enums are avoided on purpose: adding a value to them is awkward to
    reverse in migrations, whereas a CHECK constraint can be dropped and recreated.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda cls: [member.value for member in cls],
    )
