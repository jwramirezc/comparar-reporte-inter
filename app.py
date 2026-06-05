"""
Dashboard para validar columnas del reporte de trazabilidad (CSV).
Sin base de datos: todo en memoria de sesión.
"""

import streamlit as st
import pandas as pd

from comparador import (
    COMPARISON_LABELS_ORDER,
    RULES_REQUIRING_FECHA_FINAL,
    VALIDATION_RULES,
    align_columns,
    run_comparison,
    validate_required_columns,
)

ICON_OK = "✅"
ICON_ERR = "❌"
ICON_SKIP = "⏭️"
ICON_NA = "—"

SKIP_COMPARISONS = {label for label, *_ in RULES_REQUIRING_FECHA_FINAL}


def status_icon(ok: bool, omitida: bool) -> str:
    if omitida:
        return ICON_SKIP
    if ok:
        return ICON_OK
    return ICON_ERR


def build_row_status_view(raw_df: pd.DataFrame, detail: pd.DataFrame) -> pd.DataFrame:
    """Una fila por registro del CSV con iconos por validación."""
    raw = align_columns(raw_df.copy())
    ids = ["fila"] + [c for c in ["ID", "NOMBRE ACTIVIDAD"] if c in raw.columns]
    base = raw.reset_index(drop=True).reset_index(names="fila")
    base["fila"] = base["fila"] + 1

    icon_rows = []
    for fila, grp in detail.groupby("fila"):
        row_icons: dict[str, str] = {"fila": fila}
        for label in COMPARISON_LABELS_ORDER:
            match = grp[grp["comparación"] == label]
            if match.empty:
                row_icons[f"✓ {label}"] = ICON_NA
            else:
                r = match.iloc[0]
                if r.get("omitida"):
                    row_icons[f"✓ {label}"] = ICON_SKIP
                else:
                    row_icons[f"✓ {label}"] = ICON_OK if r["ok"] else ICON_ERR
        icon_rows.append(row_icons)

    icons_df = pd.DataFrame(icon_rows)
    merged = base[ids].merge(icons_df, on="fila", how="left")

    # Forzar ⏭️ en reglas 4–6 si Fecha final vacía en el CSV
    if "FECHA FINAL" in raw.columns:
        fin_col = raw.reset_index(drop=True).reset_index(names="fila")
        fin_col["fila"] = fin_col["fila"] + 1
        for label in SKIP_COMPARISONS:
            col = f"✓ {label}"
            if col in merged.columns:
                merged[col] = merged.apply(
                    lambda r, c=col: (
                        ICON_SKIP
                        if str(fin_col.loc[fin_col["fila"] == r["fila"], "FECHA FINAL"].iloc[0]).strip()
                        in ("", "nan", "None")
                        else r[c]
                    ),
                    axis=1,
                )

    ordered = ["fila"] + ids[1:] + [f"✓ {l}" for l in COMPARISON_LABELS_ORDER]
    return merged[[c for c in ordered if c in merged.columns]]


st.set_page_config(
    page_title="Comparar reporte trazabilidad",
    page_icon="📊",
    layout="wide",
)

st.title("Validador de reporte de trazabilidad")
st.caption(
    "Carga un CSV y valida las 6 reglas por fila. Sin almacenamiento persistente."
)

with st.expander("Reglas de validación", expanded=False):
    for i, rule in enumerate(VALIDATION_RULES, start=1):
        st.write(f"{i}. {rule}")

if "detail" not in st.session_state:
    st.session_state.detail = None
    st.session_state.summary = None
    st.session_state.metrics = None
    st.session_state.filename = None
    st.session_state.raw_df = None

col_upload, col_actions = st.columns([2, 1])

with col_upload:
    uploaded = st.file_uploader("Cargar archivo CSV", type=["csv"])

with col_actions:
    st.write("")
    compare_btn = st.button("Comparar columnas", type="primary", use_container_width=True)
    clear_btn = st.button("Limpiar dashboard", use_container_width=True)

if clear_btn:
    st.session_state.detail = None
    st.session_state.summary = None
    st.session_state.metrics = None
    st.session_state.filename = None
    st.session_state.raw_df = None
    st.rerun()

if uploaded is not None:
    try:
        raw_df = pd.read_csv(uploaded, dtype=str, keep_default_na=False)
        raw_df = raw_df.apply(
            lambda col: col.map(lambda x: x.strip() if isinstance(x, str) else x)
        )
        st.session_state.raw_df = raw_df
        st.session_state.filename = uploaded.name
    except Exception as e:
        st.error(f"No se pudo leer el CSV: {e}")

if compare_btn:
    if st.session_state.raw_df is None:
        st.warning("Primero carga un archivo CSV.")
    else:
        missing = validate_required_columns(st.session_state.raw_df)
        if missing:
            st.error(
                "Faltan columnas requeridas:\n\n"
                + "\n".join(f"- `{c}`" for c in missing)
            )
        else:
            with st.spinner("Validando…"):
                detail, summary, metrics = run_comparison(st.session_state.raw_df)
            st.session_state.detail = detail
            st.session_state.summary = summary
            st.session_state.metrics = metrics
            st.success(
                f"Listo — {metrics['ok']} aciertos, {metrics['error']} errores, "
                f"{metrics['omitidas']} omitidas ({metrics['filas_csv']} filas)."
            )

if st.session_state.filename:
    st.info(f"Archivo en uso: **{st.session_state.filename}**")

metrics = st.session_state.metrics
detail = st.session_state.detail
summary = st.session_state.summary

if metrics and detail is not None:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Filas en CSV", metrics["filas_csv"])
    m2.metric("Evaluadas", metrics["total"])
    m3.metric("Aciertos", metrics["ok"])
    m4.metric("Errores", metrics["error"])
    m5.metric("Omitidas", metrics["omitidas"])

    pct_ok = round(100 * metrics["ok"] / metrics["total"], 1) if metrics["total"] else 0
    st.progress(
        metrics["ok"] / metrics["total"] if metrics["total"] else 0,
        text=f"{pct_ok}% aciertos (sin omitidas)",
    )

    tab_resumen, tab_errores, tab_todo, tab_vista = st.tabs(
        ["Resumen", "Solo errores", "Detalle completo", "Vista por fila"]
    )

    with tab_resumen:
        if summary is not None and not summary.empty:
            summary_display = summary.copy()
            summary_display["% acierto"] = (
                summary_display["aciertos"]
                / (summary_display["total"] - summary_display["omitidas"])
                * 100
            ).round(1)
            st.dataframe(summary_display, use_container_width=True, hide_index=True)

    errors_df = detail[(~detail["ok"]) & (~detail["omitida"])].copy()

    with tab_errores:
        st.subheader(f"Errores ({len(errors_df)})")
        if errors_df.empty:
            st.success("No hay errores.")
        else:
            st.dataframe(
                errors_df[
                    [
                        "fila",
                        "ID",
                        "NOMBRE ACTIVIDAD",
                        "comparación",
                        "valor_*",
                        "valor_ref",
                        "valor_calc",
                        "detalle",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )

    with tab_todo:
        show_df = detail.copy()
        show_df["icono"] = show_df.apply(
            lambda r: status_icon(r["ok"], r.get("omitida", False)), axis=1
        )
        st.dataframe(
            show_df[
                [
                    "icono",
                    "fila",
                    "ID",
                    "NOMBRE ACTIVIDAD",
                    "comparación",
                    "valor_*",
                    "valor_ref",
                    "valor_calc",
                    "detalle",
                ]
            ],
            use_container_width=True,
            hide_index=True,
        )

    with tab_vista:
        if st.session_state.raw_df is not None:
            wide = build_row_status_view(st.session_state.raw_df, detail)
            st.dataframe(wide, use_container_width=True, hide_index=True)
