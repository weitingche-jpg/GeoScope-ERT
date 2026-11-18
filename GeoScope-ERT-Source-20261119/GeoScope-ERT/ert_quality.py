"""Pure quality-control helpers for observed ERT packets.

Wenner and dipole-dipole geometry are both recognised.  A negative transfer
resistance is not, by itself, a QC error: with a negative geometric factor it
produces a valid positive apparent resistivity.  Statistical outliers are only
hints; they may reflect real geological variation and are not evidence of bad
data.
"""
import copy
import math
import numbers
from datetime import datetime, timezone

import numpy as np

from ert_arrays import (geometry_metrics, normalize_array, validate_abmn)


DEFAULT_RESTORE_REASON = "恢复参与反演"
OUTLIER_NOTE = "统计离群仅为提示；可能为真实地质变化，非坏数据判定。"


def _is_integral(value):
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _finite_float(value):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _threshold(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("%s must be a finite non-negative number." % name)
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("%s must be a finite non-negative number." % name)
    return result


def _abmn(value, count, array='wenner'):
    try:
        values = list(value)
    except TypeError:
        return None
    if len(values) != 4 or not all(_is_integral(v) for v in values):
        return None
    try:
        return validate_abmn(array, [int(v) for v in values], count)
    except ValueError:
        return None


def _source_row(row, index):
    value = row.get("source_row")
    if value is None:
        return index + 1
    if _is_integral(value):
        return int(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def active_indices(packet):
    """Return enabled row indices in their original order."""
    result = []
    for index, row in enumerate(packet["rows"]):
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Row enabled state must be bool.")
        if enabled:
            result.append(index)
    return result


def quality_report(
    packet, voltage_min_mV=None, current_min_mA=None, outlier_z=4.5
):
    """Build a JSON-friendly QC report without changing the packet.

    ``center_m`` is the local midpoint of the four electrodes and
    ``spacing_m`` is the Wenner electrode spacing.  Neither is a depth.
    """
    voltage_min_mV = _threshold(voltage_min_mV, "voltage_min_mV")
    current_min_mA = _threshold(current_min_mA, "current_min_mA")
    if isinstance(outlier_z, bool) or not isinstance(outlier_z, numbers.Real):
        raise ValueError("outlier_z must be a finite positive number.")
    outlier_z = float(outlier_z)
    if not math.isfinite(outlier_z) or outlier_z <= 0:
        raise ValueError("outlier_z must be a finite positive number.")

    rows = packet["rows"]
    array = normalize_array(packet.get("array", "wenner"))
    count = packet.get("electrodes")
    if not _is_integral(count) or int(count) < 0:
        count = None
    else:
        count = int(count)
    spacing = _finite_float(packet.get("spacing"))
    if spacing is None or spacing <= 0:
        spacing = None

    records = []
    keys = []
    missing_voltage = 0
    missing_current = 0
    for index, row in enumerate(rows):
        flags = []
        abmn = _abmn(row.get("abmn"), count, array)
        center_m = None
        spacing_m = None
        metrics = None
        if abmn is None:
            flags.append("invalid_abmn")
        elif spacing is None:
            flags.append("invalid_geometry")
        else:
            metrics = geometry_metrics(array, abmn, spacing)
            center_m = metrics["center_m"]
            spacing_m = metrics["spacing_m"]
        keys.append(abmn)

        raw_enabled = row.get("enabled", True)
        if isinstance(raw_enabled, bool):
            enabled = raw_enabled
        else:
            enabled = False
            flags.append("invalid_enabled")

        raw_rhoa = row.get("rhoa")
        rhoa = _finite_float(raw_rhoa)
        if rhoa is None:
            flags.append("missing_rhoa" if raw_rhoa is None else "invalid_rhoa")
        elif rhoa <= 0:
            flags.append("invalid_rhoa")

        raw_voltage = row.get("voltage_mV")
        voltage = _finite_float(raw_voltage)
        if raw_voltage is None:
            missing_voltage += 1
            flags.append("missing_voltage")
        elif voltage is None:
            flags.append("invalid_voltage")
        elif voltage_min_mV is not None and abs(voltage) < voltage_min_mV:
            flags.append("voltage_below_threshold")

        raw_current = row.get("current_mA")
        current = _finite_float(raw_current)
        if raw_current is None:
            missing_current += 1
            flags.append("missing_current")
        elif current is None:
            flags.append("invalid_current")
        elif current_min_mA is not None and abs(current) < current_min_mA:
            flags.append("current_below_threshold")

        record = {
            "index": index,
            "source_row": _source_row(row, index),
            "enabled": enabled,
            "center_m": center_m,
            "spacing_m": spacing_m,
            "rhoa": rhoa,
            "voltage_mV": voltage,
            "current_mA": current,
            "flags": flags,
            "note": "",
        }
        if array == "dipole-dipole":
            record.update(
                array=array,
                dipole_length_m=(metrics or {}).get("dipole_length_m"),
                separation_n=(metrics or {}).get("separation_n"),
                gap_m=(metrics or {}).get("gap_m"),
                resistance=_finite_float(row.get("resistance")),
            )
        records.append(record)

    duplicate_groups = {}
    for index, key in enumerate(keys):
        if key is not None:
            duplicate_groups.setdefault(key, []).append(index)
    duplicate_count = 0
    for members in duplicate_groups.values():
        if len(members) > 1:
            duplicate_count += 1
            for index in members:
                if "duplicate_abmn" not in records[index]["flags"]:
                    records[index]["flags"].append("duplicate_abmn")

    logged = [
        (index, math.log(record["rhoa"]))
        for index, record in enumerate(records)
        if record["rhoa"] is not None and record["rhoa"] > 0
    ]
    if logged:
        values = np.asarray([item[1] for item in logged], dtype=float)
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        for index, value in logged:
            if mad > 0:
                is_outlier = 0.6744897501960817 * abs(value - median) / mad > outlier_z
            else:
                is_outlier = abs(value - median) >= math.log(10.0) - 1e-12
            if is_outlier and "outlier_rhoa" not in records[index]["flags"]:
                records[index]["flags"].append("outlier_rhoa")

    for record in records:
        notes = []
        if "invalid_abmn" in record["flags"] or "invalid_geometry" in record["flags"]:
            notes.append("ABMN或装置几何无效。")
        if "duplicate_abmn" in record["flags"]:
            notes.append("ABMN重复；未自动合并。")
        if "outlier_rhoa" in record["flags"]:
            notes.append(OUTLIER_NOTE)
        record["note"] = "".join(notes)

    total = len(records)
    active = sum(1 for record in records if record["enabled"])
    summary = {
        "total": total,
        "active": active,
        "disabled": total - active,
        "flagged": sum(1 for record in records if record["flags"]),
        "missing_voltage": missing_voltage,
        "missing_current": missing_current,
        "duplicate_groups": duplicate_count,
    }
    return {"records": records, "summary": summary}


def set_row_state(packet, indices, enabled, reason):
    """Return a deep-copied packet with selected row states changed."""
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be bool.")
    if reason is None:
        reason_text = ""
    elif isinstance(reason, str):
        reason_text = reason.strip()
    else:
        raise ValueError("reason must be a string.")
    if enabled:
        reason_text = reason_text or DEFAULT_RESTORE_REASON
    elif not reason_text:
        raise ValueError("A non-empty reason is required when disabling rows.")

    if isinstance(indices, (str, bytes, bool)):
        raise ValueError("indices must be an iterable of non-negative integers.")
    try:
        raw_indices = list(indices)
    except TypeError:
        raise ValueError("indices must be an iterable of non-negative integers.")

    rows = packet["rows"]
    selected = []
    seen = set()
    for value in raw_indices:
        if not _is_integral(value):
            raise ValueError("indices must contain non-negative integers.")
        index = int(value)
        if index < 0 or index >= len(rows):
            raise ValueError("Row index is out of range.")
        if index not in seen:
            seen.add(index)
            selected.append(index)

    changed = copy.deepcopy(packet)
    timestamp = datetime.now(timezone.utc).isoformat()
    for index in selected:
        row = changed["rows"][index]
        current_enabled = row.get("enabled", True)
        current_note = row.get("qc_note") or ""
        if current_enabled == enabled and current_note == reason_text:
            continue
        row["enabled"] = enabled
        row["qc_note"] = reason_text
        history = row.get("qc_history")
        if history is None:
            history = []
        elif not isinstance(history, list):
            history = list(history)
        history.append(
            {
                "enabled": enabled,
                "reason": reason_text,
                "timestamp": timestamp,
            }
        )
        row["qc_history"] = history
    return changed
