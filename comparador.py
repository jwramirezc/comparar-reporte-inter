"""Validaciones del reporte de trazabilidad (CSV)."""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from functools import lru_cache
from typing import Any

import holidays
import pandas as pd

VALIDATION_RULES: list[str] = [
    "Fecha inicio = Fecha de inicio planeada*",
    "Duración en horas* = Duración (1 día = 10 h hábiles; '## Horas' → ##.0)",
    "Vencimiento = Fecha inicio + Duración (días/horas hábiles CO; jornada 8:00–18:00)",
    "Vencimiento = Fecha de fin planeada*",
    "Fecha de fin planeada* = Fecha de inicio planeada* + Duración en horas* (horas hábiles)",
    "Tiempo utilizado (calendario)* = Fecha final − Fecha inicio (omitida si Fecha final vacía)",
    "Tiempo utilizado = Tiempo utilizado (hábil)* (comparación HH:MM; '1 día' = 10 h; "
    "omitida si Fecha final vacía)",
    "Tiempo total utilizado en días* = Tiempo utilizado (hábil)* / 10 "
    "(omitida si Fecha final vacía)",
]

DISPLAY_ID_COLUMNS = ["ID", "NOMBRE FLUJO", "CONSECUTIVO", "NOMBRE ACTIVIDAD"]

COMPARISON_LABELS_ORDER: list[str] = [
    "Fecha inicio = Inicio planeada*",
    "Duración en horas* = Duración",
    "Vencimiento = Inicio + Duración",
    "Vencimiento = Fecha fin planeada*",
    "Fin planeada* = Inicio planeada* + Duración*",
    "Tiempo calendario* = Fin − Inicio",
    "Tiempo utilizado = Hábil* (HH:MM)",
    "Días totales* = Hábil* / 10",
]

RULES_REQUIRING_FECHA_FINAL: list[tuple[str, str, str]] = [
    (
        "Tiempo calendario* = Fin − Inicio",
        "TIEMPO UTILIZADO (CALENDARIO)*",
        "FECHA FINAL − FECHA INICIO",
    ),
    (
        "Tiempo utilizado = Hábil* (HH:MM)",
        "TIEMPO UTILIZADO",
        "TIEMPO UTILIZADO (HÁBIL)*",
    ),
    (
        "Días totales* = Hábil* / 10",
        "TIEMPO TOTAL UTILIZADO EN DÍAS*",
        "TIEMPO UTILIZADO (HÁBIL)* / 10",
    ),
]

REQUIRED_COLUMNS = [
    "FECHA INICIO",
    "FECHA DE INICIO PLANEADA*",
    "DURACIÓN",
    "DURACIÓN EN HORAS*",
    "FECHA FINAL",
    "VENCIMIENTO",
    "FECHA DE FIN PLANEADA*",
    "TIEMPO UTILIZADO",
    "TIEMPO UTILIZADO (HÁBIL)*",
    "TIEMPO UTILIZADO (CALENDARIO)*",
    "TIEMPO TOTAL UTILIZADO EN DÍAS*",
]

BUSINESS_START = time(8, 0)
BUSINESS_END = time(18, 0)
HOURS_PER_BUSINESS_DAY = 10.0  # ventana 8:00–18:00 = 10 h hábiles
HOURS_PER_DURATION_DAY = 10.0  # 1 día en columna Duración = 10 h hábiles


def normalize_header(name: str) -> str:
    return " ".join(str(name).strip().split()).upper()


def align_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {c: normalize_header(c) for c in df.columns}
    return df.rename(columns=mapping)


def _is_empty(val: Any) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    s = str(val).strip()
    return s == "" or s.lower() in ("nan", "none", "nat")


def parse_datetime(val: Any) -> datetime | None:
    if _is_empty(val):
        return None
    s = str(val).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(s[:19] if len(s) > 10 else s, fmt)
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=False).to_pydatetime()
    except Exception:
        return None


@lru_cache(maxsize=8)
def _colombia_holidays(year_from: int, year_to: int) -> holidays.HolidayBase:
    return holidays.Colombia(years=range(year_from, year_to + 1))


def _holiday_set(start: datetime, end: datetime) -> holidays.HolidayBase:
    return _colombia_holidays(start.year, end.year)


def is_business_day(day: datetime.date, co_holidays: holidays.HolidayBase) -> bool:
    return day.weekday() < 5 and day not in co_holidays


def duration_to_hours(val: Any) -> float | None:
    """'## Horas' → ##.0; '## días' → ## × 10 horas hábiles (1 día = 10 h)."""
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)) and not pd.isna(val):
        return round(float(val), 2)

    s = str(val).strip().lower().replace(",", ".")
    m_horas = re.match(r"^(\d+(?:\.\d+)?)\s*horas?\s*$", s)
    if m_horas:
        return round(float(m_horas.group(1)), 2)
    m_dias = re.match(r"^(\d+(?:\.\d+)?)\s*d[ií]as?\s*$", s)
    if m_dias:
        return round(float(m_dias.group(1)) * HOURS_PER_DURATION_DAY, 2)
    return None


def format_duration_hours_display(val: Any, hours: float | None) -> str:
    if hours is None:
        return ""
    return f"{val} → {hours:.1f}"


def format_hours_value(hours: float | None) -> str:
    if hours is None:
        return ""
    return f"{hours:.1f}"


def parse_duration_star_hours(val: Any) -> float | None:
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)) and not pd.isna(val):
        return float(val)
    s = str(val).strip().lower()
    m = re.match(r"^(\d+):(\d{2}):(\d{2})$", s)
    if m:
        h, mi, se = map(int, m.groups())
        return h + mi / 60 + se / 3600
    num = re.search(r"([\d.]+)", s)
    return float(num.group(1)) if num else None


def duration_to_components(dur_val: Any) -> tuple[int, float] | None:
    """
    Descompone Duración en (días hábiles completos, horas hábiles restantes).
    - 'N días' → (N, 0)
    - 'H Horas' → (H//10, H%10)  (1 día = 10 h hábiles)
    """
    if _is_empty(dur_val):
        return None

    s = str(dur_val).strip().lower().replace(",", ".")
    m_dias = re.match(r"^(\d+(?:\.\d+)?)\s*d[ií]as?\s*$", s)
    if m_dias:
        return int(round(float(m_dias.group(1)))), 0.0

    m_horas = re.match(r"^(\d+(?:\.\d+)?)\s*horas?\s*$", s)
    if m_horas:
        h = float(m_horas.group(1))
        whole = int(h // HOURS_PER_DURATION_DAY)
        rem = h % HOURS_PER_DURATION_DAY
        return whole, rem

    hours = duration_to_hours(dur_val)
    if hours is None:
        return None
    return int(hours // HOURS_PER_DURATION_DAY), hours % HOURS_PER_DURATION_DAY


def add_business_days(start: datetime, days: int) -> datetime:
    """Avanza N días hábiles (Colombia) manteniendo la hora de inicio."""
    if days <= 0:
        return start
    co_holidays = _holiday_set(start, start + timedelta(days=max(30, days * 4)))
    d = start.date()
    added = 0
    while added < days:
        d += timedelta(days=1)
        if is_business_day(d, co_holidays):
            added += 1
    return datetime.combine(d, start.time())


def _next_business_morning(dt: datetime) -> datetime:
    co_holidays = _holiday_set(dt, dt + timedelta(days=14))
    d = dt.date() + timedelta(days=1)
    while not is_business_day(d, co_holidays):
        d += timedelta(days=1)
    return datetime.combine(d, BUSINESS_START)


def _clamp_to_business_window(dt: datetime) -> datetime:
    """Si está fuera de jornada, lleva al inicio del mismo día hábil o al siguiente."""
    co_holidays = _holiday_set(dt, dt + timedelta(days=14))
    if not is_business_day(dt.date(), co_holidays):
        return _next_business_morning(datetime.combine(dt.date(), BUSINESS_START))
    day_start = datetime.combine(dt.date(), BUSINESS_START)
    day_end = datetime.combine(dt.date(), BUSINESS_END)
    if dt < day_start:
        return day_start
    if dt >= day_end:
        return _next_business_morning(dt)
    return dt


def add_business_hours(start: datetime, hours: float) -> datetime:
    """
    Suma horas hábiles dentro de la jornada 8:00–18:00 (Colombia).
    El excedente pasa al siguiente día hábil desde las 8:00.
    """
    if hours <= 0:
        return start

    current = _clamp_to_business_window(start)
    remaining = hours * 3600.0

    while remaining > 1e-6:
        co_holidays = _holiday_set(current, current + timedelta(days=30))
        if not is_business_day(current.date(), co_holidays):
            current = _next_business_morning(current)
            continue

        day_end = datetime.combine(current.date(), BUSINESS_END)
        available = max(0.0, (day_end - current).total_seconds())
        if available <= 0:
            current = _next_business_morning(current)
            continue

        step = min(remaining, available)
        current += timedelta(seconds=step)
        remaining -= step

        if remaining > 1e-6 and current >= day_end:
            current = _next_business_morning(current)

    return current


def vencimiento_from_inicio_duracion(inicio: datetime, dur_val: Any) -> datetime | None:
    """
    Vencimiento = Inicio + Duración en lógica hábil colombiana.

    - Bloques de 1 día hábil (N días o cada 8 h): avanza N días hábiles
      manteniendo la hora (ej. viernes 09:53 + 1 día → lunes 09:53).
    - Horas restantes (< 8): se suman dentro de la jornada 8:00–18:00
      con arrastre al siguiente día hábil si se excede.
    """
    components = duration_to_components(dur_val)
    if components is None:
        return None

    biz_days, extra_hours = components
    result = add_business_days(inicio, biz_days) if biz_days else inicio
    if extra_hours:
        result = add_business_hours(result, extra_hours)
    return result


def format_vencimiento_calc_note(dur_val: Any) -> str:
    components = duration_to_components(dur_val)
    if components is None:
        return ""
    biz_days, extra_hours = components
    parts: list[str] = []
    if biz_days:
        parts.append(f"+ {biz_days} día(s) hábil(es)")
    if extra_hours:
        parts.append(f"+ {extra_hours:g} h hábil(es)")
    return " ".join(parts)


def parse_time_span_to_minutes(val: Any) -> float | None:
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)) and not pd.isna(val):
        return float(val) * 24 * 60

    s = str(val).strip().lower()
    m = re.match(r"^(\d+):(\d{2}):(\d{2})$", s)
    if m:
        h, mi, se = map(int, m.groups())
        return h * 60 + mi + se / 60

    days = hours = minutes = 0.0
    for part, unit in re.findall(
        r"(\d+)\s*(días|dias|día|dia|horas|hora|h|minutos|minuto|min)", s
    ):
        n = float(part)
        if unit.startswith("d"):
            days = n
        elif unit.startswith("h") or unit == "hora":
            hours = n
        else:
            minutes = n
    if days or hours or minutes:
        return days * 24 * 60 + hours * 60 + minutes

    num = re.search(r"([\d.]+)", s)
    return float(num.group(1)) if num else None


def format_minutes_as_span(minutes: float) -> str:
    total_min = int(round(minutes))
    days, rem = divmod(total_min, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days} días")
    if hours:
        parts.append(f"{hours} horas")
    if mins or not parts:
        parts.append(f"{mins} minutos")
    return " ".join(parts)


def parse_business_time_to_minutes(val: Any) -> float | None:
    """
    Parsea tiempo hábil. Soporta dos formatos:
    - 'X días Y horas Z minutos'  → 1 día = 10 h hábiles
    - 'HH:MM:SS' o 'HH:MM'        → HH horas + MM minutos (segundos truncados)
    """
    if _is_empty(val):
        return None
    if isinstance(val, (int, float)) and not pd.isna(val):
        return float(val) * 60  # se asume valor en horas

    s = str(val).strip().lower()

    m = re.match(r"^(\d+):(\d{2})(?::(\d{2}))?$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        return h * 60 + mi  # segundos ignorados

    days = hours = minutes = 0.0
    for part, unit in re.findall(
        r"(\d+)\s*(días|dias|día|dia|horas|hora|h|minutos|minuto|min)", s
    ):
        n = float(part)
        if unit.startswith("d"):
            days = n
        elif unit.startswith("h") or unit == "hora":
            hours = n
        else:
            minutes = n
    if days or hours or minutes:
        return days * HOURS_PER_BUSINESS_DAY * 60 + hours * 60 + minutes
    return None


def format_minutes_as_hhmm(minutes: float) -> str:
    total_min = int(minutes)
    h, mi = divmod(total_min, 60)
    return f"{h:02d}:{mi:02d}"


def calendar_minutes_between(inicio: datetime, fin: datetime) -> float:
    return max(0.0, (fin - inicio).total_seconds() / 60)


def _compare_minutes(
    expected_min: float | None,
    actual_min: float | None,
    label: str,
    expected_repr: str,
    actual_repr: str,
) -> tuple[bool, str]:
    if expected_min is None and actual_min is None:
        return True, "Ambos vacíos"
    if expected_min is None or actual_min is None:
        return False, f"No comparable: '{expected_repr}' vs '{actual_repr}'"
    tol = max(5.0, actual_min * 0.02)
    if abs(expected_min - actual_min) <= tol:
        return True, "Coinciden"
    return (
        False,
        f"{label}: {format_minutes_as_span(expected_min)} vs "
        f"{format_minutes_as_span(actual_min)} (Δ {abs(expected_min - actual_min):.0f} min)",
    )


def _compare_numeric(
    expected: float | None,
    actual: float | None,
    tol: float,
    unit: str,
) -> tuple[bool, str]:
    if expected is None and actual is None:
        return True, "Ambos vacíos"
    if expected is None or actual is None:
        return False, f"Valores no comparables ({unit})"
    if abs(expected - actual) <= tol:
        return True, "Coinciden"
    return False, f"Diferencia {abs(expected - actual):.4f} {unit} ({expected} vs {actual})"


def _result(
    base: dict[str, Any],
    *,
    comparacion: str,
    columna_esperada: str,
    columna_referencia: str,
    valor_esperado: Any,
    valor_referencia: Any,
    valor_calculado: Any = "",
    ok: bool,
    detalle: str,
    omitida: bool = False,
) -> dict[str, Any]:
    return {
        **base,
        "comparación": comparacion,
        "columna_*": columna_esperada,
        "columna_ref": columna_referencia,
        "valor_*": valor_esperado,
        "valor_ref": valor_referencia,
        "valor_calc": valor_calculado,
        "ok": ok,
        "omitida": omitida,
        "detalle": detalle,
    }


def _result_omitida(
    base: dict[str, Any],
    comparacion: str,
    columna_esperada: str,
    columna_referencia: str,
) -> dict[str, Any]:
    return _result(
        base,
        comparacion=comparacion,
        columna_esperada=columna_esperada,
        columna_referencia=columna_referencia,
        valor_esperado="",
        valor_referencia="",
        ok=False,
        detalle="Omitida: Fecha final vacía",
        omitida=True,
    )


def _compare_datetimes(
    dt_a: datetime | None,
    dt_b: datetime | None,
    raw_a: Any,
    raw_b: Any,
    tol_seconds: int = 2,
) -> tuple[bool, str]:
    if dt_a and dt_b:
        diff = abs((dt_a - dt_b).total_seconds())
        ok = diff <= tol_seconds
        return ok, "Coinciden" if ok else f"Δ {int(diff)}s: '{raw_a}' vs '{raw_b}'"
    ok = str(raw_a).strip() == str(raw_b).strip()
    return ok, "Coinciden (texto)" if ok else f"'{raw_a}' vs '{raw_b}'"


def compare_row(row: pd.Series, idx: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    base = {col: row.get(col, "") for col in DISPLAY_ID_COLUMNS if col in row.index}
    base["fila"] = idx + 1

    def missing(label: str, cols: list[str]) -> None:
        results.append(
            _result(
                base,
                comparacion=label,
                columna_esperada=", ".join(cols),
                columna_referencia="",
                valor_esperado="",
                valor_referencia="",
                ok=False,
                detalle=f"Columna faltante: {', '.join(cols)}",
            )
        )

    # --- 1. FECHA INICIO = FECHA DE INICIO PLANEADA* ---
    label1 = "Fecha inicio = Inicio planeada*"
    if "FECHA INICIO" not in row.index or "FECHA DE INICIO PLANEADA*" not in row.index:
        missing(label1, ["FECHA INICIO", "FECHA DE INICIO PLANEADA*"])
    else:
        ini = parse_datetime(row["FECHA INICIO"])
        ini_pl = parse_datetime(row["FECHA DE INICIO PLANEADA*"])
        ok, det = _compare_datetimes(
            ini, ini_pl, row["FECHA INICIO"], row["FECHA DE INICIO PLANEADA*"]
        )
        results.append(
            _result(
                base,
                comparacion=label1,
                columna_esperada="FECHA INICIO",
                columna_referencia="FECHA DE INICIO PLANEADA*",
                valor_esperado=row["FECHA INICIO"],
                valor_referencia=row["FECHA DE INICIO PLANEADA*"],
                ok=ok,
                detalle=det,
            )
        )

    # --- 2. Duración en horas* = Duración (1 día = 10 h) ---
    label2 = "Duración en horas* = Duración"
    if "DURACIÓN EN HORAS*" not in row.index or "DURACIÓN" not in row.index:
        missing(label2, ["DURACIÓN EN HORAS*", "DURACIÓN"])
    else:
        dur_h = duration_to_hours(row["DURACIÓN"])
        star_h = parse_duration_star_hours(row["DURACIÓN EN HORAS*"])
        ok, det = _compare_numeric(dur_h, star_h, 0.01, "h")
        if dur_h is None:
            ok, det = False, f"Duración no reconocida: '{row['DURACIÓN']}'"
        elif star_h is None:
            ok, det = False, f"Duración en horas* no comparable: '{row['DURACIÓN EN HORAS*']}'"
        elif not ok:
            det = (
                f"{format_duration_hours_display(row['DURACIÓN'], dur_h)} vs "
                f"* {format_hours_value(star_h)} h"
            )
        results.append(
            _result(
                base,
                comparacion=label2,
                columna_esperada="DURACIÓN EN HORAS*",
                columna_referencia="DURACIÓN",
                valor_esperado=row["DURACIÓN EN HORAS*"],
                valor_referencia=row["DURACIÓN"],
                valor_calculado=format_hours_value(dur_h),
                ok=ok,
                detalle=det,
            )
        )

    # --- 3. Vencimiento = Fecha inicio + Duración (días/horas hábiles) ---
    label3 = "Vencimiento = Inicio + Duración"
    if "VENCIMIENTO" not in row.index or "FECHA INICIO" not in row.index or "DURACIÓN" not in row.index:
        missing(label3, ["VENCIMIENTO", "FECHA INICIO", "DURACIÓN"])
    else:
        inicio_dt = parse_datetime(row["FECHA INICIO"])
        dur_val = row["DURACIÓN"]
        venc_dt = parse_datetime(row["VENCIMIENTO"])
        calc_dt = (
            vencimiento_from_inicio_duracion(inicio_dt, dur_val) if inicio_dt else None
        )
        if calc_dt and venc_dt:
            diff = abs((calc_dt - venc_dt).total_seconds())
            ok = diff <= 60
            calc_str = calc_dt.strftime("%Y-%m-%d %H:%M:%S")
            nota = format_vencimiento_calc_note(dur_val)
            det = (
                "Coinciden"
                if ok
                else (
                    f"Inicio '{row['FECHA INICIO']}' {nota} ({dur_val}) → {calc_str} vs "
                    f"Vencimiento '{row['VENCIMIENTO']}' (Δ {int(diff)}s)"
                )
            )
        else:
            ok = False
            calc_str = ""
            det = "No se pudo calcular (fecha inicio, duración o vencimiento inválidos)"
        results.append(
            _result(
                base,
                comparacion=label3,
                columna_esperada="VENCIMIENTO",
                columna_referencia="FECHA INICIO + DURACIÓN",
                valor_esperado=row["VENCIMIENTO"],
                valor_referencia=row["FECHA INICIO"],
                valor_calculado=calc_str,
                ok=ok,
                detalle=det,
            )
        )

    # --- 4. Vencimiento = Fecha de fin planeada* ---
    label4 = "Vencimiento = Fecha fin planeada*"
    if "VENCIMIENTO" not in row.index or "FECHA DE FIN PLANEADA*" not in row.index:
        missing(label4, ["VENCIMIENTO", "FECHA DE FIN PLANEADA*"])
    else:
        v_dt = parse_datetime(row["VENCIMIENTO"])
        f_dt = parse_datetime(row["FECHA DE FIN PLANEADA*"])
        ok, det = _compare_datetimes(
            v_dt, f_dt, row["VENCIMIENTO"], row["FECHA DE FIN PLANEADA*"]
        )
        results.append(
            _result(
                base,
                comparacion=label4,
                columna_esperada="VENCIMIENTO",
                columna_referencia="FECHA DE FIN PLANEADA*",
                valor_esperado=row["VENCIMIENTO"],
                valor_referencia=row["FECHA DE FIN PLANEADA*"],
                ok=ok,
                detalle=det,
            )
        )

    # --- 5. Fecha fin planeada* = Fecha inicio planeada* + Duración en horas* ---
    label5 = "Fin planeada* = Inicio planeada* + Duración*"
    needed_cols = ["FECHA DE FIN PLANEADA*", "FECHA DE INICIO PLANEADA*", "DURACIÓN EN HORAS*"]
    if any(c not in row.index for c in needed_cols):
        missing(label5, needed_cols)
    else:
        ini_pl = parse_datetime(row["FECHA DE INICIO PLANEADA*"])
        fin_pl = parse_datetime(row["FECHA DE FIN PLANEADA*"])
        dur_h_star = parse_duration_star_hours(row["DURACIÓN EN HORAS*"])
        if ini_pl is not None and dur_h_star is not None:
            calc_dt = add_business_hours(ini_pl, dur_h_star) if dur_h_star > 0 else ini_pl
            calc_str = calc_dt.strftime("%Y-%m-%d %H:%M:%S")
            if fin_pl is not None:
                diff = abs((calc_dt - fin_pl).total_seconds())
                ok = diff <= 60
                det = (
                    "Coinciden"
                    if ok
                    else (
                        f"Inicio planeada* '{row['FECHA DE INICIO PLANEADA*']}' "
                        f"+ {dur_h_star:g} h hábiles → {calc_str} vs "
                        f"Fin planeada* '{row['FECHA DE FIN PLANEADA*']}' (Δ {int(diff)}s)"
                    )
                )
            else:
                ok = False
                det = f"Fecha de fin planeada* inválida: '{row['FECHA DE FIN PLANEADA*']}'"
        else:
            ok = False
            calc_str = ""
            det = "No se pudo calcular (Inicio planeada* o Duración en horas* inválidos)"
        results.append(
            _result(
                base,
                comparacion=label5,
                columna_esperada="FECHA DE FIN PLANEADA*",
                columna_referencia="FECHA DE INICIO PLANEADA* + DURACIÓN EN HORAS*",
                valor_esperado=row["FECHA DE FIN PLANEADA*"],
                valor_referencia=row["FECHA DE INICIO PLANEADA*"],
                valor_calculado=calc_str,
                ok=ok,
                detalle=det,
            )
        )

    # Reglas 6-8 dependen de FECHA FINAL
    fin_vacia = _is_empty(row.get("FECHA FINAL"))
    if fin_vacia:
        for comparacion, col_a, col_ref in RULES_REQUIRING_FECHA_FINAL:
            results.append(_result_omitida(base, comparacion, col_a, col_ref))
    else:
        inicio_dt = parse_datetime(row.get("FECHA INICIO"))
        fin_dt = parse_datetime(row.get("FECHA FINAL"))
        cal_min = (
            calendar_minutes_between(inicio_dt, fin_dt) if inicio_dt and fin_dt else None
        )
        cal_span = format_minutes_as_span(cal_min) if cal_min is not None else ""

        # --- 6. Tiempo calendario* = Fin − Inicio ---
        label6 = "Tiempo calendario* = Fin − Inicio"
        if "TIEMPO UTILIZADO (CALENDARIO)*" not in row.index:
            missing(label6, ["TIEMPO UTILIZADO (CALENDARIO)*"])
        else:
            star_min = parse_time_span_to_minutes(row["TIEMPO UTILIZADO (CALENDARIO)*"])
            ok, det = _compare_minutes(
                star_min,
                cal_min,
                "Tiempo calendario*",
                str(row["TIEMPO UTILIZADO (CALENDARIO)*"]),
                cal_span,
            )
            results.append(
                _result(
                    base,
                    comparacion=label6,
                    columna_esperada="TIEMPO UTILIZADO (CALENDARIO)*",
                    columna_referencia="FECHA FINAL − FECHA INICIO",
                    valor_esperado=row["TIEMPO UTILIZADO (CALENDARIO)*"],
                    valor_referencia=cal_span,
                    valor_calculado=cal_span,
                    ok=ok,
                    detalle=det,
                )
            )

        # --- 7. Tiempo utilizado = Tiempo utilizado (hábil)* (HH:MM) ---
        label7 = "Tiempo utilizado = Hábil* (HH:MM)"
        if "TIEMPO UTILIZADO" not in row.index or "TIEMPO UTILIZADO (HÁBIL)*" not in row.index:
            missing(label7, ["TIEMPO UTILIZADO", "TIEMPO UTILIZADO (HÁBIL)*"])
        else:
            uso_min = parse_business_time_to_minutes(row["TIEMPO UTILIZADO"])
            habil_min = parse_business_time_to_minutes(row["TIEMPO UTILIZADO (HÁBIL)*"])
            if uso_min is None or habil_min is None:
                ok = False
                det = (
                    f"No comparable: '{row['TIEMPO UTILIZADO']}' vs "
                    f"'{row['TIEMPO UTILIZADO (HÁBIL)*']}'"
                )
                calc_str = ""
            else:
                uso_hhmm = int(uso_min)
                habil_hhmm = int(habil_min)
                ok = uso_hhmm == habil_hhmm
                uso_fmt = format_minutes_as_hhmm(uso_hhmm)
                habil_fmt = format_minutes_as_hhmm(habil_hhmm)
                calc_str = habil_fmt
                det = (
                    "Coinciden (HH:MM)"
                    if ok
                    else f"{uso_fmt} vs {habil_fmt} (Δ {abs(uso_hhmm - habil_hhmm)} min)"
                )
            results.append(
                _result(
                    base,
                    comparacion=label7,
                    columna_esperada="TIEMPO UTILIZADO",
                    columna_referencia="TIEMPO UTILIZADO (HÁBIL)*",
                    valor_esperado=row["TIEMPO UTILIZADO"],
                    valor_referencia=row["TIEMPO UTILIZADO (HÁBIL)*"],
                    valor_calculado=calc_str,
                    ok=ok,
                    detalle=det,
                )
            )

        # --- 8. Tiempo total utilizado en días* = Hábil* / 10 ---
        label8 = "Días totales* = Hábil* / 10"
        if "TIEMPO TOTAL UTILIZADO EN DÍAS*" not in row.index or "TIEMPO UTILIZADO (HÁBIL)*" not in row.index:
            missing(label8, ["TIEMPO TOTAL UTILIZADO EN DÍAS*", "TIEMPO UTILIZADO (HÁBIL)*"])
        else:
            habil_min = parse_business_time_to_minutes(row["TIEMPO UTILIZADO (HÁBIL)*"])
            star_days = parse_duration_star_hours(row["TIEMPO TOTAL UTILIZADO EN DÍAS*"])
            if habil_min is None:
                ok, det, calc_str = False, (
                    f"Tiempo hábil* no parseable: '{row['TIEMPO UTILIZADO (HÁBIL)*']}'"
                ), ""
            elif star_days is None:
                ok, det, calc_str = False, (
                    f"Días totales* no parseable: '{row['TIEMPO TOTAL UTILIZADO EN DÍAS*']}'"
                ), ""
            else:
                habil_hours = habil_min / 60
                expected_days = habil_hours / HOURS_PER_BUSINESS_DAY
                ok = abs(star_days - expected_days) <= 0.05
                calc_str = f"{expected_days:.4f}"
                det = (
                    "Coinciden"
                    if ok
                    else (
                        f"Esperado {expected_days:.4f} días "
                        f"(= {habil_hours:.2f} h / {HOURS_PER_BUSINESS_DAY:g}) vs "
                        f"{star_days:.4f} días "
                        f"(Δ {abs(star_days - expected_days):.4f})"
                    )
                )
            results.append(
                _result(
                    base,
                    comparacion=label8,
                    columna_esperada="TIEMPO TOTAL UTILIZADO EN DÍAS*",
                    columna_referencia="TIEMPO UTILIZADO (HÁBIL)* / 10",
                    valor_esperado=row["TIEMPO TOTAL UTILIZADO EN DÍAS*"],
                    valor_referencia=row["TIEMPO UTILIZADO (HÁBIL)*"],
                    valor_calculado=calc_str,
                    ok=ok,
                    detalle=det,
                )
            )

    return results


def run_comparison(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    df = align_columns(df)
    all_rows: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        all_rows.extend(compare_row(row, int(idx)))

    detail = pd.DataFrame(all_rows)
    if detail.empty:
        return detail, pd.DataFrame(), {"total": 0, "ok": 0, "error": 0, "omitidas": 0, "filas_csv": 0}

    evaluadas = detail[~detail["omitida"]]
    summary = (
        detail.groupby("comparación", as_index=False)
        .agg(
            total=("ok", "count"),
            errores=("ok", lambda s: int((~s).sum())),
            omitidas=("omitida", lambda s: int(s.sum())),
        )
    )
    summary["aciertos"] = summary["total"] - summary["errores"] - summary["omitidas"]

    metrics = {
        "total": len(evaluadas),
        "ok": int(evaluadas["ok"].sum()),
        "error": int((~evaluadas["ok"]).sum()),
        "omitidas": int(detail["omitida"].sum()),
        "filas_csv": len(df),
    }
    return detail, summary, metrics


def validate_required_columns(df: pd.DataFrame) -> list[str]:
    df = align_columns(df)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    return missing
