# Comparar reporte de trazabilidad

Dashboard en Python (Streamlit) para cotejar columnas de un CSV de trazabilidad. No usa base de datos: todo queda en memoria hasta que pulses **Limpiar dashboard**.

## Comparaciones

| Columna (\*)                     | Columna de referencia |
| -------------------------------- | --------------------- |
| FECHA DE INICIO PLANEADA\*       | FECHA INICIO          |
| DURACIÓN EN HORAS\*              | DURACIÓN              |
| FECHA DE FIN PLANEADA\*          | FECHA FINAL           |
| TIEMPO UTILIZADO (HÁBIL)\*       | TIEMPO TRANSCURRIDO   |
| TIEMPO UTILIZADO (CALENDARIO)\*  | TIEMPO TRANSCURRIDO   |
| TIEMPO TOTAL UTILIZADO EN DÍAS\* | TIEMPO TRANSCURRIDO   |

Las fechas admiten diferencia de hasta 2 segundos. Duración y tiempos se normalizan entre formatos texto (`1 Horas`, `9 días…`) y numéricos (`1.0`, `00:04:08`).

## Uso

```bash
cd comparar-reporte-inter
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

1. Carga el CSV.
2. Pulsa **Comparar columnas**.
3. Revisa pestañas: resumen, errores, detalle y vista por fila.
4. **Limpiar dashboard** para cargar otro archivo.

## Archivo de ejemplo

`trazabilidad_20260604_160453_6a21e875184a3.csv` en la raíz del proyecto.
