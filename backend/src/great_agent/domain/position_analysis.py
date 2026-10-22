"""Position errors relative to a documented DF-float final-epoch reference."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PositionEpoch:
    sow: float
    xyz: tuple[float, float, float]


def read_flt(path: Path) -> tuple[PositionEpoch, ...]:
    epochs = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            values = tuple(float(value) for value in line.split()[:4])
            if len(values) != 4 or not all(math.isfinite(value) for value in values):
                raise ValueError("expected finite seconds-of-week and XYZ")
            if epochs and values[0] <= epochs[-1].sow:
                raise ValueError("epochs must be strictly increasing")
            epochs.append(PositionEpoch(values[0], values[1:4]))
        except ValueError as error:
            raise ValueError(f"{path.name}:{number}: {error}") from error
    if not epochs:
        raise ValueError(f"{path.name}: no position epochs")
    return tuple(epochs)


def ecef_to_enu(
    xyz: tuple[float, float, float], reference: tuple[float, float, float]
) -> tuple[float, float, float]:
    x, y, z = reference
    a = 6378137.0
    flattening = 1 / 298.257223563
    b = a * (1 - flattening)
    e2 = flattening * (2 - flattening)
    ep2 = (a * a - b * b) / (b * b)
    p = math.hypot(x, y)
    if math.hypot(p, z) < 1:
        raise ValueError("reference coordinate is at Earth's centre")
    theta = math.atan2(z * a, p * b)
    latitude = math.atan2(
        z + ep2 * b * math.sin(theta) ** 3,
        p - e2 * a * math.cos(theta) ** 3,
    )
    longitude = math.atan2(y, x)
    slat, clat = math.sin(latitude), math.cos(latitude)
    slon, clon = math.sin(longitude), math.cos(longitude)
    dx, dy, dz = (value - origin for value, origin in zip(xyz, reference, strict=True))
    return (
        -slon * dx + clon * dy,
        -slat * clon * dx - slat * slon * dy + clat * dz,
        clat * clon * dx + clat * slon * dy + slat * dz,
    )


def convergence_start(
    times: list[float], within: list[bool], hold_seconds: float, interval_seconds: float
) -> float | None:
    start = None
    previous = None
    for time, good in zip(times, within, strict=True):
        if not good:
            start = None
        else:
            if start is None or (previous is not None and time - previous > interval_seconds * 1.01):
                start = time
            if time - start >= hold_seconds:
                return start
        previous = time
    return None


def analyze_station(
    epochs: tuple[PositionEpoch, ...],
    *,
    reference: PositionEpoch | None = None,
    horizontal_threshold_m: float = 0.2,
    vertical_threshold_m: float = 0.4,
    hold_seconds: float = 600,
    interval_seconds: float = 30,
) -> dict:
    if not epochs:
        raise ValueError("no epochs to analyze")
    if (
        not all(math.isfinite(value) for value in (
            horizontal_threshold_m, vertical_threshold_m, hold_seconds, interval_seconds
        ))
        or min(horizontal_threshold_m, vertical_threshold_m, interval_seconds) <= 0
        or hold_seconds < 0
    ):
        raise ValueError("invalid convergence policy")
    reference = reference or epochs[-1]
    day_start = math.floor(epochs[0].sow / 86400) * 86400
    rows = []
    for epoch in epochs:
        east, north, up = ecef_to_enu(epoch.xyz, reference.xyz)
        horizontal = math.hypot(east, north)
        rows.append({
            "sow": epoch.sow,
            "elapsed_minutes": (epoch.sow - day_start) / 60,
            "east_m": east, "north_m": north, "up_m": up,
            "horizontal_m": horizontal,
            "horizontal_within": horizontal <= horizontal_threshold_m,
            "vertical_within": abs(up) <= vertical_threshold_m,
        })
    times = [row["sow"] for row in rows]
    horizontal_good = [row["horizontal_within"] for row in rows]
    vertical_good = [row["vertical_within"] for row in rows]
    joint_good = [h and v for h, v in zip(horizontal_good, vertical_good, strict=True)]
    starts = {
        key: convergence_start(times, good, hold_seconds, interval_seconds)
        for key, good in (
            ("horizontal", horizontal_good), ("vertical", vertical_good), ("joint", joint_good)
        )
    }

    def rms(selected: list[dict]) -> dict[str, float | None]:
        return {
            key: math.sqrt(sum(row[key] ** 2 for row in selected) / len(selected))
            if selected else None
            for key in ("east_m", "north_m", "up_m", "horizontal_m")
        }

    joint_start = starts["joint"]
    return {
        "reference_xyz_m": list(reference.xyz),
        "reference_sow": reference.sow,
        "epoch_count": len(rows),
        "first_sow": times[0], "last_sow": times[-1],
        "day_start_sow": day_start,
        "valid_epoch_fraction": len(rows) / (86400 / interval_seconds),
        **{
            f"{key}_convergence_minutes": (start - day_start) / 60 if start is not None else None
            for key, start in starts.items()
        },
        "joint_within_fraction": sum(joint_good) / len(rows),
        "rms_all_epochs_m": rms(rows),
        "rms_after_joint_convergence_m": rms([
            row for row in rows if joint_start is not None and row["sow"] >= joint_start
        ]),
        "epochs": rows,
    }
