"""Tests for Wizard101 pet hatching calculations and autocomplete."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import discord
import pytest
from discord.ext import commands

from wiz_hatch.cogs.w101_hatch import (
    Pet,
    PetCatalog,
    UnknownPetError,
    W101Hatch,
    calculate_pet_stats,
    calculate_return_chances,
    cumulative_hatch_probability,
    hatches_for_confidence,
    multi_hatch_odds,
    pet_wiki_url,
    wiki_search_url,
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


def test_wiki_urls_encode_names_and_queries_deterministically() -> None:
    assert pet_wiki_url("O'Brien (Fire) Pet") == (
        "https://wiki.wizard101central.com/wiki/Pet:O%27Brien_(Fire)_Pet"
    )
    assert wiki_search_url("O'Brien (Fire)/Storm") == (
        "https://wiki.wizard101central.com/wiki/Special:Search?search="
        "O%27Brien%20%28Fire%29%2FStorm"
    )
    assert make_pet("O'Brien (Fire) Pet", 5).wiki_url == pet_wiki_url(
        "O'Brien (Fire) Pet"
    )


def test_multi_hatch_probability_and_confidence_math() -> None:
    assert cumulative_hatch_probability(0.1, 3) == pytest.approx(0.271)
    assert hatches_for_confidence(0.5, 0.5) == 1
    assert hatches_for_confidence(0.1, 0.5) == 7

    odds = multi_hatch_odds(0.1)
    assert odds.cumulative == pytest.approx((0.271, 0.40951, 0.651321))
    assert odds.confidence_hatches == (7, 14, 22)


def test_standard_pet_2_stats_use_exact_and_half_even_rounding() -> None:
    stats = calculate_pet_stats()

    assert stats["School-Dealer"].unrounded == Decimal("10.24")
    assert stats["School-Dealer"].rounded == 10
    assert stats["School-Giver / Pain-Giver"].unrounded == Decimal("6.4")
    assert stats["School-Giver / Pain-Giver"].rounded == 6
    assert stats["Armor Piercer"].unrounded == Decimal("2.048")
    assert stats["Armor Piercer"].rounded == 2


def test_mighty_cap_adds_65_strength_to_stat_formulas() -> None:
    stats = calculate_pet_stats(mighty_or_thinkin_cap=True)

    assert stats["School-Dealer"].unrounded == Decimal("11.28")
    assert stats["School-Dealer"].rounded == 11
    assert stats["Spell-Proof"].unrounded == Decimal("11.28")


def _fake_interaction() -> tuple[discord.Interaction, AsyncMock]:
    sender = AsyncMock()
    interaction = cast(
        discord.Interaction,
        SimpleNamespace(response=SimpleNamespace(send_message=sender)),
    )
    return interaction, sender


def _test_cog(*pets: Pet) -> W101Hatch:
    return W101Hatch(cast(commands.Bot, object()), PetCatalog(pets))


@pytest.mark.asyncio
async def test_hatch_command_links_pets_and_shows_cumulative_odds() -> None:
    cog = _test_cog(make_pet("WF Base", 10), make_pet("Target Pet", 1))
    interaction, sender = _fake_interaction()

    await W101Hatch.hatch.callback(cog, interaction, "WF Base", "Target Pet")

    embed = sender.call_args.kwargs["embed"]
    field_values = " ".join(field.value for field in embed.fields)
    assert (
        "[WF Base](https://wiki.wizard101central.com/wiki/Pet:WF_Base)"
        in field_values
    )
    assert (
        "[Target Pet](https://wiki.wizard101central.com/wiki/Pet:Target_Pet)"
        in field_values
    )
    assert "3 hatches:" in field_values
    assert "50% confidence:" in field_values
    assert "90% confidence:" in field_values


@pytest.mark.asyncio
async def test_pet_command_shows_pairing_odds_and_reference_links() -> None:
    cog = _test_cog(make_pet("Target Pet", 1))
    interaction, sender = _fake_interaction()

    await W101Hatch.pet.callback(cog, interaction, "Target Pet")

    embed = sender.call_args.kwargs["embed"]
    field_values = " ".join(field.value for field in embed.fields)
    assert "Wow Factor: 1/10" in field_values
    assert "Exclusive: No" in field_values
    assert "Exact right-slot return: **91%**" in field_values
    assert "50% confidence:" in field_values
    assert "Pet_Locator" in embed.description
    assert "Pet_Stat_Calculator" in embed.description


@pytest.mark.asyncio
async def test_wiki_command_links_exact_pet_and_all_locator_tables() -> None:
    cog = _test_cog(make_pet("Target Pet", 1))
    interaction, sender = _fake_interaction()

    await W101Hatch.wiki.callback(cog, interaction, "Target Pet", "Pet")

    embed = sender.call_args.kwargs["embed"]
    rendered = embed.description + " " + " ".join(
        field.value for field in embed.fields
    )
    assert "Pet:Target_Pet" in rendered
    assert "Category:Pet" in rendered
    assert "Pet_Talent_Locator" in rendered
    assert "Pet_Jewel_Locator" in rendered
    assert "Snack_Finder" in rendered


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

    assert [choice.name for choice in catalog.search_choices("  ALP ")] == [
        "Alpha Wolf",
        "Alpine Bat",
        "Wolf Alpha",
    ]
    assert [pet.name for pet in catalog.search("  ALP ")] == [
        "Alpha Wolf",
        "Alpine Bat",
        "Wolf Alpha",
    ]


def test_empty_autocomplete_query_returns_cached_choices() -> None:
    catalog = PetCatalog([make_pet(f"Pet {index:02}", 5) for index in range(30)])

    first = catalog.search_choices("")
    second = catalog.search_choices("  \t")

    assert first is second
    assert len(first) == 25
    assert all(choice.name == choice.value for choice in first)


def test_autocomplete_reuses_cached_choice_objects() -> None:
    catalog = PetCatalog([make_pet("Alpha Wolf", 5), make_pet("Wolf Alpha", 6)])

    first = catalog.search_choices("alpha")
    second = catalog.search_choices("alpha")

    assert first is not second
    assert first[0] is second[0]


def test_autocomplete_rejects_control_characters() -> None:
    catalog = PetCatalog([make_pet("Alpha", 5)])

    assert catalog.search("Alpha\u0000") == []
    assert catalog.search_choices("Alpha\u0000") == []


def test_checked_in_catalog_resolves_and_calculates_every_known_pet() -> None:
    data_path = Path(__file__).resolve().parents[1] / "data" / "pets.json"
    catalog = PetCatalog.from_path(data_path)

    assert len(catalog.pets) == 1410
    for pet in catalog.pets:
        if pet.wow_factor is None:
            with pytest.raises(UnknownPetError):
                catalog.get_calculable(f" {pet.name} ")
            continue

        assert catalog.get_calculable(f" {pet.name} ") is pet
        expected = (100, 0) if pet.exclusive else (50, 50)
        assert calculate_return_chances(pet, pet) == expected


def test_checked_in_catalog_is_valid_and_fast() -> None:
    data_path = Path(__file__).resolve().parents[1] / "data" / "pets.json"
    catalog = PetCatalog.from_path(data_path)

    started = perf_counter()
    for _ in range(1000):
        results = catalog.search_choices("dragon")
        assert len(results) <= 25
    average_duration = (perf_counter() - started) / 1000
    assert average_duration < 0.005
