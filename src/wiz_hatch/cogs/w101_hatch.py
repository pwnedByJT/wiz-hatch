"""Wizard101 pet body return chance slash command."""

from __future__ import annotations

import json
import math
import os
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands

MAX_AUTOCOMPLETE_CHOICES: Final = 25
MAX_PET_NAME_LENGTH: Final = 100
MAX_WIKI_QUERY_LENGTH: Final = 200
SOURCE_URL: Final = "https://petbodyw101.vercel.app/"
WIKI_BASE_URL: Final = "https://wiki.wizard101central.com/wiki/"
PET_LOCATOR_URL: Final = f"{WIKI_BASE_URL}Special:RunQuery/Pet_Locator"
PET_STAT_CALCULATOR_URL: Final = f"{WIKI_BASE_URL}Special:RunQuery/Pet_Stat_Calculator"
PET_TALENT_LOCATOR_URL: Final = f"{WIKI_BASE_URL}Special:RunQuery/Pet_Talent_Locator"
PET_JEWEL_LOCATOR_URL: Final = f"{WIKI_BASE_URL}Special:RunQuery/Pet_Jewel_Locator"
SNACK_FINDER_URL: Final = f"{WIKI_BASE_URL}Special:RunQuery/Snack_Finder"
WIKI_CATEGORIES: Final = (
    "Pet",
    "ItemCard",
    "Spell",
    "Creature",
    "Item",
    "Recipe",
    "Search",
)


class PetDataError(ValueError):
    """Raised when the checked-in pet catalog is invalid."""


class UnknownPetError(ValueError):
    """Raised when a supplied pet name is absent or cannot be calculated."""


def pet_wiki_url(pet_name: str) -> str:
    """Build a canonical Wizard101 Central Wiki pet URL."""
    slug = quote(pet_name.replace(" ", "_"), safe="()_-")
    return f"{WIKI_BASE_URL}Pet:{slug}"


def wiki_search_url(query: str) -> str:
    """Build a safely encoded Wizard101 Central Wiki search URL."""
    return f"{WIKI_BASE_URL}Special:Search?search={quote(query, safe='')}"


def wiki_category_url(category: str) -> str:
    """Build a safely encoded Wizard101 Central Wiki category URL."""
    return f"{WIKI_BASE_URL}Category:{quote(category, safe='()_-')}"


@dataclass(frozen=True, slots=True)
class HatchOdds:
    """Cumulative odds and confidence counts for one return probability."""

    cumulative: tuple[float, float, float]
    confidence_hatches: tuple[int, int, int]


def cumulative_hatch_probability(probability: float, hatches: int) -> float:
    """Return the probability of at least one success in ``hatches`` tries."""
    _validate_probability(probability)
    if hatches <= 0:
        raise ValueError("hatches must be a positive integer")
    return 1 - (1 - probability) ** hatches


def hatches_for_confidence(probability: float, target: float) -> int:
    """Return the minimum hatches needed to reach a cumulative target."""
    _validate_probability(probability)
    if not 0 < target < 1:
        raise ValueError("target must be between 0 and 1")
    return math.ceil(math.log(1 - target) / math.log(1 - probability))


def multi_hatch_odds(probability: float) -> HatchOdds:
    """Calculate the requested multi-hatch and confidence breakdown."""
    return HatchOdds(
        cumulative=(
            cumulative_hatch_probability(probability, 3),
            cumulative_hatch_probability(probability, 5),
            cumulative_hatch_probability(probability, 10),
        ),
        confidence_hatches=(
            hatches_for_confidence(probability, 0.5),
            hatches_for_confidence(probability, 0.75),
            hatches_for_confidence(probability, 0.9),
        ),
    )


def _validate_probability(probability: float) -> None:
    if not 0 < probability < 1:
        raise ValueError("probability must be between 0 and 1")


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

    @property
    def wiki_url(self) -> str:
        """Return the pet's canonical Wizard101 Central Wiki URL."""
        return pet_wiki_url(self.name)

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

    def get(self, name: str) -> Pet:
        """Return a pet by exact normalized name, including unknown wow factors."""
        try:
            lookup_key = normalize_pet_name(name).casefold()
        except ValueError as error:
            raise UnknownPetError("The pet name contains invalid characters") from error

        pet = self._by_name.get(lookup_key)
        if pet is None:
            raise UnknownPetError("Select a pet from the autocomplete results")
        return pet

    def get_calculable(self, name: str) -> Pet:
        """Return a calculable pet by exact normalized name."""
        pet = self.get(name)
        if pet.wow_factor is None:
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

    def autocomplete(
        self,
        query: str,
        limit: int = MAX_AUTOCOMPLETE_CHOICES,
    ) -> list[app_commands.Choice[str]]:
        """Return cached autocomplete choices without I/O."""
        return self.search_choices(query, limit)

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
        return [entry.choice for entry in self._matching_entries(needle, bounded_limit)]

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


@dataclass(frozen=True, slots=True)
class StatCalculation:
    """One talent's exact percentage and displayed rounded percentage."""

    unrounded: Decimal
    rounded: int


def calculate_pet_stats(
    strength: int = 255,
    intellect: int = 250,
    agility: int = 260,
    will: int = 260,
    power: int = 250,
    mighty_or_thinkin_cap: bool = False,
) -> dict[str, StatCalculation]:
    """Calculate Wizard101 Pet 2.0 talent percentages."""
    for name, value in (
        ("strength", strength),
        ("intellect", intellect),
        ("agility", agility),
        ("will", will),
        ("power", power),
    ):
        if isinstance(value, bool) or not 0 <= value <= 350:
            raise ValueError(f"{name} must be an integer from 0 through 350")

    effective_strength = strength + 65 if mighty_or_thinkin_cap else strength
    school_numerator = 2 * effective_strength + 2 * will + power
    spell_numerator = 2 * effective_strength + 2 * agility + power
    formulas = (
        ("School-Dealer", school_numerator, 125),
        ("School-Giver / Pain-Giver", school_numerator, 200),
        ("School-Boon / Pain-Boon", school_numerator, 400),
        ("Spell-Proof", spell_numerator, 125),
        ("Spell-Defying", spell_numerator, 250),
        ("Armor Breaker", spell_numerator, 400),
        ("Armor Piercer", spell_numerator, 625),
    )
    return {
        name: _stat_calculation(numerator, denominator)
        for name, numerator, denominator in formulas
    }


def _stat_calculation(numerator: int, denominator: int) -> StatCalculation:
    unrounded = Decimal(numerator) / Decimal(denominator)
    rounded = int(unrounded.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN))
    return StatCalculation(unrounded, rounded)


def _format_stat_calculation(calculation: StatCalculation) -> str:
    return f"{calculation.unrounded:f}% → {calculation.rounded}%"


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
    """Expose Wizard101 pet hatching and Wiki utilities as slash commands."""

    def __init__(self, bot: commands.Bot, catalog: PetCatalog) -> None:
        self.bot = bot
        self.catalog = catalog

    def _autocomplete_choices(self, current: str) -> list[app_commands.Choice[str]]:
        return self.catalog.autocomplete(current)

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
        if 0 < right_chance < 100:
            embed.add_field(
                name="Right-slot cumulative odds",
                value=_format_multi_hatch_breakdown(right_chance / 100),
                inline=False,
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

    @app_commands.command(
        name="pet",
        description="Inspect a Wizard101 pet body and pairing odds.",
    )
    @app_commands.describe(pet_name="The pet body to inspect")
    async def pet(self, interaction: discord.Interaction, pet_name: str) -> None:
        """Show a pet's body information and WF 10 base return odds."""
        try:
            pet = self.catalog.get(pet_name)
        except UnknownPetError:
            await interaction.response.send_message(
                "Please choose a pet from the autocomplete results.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"Wizard101 Pet: {pet.name}",
            description=(
                f"[{_escaped_link_name(pet.name)}]({pet.wiki_url}) · "
                f"[Pet Locator]({PET_LOCATOR_URL}) · "
                f"[Pet Stat Calculator]({PET_STAT_CALCULATOR_URL})"
            ),
            color=discord.Color.green(),
        )
        wow_factor = "Unknown" if pet.wow_factor is None else str(pet.wow_factor)
        embed.add_field(
            name="Body",
            value=(
                f"Wow Factor: {wow_factor}/10\n"
                f"Exclusive: {'Yes' if pet.exclusive else 'No'}"
            ),
            inline=True,
        )

        if pet.wow_factor is None:
            odds = "Return odds unavailable because this pet has no known Wow Factor."
        elif pet.exclusive:
            odds = (
                "Exact right-slot return: **0%**\n"
                "This pet is Exclusive; it returns from the right slot only if "
                "you already own it."
            )
        else:
            _, right_chance = calculate_return_chances(_wf10_base_pet(), pet)
            odds = (
                f"Exact right-slot return: **{right_chance}%**\n"
                f"{_format_confidence_hatches(right_chance / 100)}"
            )
        embed.add_field(
            name="WF 10 Kiosk/Sticky Base pairing",
            value=odds,
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @pet.autocomplete("pet_name")
    async def pet_name_autocomplete(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Return pet inspector suggestions from the in-memory index."""
        return self._autocomplete_choices(current)

    @app_commands.command(
        name="stats",
        description="Calculate Wizard101 Pet 2.0 talent rounding.",
    )
    @app_commands.describe(
        strength="Strength stat (0-350)",
        intellect="Intellect stat (0-350)",
        agility="Agility stat (0-350)",
        will="Will stat (0-350)",
        power="Power stat (0-350)",
        mighty_or_thinkin_cap="Add Mighty's +65 Strength cap bonus",
    )
    async def stats(
        self,
        interaction: discord.Interaction,
        strength: app_commands.Range[int, 0, 350] = 255,
        intellect: app_commands.Range[int, 0, 350] = 250,
        agility: app_commands.Range[int, 0, 350] = 260,
        will: app_commands.Range[int, 0, 350] = 260,
        power: app_commands.Range[int, 0, 350] = 250,
        mighty_or_thinkin_cap: bool = False,
    ) -> None:
        """Calculate exact and displayed talent values without network I/O."""
        calculations = calculate_pet_stats(
            strength,
            intellect,
            agility,
            will,
            power,
            mighty_or_thinkin_cap,
        )
        embed = discord.Embed(
            title="Wizard101 Pet 2.0 Max Stat Calculator",
            description=(
                "Exact values use the supplied stats; displayed values use "
                "half-to-even rounding."
            ),
            color=discord.Color.gold(),
        )
        for name, calculation in calculations.items():
            embed.add_field(
                name=name,
                value=_format_stat_calculation(calculation),
                inline=True,
            )
        effective_strength = strength + 65 if mighty_or_thinkin_cap else strength
        embed.add_field(
            name="Input stats",
            value=(
                f"STR {effective_strength} · INT {intellect} · AGI {agility} · "
                f"WIL {will} · POW {power}"
            ),
            inline=False,
        )
        cap_note = (
            "Mighty +65 STR enabled" if mighty_or_thinkin_cap else "Standard caps"
        )
        embed.add_field(name="Cap mode", value=cap_note, inline=False)
        embed.add_field(
            name="Reference",
            value=f"[Pet Stat Calculator]({PET_STAT_CALCULATOR_URL})",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="wiki",
        description="Open Wizard101 Central Wiki search and locator links.",
    )
    @app_commands.describe(
        query="A pet name or Wiki search query",
        category="Optional Wiki category to open",
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name=category, value=category)
            for category in WIKI_CATEGORIES
        ]
    )
    async def wiki(
        self,
        interaction: discord.Interaction,
        query: str,
        category: str = "Search",
    ) -> None:
        """Provide a direct pet page when the query exactly matches a catalog pet."""
        try:
            normalized_query = _normalize_wiki_query(query)
        except ValueError:
            await interaction.response.send_message(
                "The Wiki query must contain 1-200 visible characters.",
                ephemeral=True,
            )
            return

        if category not in WIKI_CATEGORIES:
            category = "Search"
        escaped_query = discord.utils.escape_markdown(normalized_query)
        links = [f"[Search results]({wiki_search_url(normalized_query)})"]
        if category == "Search":
            links.append("[Wiki home](https://wiki.wizard101central.com/wiki/)")
        else:
            links.append(f"[Category: {category}]({wiki_category_url(category)})")

        embed = discord.Embed(
            title="Wizard101 Central Wiki Navigator",
            description=f"Query: **{escaped_query}**\n" + " · ".join(links),
            color=discord.Color.blurple(),
        )
        try:
            matched_pet = self.catalog.get(normalized_query)
        except UnknownPetError:
            matched_pet = None
        if matched_pet is not None:
            embed.add_field(
                name="Known pet",
                value=(
                    f"[{_escaped_link_name(matched_pet.name)}]({matched_pet.wiki_url})"
                ),
                inline=False,
            )
        embed.add_field(
            name="Wiki locators",
            value=(
                f"[Pet Locator]({PET_LOCATOR_URL}) · "
                f"[Pet Talent Locator]({PET_TALENT_LOCATOR_URL}) · "
                f"[Pet Jewel Locator]({PET_JEWEL_LOCATOR_URL}) · "
                f"[Snack Finder]({SNACK_FINDER_URL})"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)


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


def _wf10_base_pet() -> Pet:
    return Pet(
        name="WF 10 Kiosk/Sticky Base",
        school="Any",
        wow_factor=10,
        egg_name="",
        exclusive=False,
        unhatchable=False,
        retired=False,
        special_body=False,
    )


def _normalize_wiki_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized or len(normalized) > MAX_WIKI_QUERY_LENGTH:
        raise ValueError("Wiki query must contain 1-200 characters")
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise ValueError("Wiki query cannot contain control characters")
    return normalized


def _escaped_link_name(name: str) -> str:
    return discord.utils.escape_markdown(name)


def _format_confidence_hatches(probability: float) -> str:
    odds = multi_hatch_odds(probability)
    return (
        f"50% confidence: **{odds.confidence_hatches[0]} hatches**\n"
        f"90% confidence: **{odds.confidence_hatches[2]} hatches**"
    )


def _format_multi_hatch_breakdown(probability: float) -> str:
    odds = multi_hatch_odds(probability)
    return (
        f"3 hatches: {odds.cumulative[0] * 100:.2f}%\n"
        f"5 hatches: {odds.cumulative[1] * 100:.2f}%\n"
        f"10 hatches: {odds.cumulative[2] * 100:.2f}%\n"
        f"50% confidence: {odds.confidence_hatches[0]} hatches · "
        f"90% confidence: {odds.confidence_hatches[2]} hatches"
    )


def _pet_result(pet: Pet, chance: int) -> str:
    flags = []
    if pet.exclusive:
        flags.append("Exclusive")
    if pet.retired:
        flags.append("Retired")
    suffix = f"\n{' · '.join(flags)}" if flags else ""
    safe_name = _escaped_link_name(pet.name)
    return f"**[{safe_name}]({pet.wiki_url})**\n{chance}% return chance{suffix}"


async def setup(bot: commands.Bot) -> None:
    """Load the catalog once and register the cog."""
    catalog = PetCatalog.from_path(default_data_path())
    await bot.add_cog(W101Hatch(bot, catalog))
