"""Pure geometry, response, and iteration-history diagnostics for ERT."""
import math
import numbers

import numpy as np

from ert_arrays import geometry_metrics, normalize_array, validate_abmn


def _is_integral(value):
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _positive_float(value, name):
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError("%s must be a finite positive number." % name)
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("%s must be a finite positive number." % name)
    return result


def _abmn(value, count, array="wenner"):
    try:
        values = list(value)
    except TypeError:
        raise ValueError("ABMN must contain four electrode indices.")
    if len(values) != 4 or not all(_is_integral(v) for v in values):
        raise ValueError("ABMN must contain four electrode indices.")
    try:
        return validate_abmn(array, [int(v) for v in values], count)
    except ValueError as error:
        raise ValueError(str(error)) from error


def pseudosection_positions(packet, indices=None):
    """Return local four-pole midpoint and array geometry for selected rows.

    For Wenner, ``spacing_m`` is the electrode spacing ``a``.  For dipole-dipole,
    ``dipole_length_m`` is ``a``, ``separation_n`` is the integer ``gap/a`` and
    ``spacing_m`` aliases the dipole length.  None of these is a depth.
    """
    rows = packet["rows"]
    array = normalize_array(packet.get("array", "wenner"))
    count = packet.get("electrodes")
    if not _is_integral(count) or int(count) < 4:
        raise ValueError("electrodes must be an integer of at least four.")
    count = int(count)
    spacing = _positive_float(packet.get("spacing"), "spacing")

    if indices is None:
        selected = list(range(len(rows)))
    else:
        if isinstance(indices, (str, bytes, bool)):
            raise ValueError("indices must be an iterable of row indices.")
        try:
            raw_indices = list(indices)
        except TypeError:
            raise ValueError("indices must be an iterable of row indices.")
        selected = []
        seen = set()
        for value in raw_indices:
            if not _is_integral(value):
                raise ValueError("indices must contain non-negative integers.")
            index = int(value)
            if index < 0 or index >= len(rows):
                raise ValueError("Row index is out of range.")
            if index in seen:
                raise ValueError("indices must be unique.")
            seen.add(index)
            selected.append(index)

    result = {
        "indices": [],
        "center_m": [],
        "spacing_m": [],
        "rhoa": [],
        "enabled": [],
    }
    if array == "dipole-dipole":
        result.update(array=array, dipole_length_m=[], separation_n=[],
                      gap_m=[])
    for index in selected:
        row = rows[index]
        a, b, m, n = _abmn(row.get("abmn"), count, array)
        metrics = geometry_metrics(array, (a, b, m, n), spacing)
        rhoa = _positive_float(row.get("rhoa"), "rhoa")
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("Row enabled state must be bool.")
        result["indices"].append(index)
        result["center_m"].append(metrics["center_m"])
        result["spacing_m"].append(metrics["spacing_m"])
        result["rhoa"].append(rhoa)
        result["enabled"].append(enabled)
        if array == "dipole-dipole":
            result["dipole_length_m"].append(metrics["dipole_length_m"])
            result["separation_n"].append(metrics["separation_n"])
            result["gap_m"].append(metrics["gap_m"])
    return result


def _vector(values, name):
    try:
        array = np.asarray(values)
    except (TypeError, ValueError):
        raise ValueError("%s must be a non-empty 1D sequence." % name)
    if array.ndim != 1 or array.size == 0:
        raise ValueError("%s must be a non-empty 1D sequence." % name)
    if array.dtype.kind in ("b", "c"):
        raise ValueError("%s must contain real numbers." % name)
    try:
        array = array.astype(float)
    except (TypeError, ValueError):
        raise ValueError("%s must contain real numbers." % name)
    if not np.all(np.isfinite(array)) or np.any(array <= 0):
        raise ValueError("%s must contain only finite positive values." % name)
    return array


def _source_indices(source_indices, length):
    if source_indices is None:
        return list(range(length))
    if isinstance(source_indices, (str, bytes, bool)):
        raise ValueError("source_indices must contain unique non-negative integers.")
    try:
        values = list(source_indices)
    except TypeError:
        raise ValueError("source_indices must contain unique non-negative integers.")
    if len(values) != length:
        raise ValueError("source_indices length must match observations.")
    result = []
    seen = set()
    for value in values:
        if not _is_integral(value):
            raise ValueError("source_indices must contain non-negative integers.")
        index = int(value)
        if index < 0:
            raise ValueError("source_indices must be non-negative.")
        if index in seen:
            raise ValueError("source_indices must be unique.")
        seen.add(index)
        result.append(index)
    return result


def response_diagnostics(observed, response, source_indices=None):
    """Compare observed and computed responses with ordinary residuals.

    The returned RRMS is calculated here from these pairs.  It is not the
    solver's chi2, which depends on the error model and data transform.
    """
    observed_array = _vector(observed, "observed")
    response_array = _vector(response, "response")
    if observed_array.size != response_array.size:
        raise ValueError("observed and response must have equal length.")
    source = _source_indices(source_indices, observed_array.size)

    relative = (
        (response_array - observed_array) / observed_array * 100.0
    )
    rrms = float(np.sqrt(np.mean(relative ** 2)))
    max_abs = float(np.max(np.abs(relative)))
    return {
        "observed": observed_array.tolist(),
        "response": response_array.tolist(),
        "source_indices": source,
        "relative_residual_percent": relative.tolist(),
        "rrms_percent": rrms,
        "max_abs_residual_percent": max_abs,
    }


def _optional_number(value, allow_zero):
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, numbers.Real):
        return None
    result = float(value)
    if not math.isfinite(result):
        return None
    if allow_zero:
        return result if result >= 0 else None
    return result if result > 0 else None


def sanitize_history(history):
    """Return standard history records without filling or smoothing data."""
    if not isinstance(history, list):
        raise ValueError("history must be a list of dictionaries.")
    result = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        raw_iteration = entry.get("iteration")
        if isinstance(raw_iteration, bool) or not isinstance(
            raw_iteration, numbers.Real
        ):
            continue
        iteration = float(raw_iteration)
        if (
            not math.isfinite(iteration)
            or iteration < 0
            or iteration != int(iteration)
        ):
            continue
        result.append(
            {
                "iteration": int(iteration),
                "chi2": _optional_number(entry.get("chi2"), allow_zero=True),
                "rrms_percent": _optional_number(
                    entry.get("rrms_percent"), allow_zero=True
                ),
                "lam": _optional_number(entry.get("lam"), allow_zero=False),
                "phi_data": _optional_number(
                    entry.get("phi_data"), allow_zero=False
                ),
                "phi_model": _optional_number(
                    entry.get("phi_model"), allow_zero=False
                ),
            }
        )
    return result
