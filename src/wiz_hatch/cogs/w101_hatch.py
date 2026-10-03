"""Wizard101 pet body return chance slash command."""

from __future__ import annotations

import json
import os
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

import discord
from discord import app_commands
from discord.ext import commands

MAX_AUTOCOMPLETE_CHOICES: Final = 25
MAX_PET_NAME_LENGTH: Final = 100
SOURCE_URL: Final = "https://petbodyw101.vercel.app/"


class PetDataError(ValueError):
    """Raised when the checked-in pet catalog is invalid."""


class UnknownPetError(ValueError):
    """Raised when a supplied pet name is absent or cannot be calculated."""


@dataclass(frozen=True, slots=True)
class Pet:
    """An immutable Wizard101 pet body record."""

    name: str
    school: str
    wow_factor: int | None
    egg_name: str
    exclusive: bool
    unhatchable: bool
    retired: bool
    special_body: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Pet:
        """Validate and construct a pet from decoded JSON."""
        name = _required_string(value, "name")
        if len(name) > MAX_PET_NAME_LENGTH:
            msg = f"Pet name exceeds {MAX_PET_NAME_LENGTH} characters: {name!r}"
            raise PetDataError(msg)

        raw_wow_factor = value.get("wowFactor")
        if raw_wow_factor is not None and (
            isinstance(raw_wow_factor, bool)
            or not isinstance(raw_wow_factor, int)
            or not 0 <= raw_wow_factor <= 10
        ):
            msg = f"Invalid wow factor for {name!r}: {raw_wow_factor!r}"
            raise PetDataError(msg)

        return cls(
            name=name,
            school=_required_string(value, "school"),
            wow_factor=raw_wow_factor,
            egg_name=_required_string(value, "eggName", allow_empty=True),
            exclusive=_required_bool(value, "exclusive"),
            unhatchable=_required_bool(value, "unhatchable"),
            retired=_required_bool(value, "retired"),
            special_body=_required_bool(value, "specialBody"),
        )


@dataclass(frozen=True, slots=True)
class _SearchEntry:
    """Precomputed data used by autocomplete matching."""

    normalized_name: str
    pet: Pet
    choice: app_commands.Choice[str]


class PetCatalog:
    """Immutable in-memory pet lookup and autocomplete index."""

    def __init__(self, pets: Iterable[Pet]) -> None:
        ordered = tuple(sorted(pets, key=lambda pet: pet.name.casefold()))
        by_name: dict[str, Pet] = {}
        choices: list[app_commands.Choice[str]] = []
        searchable: list[_SearchEntry] = []
        prefix_index: dict[str, list[_SearchEntry]] = {}
        for pet in ordered:
            normalized_name = normalize_pet_name(pet.name).casefold()
            if normalized_name in by_name:
                msg = f"Duplicate pet name after normalization: {pet.name!r}"
                raise PetDataError(msg)

            by_name[normalized_name] = pet
            choice = app_commands.Choice(name=pet.name, value=pet.name)
            choices.append(choice)
            if pet.wow_factor is not None:
                entry = _SearchEntry(normalized_name, pet, choice)
                searchable.append(entry)
                for end in range(1, len(normalized_name) + 1):
                    prefix_index.setdefault(normalized_name[:end], []).append(entry)

        if not by_name:
            raise PetDataError("The pet catalog cannot be empty")

        self._pets = ordered
        self._by_name: Mapping[str, Pet] = MappingProxyType(by_name)
        self._choices = tuple(choices)
        self._searchable = tuple(searchable)
        self._prefix_index: Mapping[str, tuple[_SearchEntry, ...]] = MappingProxyType(
            {key: tuple(entries) for key, entries in prefix_index.items()}
        )
        self._default_choices = [
            entry.choice for entry in self._searchable[:MAX_AUTOCOMPLETE_CHOICES]
        ]

    @classmethod
    def from_path(cls, path: Path) -> PetCatalog:
        """Load and validate a catalog once from a local JSON file."""
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            msg = f"Unable to load pet data from {path}: {error}"
            raise PetDataError(msg) from error

        if not isinstance(raw, list):
            raise PetDataError("Pet data must be a JSON array")

        pets: list[Pet] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                msg = f"Pet at index {index} is not a JSON object"
                raise PetDataError(msg)
            pets.append(Pet.from_mapping(cast("dict[str, object]", item)))
        return cls(pets)

    @property
    def pets(self) -> tuple[Pet, ...]:
        """Return every validated catalog entry."""
        return self._pets

    def get_calculable(self, name: str) -> Pet:
        """Return a calculable pet by exact normalized name."""
        try:
            lookup_key = normalize_pet_name(name).casefold()
        except ValueError as error:
            raise UnknownPetError("The pet name contains invalid characters") from error

        pet = self._by_name.get(lookup_key)
        if pet is None or pet.wow_factor is None:
            raise UnknownPetError("Select a pet with a known wow factor")
        return pet

    def search(self, query: str, limit: int = MAX_AUTOCOMPLETE_CHOICES) -> list[Pet]:
        """Search the in-memory index, prioritizing prefix matches."""
        if limit <= 0:
            return []
        needle = _normalized_search_query(query)
        if needle is None:
            return []
        return [entry.pet for entry in self._matching_entries(needle, limit)]

    def search_choices(
        self,
        query: str,
        limit: int = MAX_AUTOCOMPLETE_CHOICES,
    ) -> list[app_commands.Choice[str]]:
        """Return cached autocomplete choices, prioritizing prefix matches."""
        if limit <= 0:
            return []
        bounded_limit = min(limit, MAX_AUTOCOMPLETE_CHOICES)
        needle = _normalized_search_query(query)
        if needle is None:
            return []
        if not needle:
            if bounded_limit == MAX_AUTOCOMPLETE_CHOICES:
                return self._default_choices
            return self._default_choices[:bounded_limit]
        return [
            entry.choice for entry in self._matching_entries(needle, bounded_limit)
        ]

    def _matching_entries(self, needle: str, limit: int) -> tuple[_SearchEntry, ...]:
        if limit <= 0:
            return ()
        bounded_limit = min(limit, MAX_AUTOCOMPLETE_CHOICES)
        if not needle:
            return self._searchable[:bounded_limit]

        prefix = self._prefix_index.get(needle, ())
        if len(prefix) >= bounded_limit:
            return prefix[:bounded_limit]

        remaining = bounded_limit - len(prefix)
        contains: list[_SearchEntry] = []
        for entry in self._searchable:
            if entry.normalized_name.startswith(needle):
                continue
            if needle in entry.normalized_name:
                contains.append(entry)
                if len(contains) == remaining:
                    break
        return prefix + tuple(contains)


def _normalized_search_query(value: str) -> str | None:
    try:
        return normalize_pet_name(value, allow_empty=True).casefold()
    except ValueError:
        return None


def normalize_pet_name(value: str, *, allow_empty: bool = False) -> str:
    """Normalize a user-supplied pet name and reject control characters."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    if len(normalized) > MAX_PET_NAME_LENGTH:
        msg = f"Pet name cannot exceed {MAX_PET_NAME_LENGTH} characters"
        raise ValueError(msg)
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError("Pet name cannot contain control or formatting characters")
    if not normalized and not allow_empty:
        raise ValueError("Pet name cannot be empty")
    return normalized


def calculate_return_chances(left: Pet, right: Pet) -> tuple[int, int]:
    """Calculate the body return percentages used by the reference site."""
    if left.wow_factor is None or right.wow_factor is None:
        raise UnknownPetError("Both pets must have known wow factors")
    if right.exclusive:
        return 100, 0

    denominator = 22 - (left.wow_factor + right.wow_factor)
    if denominator <= 0:
        raise PetDataError("Wow factors produced an invalid probability denominator")
    left_chance = _javascript_round((11 - left.wow_factor) * 100, denominator)
    right_chance = _javascript_round((11 - right.wow_factor) * 100, denominator)
    return left_chance, right_chance


def default_data_path() -> Path:
    """Resolve the catalog path for source checkouts and containers."""
    configured = os.environ.get("WIZ_HATCH_DATA_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    working_tree_path = (Path.cwd() / "data" / "pets.json").resolve()
    if working_tree_path.is_file():
        return working_tree_path
    return (Path(__file__).resolve().parents[3] / "data" / "pets.json").resolve()


class W101Hatch(commands.Cog):
    """Expose Wizard101 pet hatching calculations as slash commands."""

    def __init__(self, bot: commands.Bot, catalog: PetCatalog) -> None:
        self.bot = bot
        self.catalog = catalog

    def _autocomplete_choices(self, current: str) -> list[app_commands.Choice[str]]:
        return self.catalog.search_choices(current)

    @app_commands.command(
        name="hatch",
        description="Calculate Wizard101 pet body return chances.",
    )
    @app_commands.describe(
        left_pet="Your pet in the left self-hatch slot",
        right_pet="The pet in the right self-hatch slot",
    )
    async def hatch(
        self,
        interaction: discord.Interaction,
        left_pet: str,
        right_pet: str,
    ) -> None:
        """Calculate return chances for two exact autocomplete selections."""
        try:
            left = self.catalog.get_calculable(left_pet)
            right = self.catalog.get_calculable(right_pet)
            left_chance, right_chance = calculate_return_chances(left, right)
        except (UnknownPetError, ValueError):
            await interaction.response.send_message(
                "Please choose both pets from the autocomplete results.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Wizard101 Pet Return Chance",
            description="Estimated body returned from this hatch.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Left slot",
            value=_pet_result(left, left_chance),
            inline=True,
        )
        embed.add_field(
            name="Right slot",
            value=_pet_result(right, right_chance),
            inline=True,
        )
        if right.exclusive:
            embed.add_field(
                name="Exclusive body rule",
                value=(
                    "An exclusive body in the right self-hatch slot cannot be "
                    "returned, so the left body is guaranteed."
                ),
                inline=False,
            )
        embed.set_footer(text="Pet data and formula: petbodyw101.vercel.app")
        await interaction.response.send_message(embed=embed)

    @hatch.autocomplete("left_pet")
    async def left_pet_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return left-slot pet suggestions from memory."""
        return self._autocomplete_choices(current)

    @hatch.autocomplete("right_pet")
    async def right_pet_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return right-slot pet suggestions from memory."""
        return self._autocomplete_choices(current)


def _required_string(
    value: Mapping[str, object],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    result = value.get(key)
    if not isinstance(result, str) or (not result and not allow_empty):
        msg = f"Expected a string for {key!r}"
        raise PetDataError(msg)
    return result


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    result = value.get(key)
    if not isinstance(result, bool):
        msg = f"Expected a boolean for {key!r}"
        raise PetDataError(msg)
    return result


def _javascript_round(numerator: int, denominator: int) -> int:
    """Match JavaScript Math.round for the positive fractions used here."""
    return (2 * numerator + denominator) // (2 * denominator)


def _pet_result(pet: Pet, chance: int) -> str:
    flags = []
    if pet.exclusive:
        flags.append("Exclusive")
    if pet.retired:
        flags.append("Retired")
    suffix = f"\n{' · '.join(flags)}" if flags else ""
    safe_name = discord.utils.escape_markdown(pet.name)
    return f"**{safe_name}**\n{chance}% return chance{suffix}"


async def setup(bot: commands.Bot) -> None:
    """Load the catalog once and register the cog."""
    catalog = PetCatalog.from_path(default_data_path())
    await bot.add_cog(W101Hatch(bot, catalog))
