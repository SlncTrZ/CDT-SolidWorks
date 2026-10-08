"""Mechanical generators owned by Agent 3."""

from cdt_solidworks.mechanical.gear import (
    GEAR_DIMENSION_NAMES,
    GearValidationError,
    SpurGearParameters,
    SpurGearSpec,
    build_spur_gear_sketch,
    calculate_spur_gear,
)
from cdt_solidworks.mechanical.service import GearBuildMutationError, SpurGearBuildResult, SpurGearService

__all__ = [
    "GEAR_DIMENSION_NAMES",
    "GearValidationError",
    "SpurGearParameters",
    "SpurGearSpec",
    "build_spur_gear_sketch",
    "calculate_spur_gear",
    "GearBuildMutationError",
    "SpurGearBuildResult",
    "SpurGearService",
]
