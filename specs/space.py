"""Per-claim specification spaces.

Not every dimension applies to every claim. A claim about citywide totals has no
geography dimension; a claim stated as a raw count should not be silently
converted to a rate. The space is declared per claim, and dimensions that do not
apply are pinned to a single option rather than dropped, so the record shows the
choice was made rather than overlooked.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from specs.dimensions import get_dimension

__all__ = ["Spec", "SpecSpace", "MAX_SPECS_PER_CLAIM"]

# The cross-product of all six dimensions is 4*4*3*4*3*2 = 1152, which is both
# slow and dishonest -- most combinations are not choices anyone would defend
# together. Claims declare a subset, and this cap is a tripwire for the ones that
# forgot to.
MAX_SPECS_PER_CLAIM = 48


@dataclass(frozen=True, order=True)
class Spec:
    """One fully-determined set of analytic choices."""

    choices: tuple[tuple[str, str], ...]

    def __getitem__(self, dimension: str) -> str:
        for key, option in self.choices:
            if key == dimension:
                return option
        raise KeyError(f"spec has no dimension {dimension!r}")

    def get(self, dimension: str, default: str | None = None) -> str | None:
        try:
            return self[dimension]
        except KeyError:
            return default

    @property
    def spec_id(self) -> str:
        return "|".join(f"{k}={v}" for k, v in self.choices)

    def as_dict(self) -> dict[str, str]:
        return dict(self.choices)

    def __str__(self) -> str:
        return self.spec_id


@dataclass(frozen=True)
class SpecSpace:
    """The set of defensible specifications for one claim."""

    dimensions: tuple[tuple[str, tuple[str, ...]], ...]
    """Ordered (dimension_key, allowed_option_keys). A single-element tuple pins
    the dimension -- an explicit decision, recorded."""

    def __post_init__(self) -> None:
        if not self.dimensions:
            raise ValueError("a spec space needs at least one dimension")

        seen: set[str] = set()
        for key, options in self.dimensions:
            if key in seen:
                raise ValueError(f"dimension {key!r} declared twice")
            seen.add(key)

            dimension = get_dimension(key)
            if not options:
                raise ValueError(f"dimension {key!r} has no options selected")
            unknown = set(options) - set(dimension.option_keys)
            if unknown:
                raise ValueError(
                    f"dimension {key!r} has unknown options {sorted(unknown)}; "
                    f"have {dimension.option_keys}"
                )

        if self.size > MAX_SPECS_PER_CLAIM:
            raise ValueError(
                f"spec space has {self.size} specifications, over the cap of "
                f"{MAX_SPECS_PER_CLAIM}. Pin the dimensions that do not vary for "
                "this claim rather than taking a full cross-product."
            )

    @classmethod
    def build(cls, **dimensions: str | tuple[str, ...] | list[str]) -> SpecSpace:
        """Convenience constructor.

        ``SpecSpace.build(denominator=("none", "acs_5yr"), window="ytd_vs_ytd")``
        varies the denominator and pins the window.
        """
        normalized: list[tuple[str, tuple[str, ...]]] = []
        for key, value in dimensions.items():
            options = (value,) if isinstance(value, str) else tuple(value)
            normalized.append((key, options))
        return cls(dimensions=tuple(normalized))

    @property
    def size(self) -> int:
        count = 1
        for _, options in self.dimensions:
            count *= len(options)
        return count

    @property
    def varying_dimensions(self) -> tuple[str, ...]:
        """Dimensions with more than one option -- the ones that can flip a verdict."""
        return tuple(key for key, options in self.dimensions if len(options) > 1)

    def enumerate(self) -> list[Spec]:
        keys = [key for key, _ in self.dimensions]
        option_lists = [options for _, options in self.dimensions]
        return [
            Spec(choices=tuple(zip(keys, combination, strict=True)))
            for combination in itertools.product(*option_lists)
        ]
