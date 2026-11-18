"""Shared electrode-array geometry and signed geometric factors.

This module is deliberately free of Qt and pyGIMLi imports.  Both the observed
data packet layer and the inversion preparation use the same definitions so a
negative transfer resistance can never be "repaired" by sorting electrode roles
or by taking an absolute value.

Arrays supported in schema 1:

* ``wenner``          A--M--N--B with equal electrode gaps (legacy)
* ``dipole-dipole``   equal, disjoint A/B and M/N dipoles on a uniform line

The dipole-dipole convention used here stores ``abmn`` in solver order
(``[A, B, M, N]``).  The signed geometric factor is

    K = 2*pi / (1/rAM - 1/rAN - 1/rBM + 1/rBN)

and the apparent resistivity is ``rhoa = K * R``.  A measured negative
resistance is valid when K is also negative.
"""
import math

ARRAYS = ('wenner', 'dipole-dipole')
ARRAY_LABELS = {
    'wenner': '温纳 A–M–N–B',
    'dipole-dipole': '偶极—偶极 A–B–M–N',
}
_ARRAY_ALIASES = {
    'wenner': 'wenner',
    'wenner-alpha': 'wenner',
    '温纳': 'wenner',
    'dipole-dipole': 'dipole-dipole',
    'dipole_dipole': 'dipole-dipole',
    'dipole': 'dipole-dipole',
    'dd': 'dipole-dipole',
    '偶极—偶极': 'dipole-dipole',
    '偶极-偶极': 'dipole-dipole',
}


def normalize_array(array):
    """Return the canonical array name or raise a user-facing error."""
    if array is None:
        return 'wenner'
    key = str(array).strip().lower()
    if key not in _ARRAY_ALIASES:
        raise ValueError('仅支持温纳（wenner）或偶极—偶极（dipole-dipole）排列。')
    return _ARRAY_ALIASES[key]


def array_label(array):
    return ARRAY_LABELS[normalize_array(array)]


def _integer(value, name='电极编号'):
    if isinstance(value, bool):
        raise ValueError(f'{name}须为整数。')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{name}须为整数。') from None
    if not math.isfinite(number) or number != int(number):
        raise ValueError(f'{name}须为整数。')
    return int(number)


def _abmn(abmn):
    try:
        values = list(abmn)
    except TypeError:
        raise ValueError('ABMN 须包含四个电极编号。') from None
    if len(values) != 4:
        raise ValueError('ABMN 须包含四个电极编号。')
    return tuple(_integer(value) for value in values)


def validate_abmn(array, abmn, count=None):
    """Validate one quadrupole and return ``(a, b, m, n)`` as integers.

    ``count`` is the number of electrodes when known.  All four indices are
    checked independently; roles are never sorted to make a bad geometry fit.
    """
    array = normalize_array(array)
    a, b, m, n = _abmn(abmn)
    indices = (a, b, m, n)
    if any(value < 0 for value in indices):
        raise ValueError('电极编号不能为负。')
    if count is not None:
        count = _integer(count, '电极数量')
        if count < 4:
            raise ValueError('电极数量至少为4。')
        if any(value >= count for value in indices):
            raise ValueError('电极编号超出电极数量范围。')
    if array == 'wenner':
        if not (a < m < n < b and m - a == n - m == b - n):
            raise ValueError('电极排列不符合等间隔 A–M–N–B 温纳装置。')
        return a, b, m, n

    ab_length = abs(b - a)
    mn_length = abs(n - m)
    if ab_length < 1 or mn_length < 1 or ab_length != mn_length:
        raise ValueError('偶极—偶极的 AB 与 MN 须为等长且长度至少为1个电极间距。')
    ab_low, ab_high = sorted((a, b))
    mn_low, mn_high = sorted((m, n))
    gap = max(ab_low, mn_low) - min(ab_high, mn_high)
    if gap < 1:
        raise ValueError('偶极—偶极的 AB 与 MN 不能共用或跨越电极，须为不相交偶极。')
    if gap % ab_length:
        raise ValueError('偶极—偶极的隔离间距 gap 须为偶极长度 a 的整数倍且至少为1个 a。')
    if gap // ab_length < 1:
        raise ValueError('偶极—偶极的隔离系数 n=gap/a 须为至少1的整数。')
    return a, b, m, n


def validate_geometry(array, abmn, count=None):
    """Alias used by callers that validate geometry without data values."""
    return validate_abmn(array, abmn, count)


def classify_array(abmn, count=None):
    """Return the unique canonical array matching one ABMN quadrupole.

    Only the existing geometry validators decide the result.  Electrode roles
    are never sorted, and no filename, value sign or majority vote is used.
    A quadrupole that matches no supported array, or more than one, raises
    ``ValueError`` instead of guessing.
    """
    matches = []
    failures = []
    for array in ARRAYS:
        try:
            validate_abmn(array, abmn, count)
        except ValueError as error:
            failures.append((array, str(error)))
        else:
            matches.append(array)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = '、'.join(array_label(array) for array in matches)
        raise ValueError(f'电极排列同时符合{names}，无法唯一识别。')
    detail = '；'.join(f'{array_label(array)}：{message}'
                      for array, message in failures)
    raise ValueError(f'未能识别为温纳或偶极—偶极排列（{detail}）。')


def geometric_factor(abmn, spacing):
    """Return the signed geometric factor for four electrode indices.

    Electrodes are on a uniform line at ``index * spacing``.  The formula is
    intentionally signed: swapping only A/B (or only M/N) changes the sign of
    K, as does a reversed measurement polarity.
    """
    a, b, m, n = _abmn(abmn)
    if any(value < 0 for value in (a, b, m, n)):
        raise ValueError('电极编号不能为负。')
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('电极间距须为有限正值。')
    xa, xb, xm, xn = (value * spacing for value in (a, b, m, n))
    ram = abs(xa - xm)
    ran = abs(xa - xn)
    rbm = abs(xb - xm)
    rbn = abs(xb - xn)
    if min(ram, ran, rbm, rbn) <= 0:
        raise ValueError('电极角色重合，无法计算几何因子。')
    denominator = 1.0 / ram - 1.0 / ran - 1.0 / rbm + 1.0 / rbn
    if not math.isfinite(denominator) or abs(denominator) < 1e-15:
        raise ValueError('几何因子分母为零，电极排列退化。')
    factor = 2.0 * math.pi / denominator
    if not math.isfinite(factor) or factor == 0:
        raise ValueError('几何因子无效。')
    return factor


# The longer explicit name is used by the packet layer and by reviewers.
signed_geometric_factor = geometric_factor


def geometry_metrics(array, abmn, spacing):
    """Return plotting/validation geometry for one quadrupole.

    ``center_m`` is the midpoint of the outer two electrodes (the four-pole
    centre), never a depth.  For dipole-dipole, ``dipole_length_m`` is ``a``,
    ``separation_n`` is the integer ``gap/a`` and ``spacing_m`` is an alias of
    the dipole length for older plotting callers.  For Wenner, ``spacing_m`` is
    the classical electrode spacing ``a``.
    """
    array = normalize_array(array)
    a, b, m, n = validate_abmn(array, abmn)
    spacing = float(spacing)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('电极间距须为有限正值。')
    indices = (a, b, m, n)
    center = (min(indices) + max(indices)) * spacing / 2.0
    if array == 'wenner':
        return dict(array=array, center_m=center, spacing_m=(m - a) * spacing,
                    dipole_length_m=None, separation_n=None, gap_m=None,
                    ab_cells=abs(b - a), mn_cells=abs(n - m))
    ab_cells = abs(b - a)
    mn_cells = abs(n - m)
    ab_low, ab_high = sorted((a, b))
    mn_low, mn_high = sorted((m, n))
    gap_cells = max(ab_low, mn_low) - min(ab_high, mn_high)
    dipole_length = ab_cells * spacing
    return dict(array=array, center_m=center, spacing_m=dipole_length,
                dipole_length_m=dipole_length,
                separation_n=gap_cells // ab_cells,
                gap_m=gap_cells * spacing,
                ab_cells=ab_cells, mn_cells=mn_cells, gap_cells=gap_cells)


def separation_factor(abmn, spacing):
    """Return integer ``gap/a`` for a dipole-dipole quadrupole."""
    return geometry_metrics('dipole-dipole', abmn, spacing)['separation_n']


def apparent_resistivity(abmn, spacing, resistance):
    """Return signed-K apparent resistivity ``K*R`` without taking abs()."""
    resistance = float(resistance)
    if not math.isfinite(resistance):
        raise ValueError('电阻须为有限数值。')
    return geometric_factor(abmn, spacing) * resistance


def rhoa_from_resistance(abmn, spacing, resistance):
    return apparent_resistivity(abmn, spacing, resistance)


def dd_plot_levels(lengths, separations):
    """Separate (a,n) groups when multiple dipole lengths share a centre."""
    pairs=list(zip(lengths,separations))
    if len(set(lengths))<=1:
        ticks=sorted(set(separations))
        return list(separations),ticks,[str(int(n)) for n in ticks],False
    groups=sorted(set(pairs))
    levels={pair:i+1 for i,pair in enumerate(groups)}
    return ([levels[pair] for pair in pairs],list(range(1,len(groups)+1)),
            [f'a={a:g} m, n={int(n)}' for a,n in groups],True)
