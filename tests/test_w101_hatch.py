"""Tests for Wizard101 pet hatching calculations and autocomplete."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

import pytest

from wiz_hatch.cogs.w101_hatch import (
    Pet,
    PetCatalog,
    UnknownPetError,
    calculate_return_chances,
)


def make_pet(
    name: str,
    wow_factor: int | None,
    *,
    exclusive: bool = False,
) -> Pet:
    """Create a minimal valid pet for unit tests."""
    return Pet(
        name=name,
        school="Balance",
        wow_factor=wow_factor,
        egg_name="Test Egg",
        exclusive=exclusive,
        unhatchable=False,
        retired=False,
        special_body=False,
    )


def test_equal_wow_factors_split_evenly() -> None:
    left = make_pet("Left", 7)
    right = make_pet("Right", 7)

    assert calculate_return_chances(left, right) == (50, 50)


def test_higher_wow_factor_has_lower_return_chance() -> None:
    low_wow = make_pet("Low", 1)
    high_wow = make_pet("High", 10)

    assert calculate_return_chances(low_wow, high_wow) == (91, 9)


def test_exclusive_right_body_guarantees_left_body() -> None:
    left = make_pet("Left", 10)
    right = make_pet("Exclusive Right", 1, exclusive=True)

    assert calculate_return_chances(left, right) == (100, 0)


def test_exclusive_left_body_uses_wow_factor_formula() -> None:
    left = make_pet("Exclusive Left", 10, exclusive=True)
    right = make_pet("Right", 1)

    assert calculate_return_chances(left, right) == (9, 91)


def test_unknown_wow_factor_is_rejected() -> None:
    unknown = make_pet("Unknown", None)
    known = make_pet("Known", 5)

    with pytest.raises(UnknownPetError):
        calculate_return_chances(unknown, known)


def test_autocomplete_prioritizes_prefix_and_excludes_unknown() -> None:
    catalog = PetCatalog(
        [
            make_pet("Alpha Wolf", 5),
            make_pet("Wolf Alpha", 6),
            make_pet("Alpine Bat", 7),
            make_pet("Alpha Unknown", None),
        ]
    )

    assert [pet.name for pet in catalog.search("alp")] == [
        "Alpha Wolf",
        "Alpine Bat",
        "Wolf Alpha",
    ]


def test_autocomplete_rejects_control_characters() -> None:
    catalog = PetCatalog([make_pet("Alpha", 5)])

    assert catalog.search("Alpha\u0000") == []


def test_checked_in_catalog_is_valid_and_fast() -> None:
    data_path = Path(__file__).resolve().parents[1] / "data" / "pets.json"
    catalog = PetCatalog.from_path(data_path)

    assert len(catalog.pets) >= 100
    started = perf_counter()
    for _ in range(100):
        results = catalog.search("dragon")
        assert len(results) <= 25
    average_duration = (perf_counter() - started) / 100
    assert average_duration < 0.05
