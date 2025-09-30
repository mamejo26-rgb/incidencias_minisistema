
import os
import io
import json
import sqlite3
import pandas as pd
import streamlit as st
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "incidencias.db"
SEEDS_PATH = "seeds/plants.json"
DEFAULT_TYPES = ["FALTA", "RETARDO", "PERMISO", "INCAPACIDAD", "OTRO"]

# ------------------------------
# DB helpers
# ------------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plants(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS incidences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dt TEXT NOT NULL,
            employee TEXT NOT NULL,
            plant TEXT NOT NULL,
            inc_type TEXT NOT NULL,
            hours REAL,
            notes TEXT
        );
    """)
    return conn


def seed_plants(conn):
    # Seed only if table is empty
    cur = conn.execute("SELECT COUNT(*) FROM plants;")
    count = cur.fetchone()[0]
    if count == 0 and os.path.exists(SEEDS_PATH):
        with open(SEEDS_PATH, "r", encoding="utf-8") as f:
            names = json.load(f)
        for n in names:
            try:
                conn.execute("INSERT INTO plants(name) VALUES(?)", (n.strip().upper(),))
            except sqlite3.IntegrityError:
                pass
        conn.commit()

def get_plants(conn):
    cur = conn.execute("SELECT name FROM plants ORDER BY name;")
    return [r[0] for r in cur.fetchall()]

def add_plant(conn, name):
    conn.execute("INSERT OR IGNORE INTO plants(name) VALUES(?)", (name.strip().upper(),))
    conn.commit()

def insert_incidence(conn, dt, employee, plant, inc_type, hours, notes):
    conn.execute(
        "INSERT INTO incidences(dt, employee, plant, inc_type, hours, notes) VALUES(?,?,?,?,?,?)",
        (dt, employee.strip(), plant.strip().upper(), inc_type.strip().upper(), hours, notes.strip() if notes else None)
    )
    conn.commit()

def read_incidents_df(conn, start_dt=None, end_dt=None, plant=None):
    query = "SELECT dt, employee, plant, inc_type, hours, notes FROM incidences WHERE 1=1"
    params = []
    if start_dt:
        query += " AND DATE(dt) >= DATE(?)"
        params.append(start_dt)
    if end_dt:
        query += " AND DATE(dt) <= DATE(?)"
        params.append(end_dt)
    if plant and plant != "TODAS":
        query += " AND plant = ?"
        params.append(plant)
    query += " ORDER BY DATE(dt) DESC, plant, employee;"
    return pd.read_sql_query(query, conn, params=params)

def to_excel_bytes(df_data, df_summary):
    import pandas as pd
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_data.to_excel(writer, sheet_name="Datos", index=False)
        df_summary.to_excel(writer, sheet_name="Resumen", index=False)
    output.seek(0)
    return output.getvalue()

# ------------------------------
# UI
# ------------------------------
st.set_page_config(page_title="Incidencias Semanales", page_icon="🗂️", layout="wide")

conn = get_conn()
seed_plants(conn)

# Sidebar
st.sidebar.title("🛠️ Configuración")
section = st.sidebar.radio("Sección", ["Capturar incidencia", "Consolidado / Exportar", "Catálogo de Plantas", "Configuración"])

# Manage incidence types in session_state
if "inc_types" not in st.session_state:
    st.session_state.inc_types = DEFAULT_TYPES.copy()

with st.sidebar.expander("Tipos de incidencia"):
    # Simple editable list
    inc_types_text = st.text_area("Define los tipos (uno por línea)", value="\\n".join(st.session_state.inc_types), height=120)
    if st.button("Guardar tipos"):
        types = [t.strip().upper() for t in inc_types_text.splitlines() if t.strip()]
        if types:
            st.session_state.inc_types = types
            st.success("Tipos de incidencia actualizados.")

admin_pin_env = os.getenv("ADMIN_PIN", "").strip()
if admin_pin_env:
    st.sidebar.info("Consolidado protegido con PIN.")

# ------------------------------
# Sections
# ------------------------------
if section == "Capturar incidencia":
    st.header("✍️ Captura de Incidencia")
    col1, col2 = st.columns(2)
    with col1:
        dt = st.date_input("Fecha", value=date.today())
        employee = st.text_input("Nombre del empleado")
        plant_list = get_plants(conn)
        plant = st.selectbox("Planta", options=plant_list)
    with col2:
        inc_type = st.selectbox("Tipo de incidencia", options=st.session_state.inc_types)
        hours = st.number_input("Horas afectadas", min_value=0.0, step=0.5, value=0.0, help="Use 0 si no aplica")
        notes = st.text_area("Observaciones", placeholder="Opcional")

    if st.button("Guardar incidencia", type="primary", use_container_width=True):
        if not employee.strip():
            st.error("El nombre del empleado es obligatorio.")
        else:
            insert_incidence(conn, dt.isoformat(), employee, plant, inc_type, hours, notes or "")
            st.success("Incidencia guardada correctamente ✅")

    st.divider()
    st.subheader("Últimas capturas")
    recent_df = read_incidents_df(conn)[:50]
    if recent_df.empty:
        st.info("Aún no hay incidencias capturadas.")
    else:
        st.dataframe(recent_df, use_container_width=True)

elif section == "Consolidado / Exportar":
    st.header("📦 Consolidado / Exportar")

    # Optional PIN
    if admin_pin_env:
        pin = st.text_input("PIN de administrador", type="password")
        if pin.strip() != admin_pin_env:
            st.warning("Ingresa el PIN para ver el consolidado.")
            st.stop()

    colf1, colf2, colf3 = st.columns(3)
    with colf1:
        start_dt = st.date_input("Desde", value=date.today().replace(day=1))
    with colf2:
        end_dt = st.date_input("Hasta", value=date.today())
    with colf3:
        plant_filter = st.selectbox("Planta", options=["TODAS"] + get_plants(conn))

    df = read_incidents_df(conn, start_dt.isoformat(), end_dt.isoformat(), plant_filter)
    st.dataframe(df, use_container_width=True, height=400)

    if not df.empty:
        # Build summary
        summary = (df
                   .assign(Horas=df["hours"].fillna(0))
                   .groupby(["plant", "inc_type"], as_index=False)
                   .agg(Incidencias=("inc_type", "count"), Horas=("Horas", "sum"))
                  )
        st.subheader("Resumen por Planta y Tipo")
        st.dataframe(summary, use_container_width=True)

        excel_bytes = to_excel_bytes(
            df.rename(columns={"dt": "Fecha", "employee": "Empleado", "plant": "Planta",
                               "inc_type": "Tipo", "hours": "Horas", "notes": "Observaciones"}),
            summary.rename(columns={"plant": "Planta", "inc_type": "Tipo"})
        )
        st.download_button(
            "⬇️ Descargar Excel (Datos + Resumen)",
            data=excel_bytes,
            file_name=f"incidencias_consolidado_{date.today().isoformat()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )
    else:
        st.info("No hay datos en el rango seleccionado.")

elif section == "Catálogo de Plantas":
    st.header("🏷️ Catálogo de Plantas")
    st.write("Agrega o corrige nombres. Evita duplicados y mayúsculas/minúsculas inconsistentes.")
    existing = get_plants(conn)
    st.write("Plantas actuales:", ", ".join(existing) if existing else "—")

    new_name = st.text_input("Nueva planta", placeholder="Ej. GAS LUX, JEREZ, etc.")
    if st.button("Agregar planta"):
        if not new_name.strip():
            st.error("Escribe un nombre válido.")
        else:
            add_plant(conn, new_name)
            st.success(f"Planta '{new_name.upper()}' agregada.")
            st.experimental_rerun()

else:  # Configuración
    st.header("⚙️ Configuración")
    st.write("Ajustes básicos del sistema.")
    st.code(f"DB_PATH = '{DB_PATH}'", language="python")
    st.write("Para proteger el consolidado con un PIN, crea un archivo `.env` y define `ADMIN_PIN`.")

    st.write("**Respaldo de datos**: copia el archivo `incidencias.db`.")
    st.write("**Carga inicial de plantas**: edita `seeds/plants.json` antes del primer arranque.")
