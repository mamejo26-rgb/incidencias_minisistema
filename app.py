
# app.py
import os
import io
import json
import sqlite3
import pandas as pd
import streamlit as st
from datetime import date, datetime, timedelta
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
    # plants
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plants(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        );
    """)
    # employees (incluye company desde el inicio)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS employees(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            plant TEXT NOT NULL,
            hire_date TEXT NOT NULL,
            days_per_year INTEGER NOT NULL DEFAULT 12,
            rest_day TEXT,
            company TEXT
        );
    """)
    # incidences
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

def ensure_company_column(conn):
    cols = {r[1].lower() for r in conn.execute("PRAGMA table_info(employees);").fetchall()}
    if "company" not in cols:
        conn.execute("ALTER TABLE employees ADD COLUMN company TEXT;")
        conn.commit()

# === ZONAS (usamos la columna plant como "zona" en UI) ===
def get_zonas(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT plant FROM employees WHERE plant IS NOT NULL AND plant<>'' ORDER BY 1;"
    ).fetchall()]

# === Mapa de colores para incidencias ===
INC_COLOR = {
    "FALTA": "#ffcccc",        # rojo claro
    "RETARDO": "#fff3cd",      # amarillo
    "PERMISO": "#d1e7dd",      # verde
    "INCAPACIDAD": "#cfe2ff",  # azul
    "VACACIONES": "#e6ccff",   # morado
    "OTRO": "#f0f0f0",         # gris
    "—": "white",              # vacío
}


def seed_plants(conn):
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

# --- employees
def seed_employees(conn, rows):
    """UPSERT por name (no duplica, actualiza)."""
    for r in rows:
        name = r["name"].strip().upper()
        plant = r["plant"].strip().upper()
        hire  = r["hire_date"]
        dpy   = int(r.get("days_per_year", 12))
        rest  = (r.get("rest_day") or "").strip().upper() or None
        comp  = (r.get("company") or "").strip().upper() or None

        conn.execute("""
            INSERT INTO employees (name, plant, hire_date, days_per_year, rest_day, company)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
               plant=excluded.plant,
               hire_date=excluded.hire_date,
               days_per_year=excluded.days_per_year,
               rest_day=excluded.rest_day,
               company=excluded.company
        """, (name, plant, hire, dpy, rest, comp))
    conn.commit()

def get_employees_df(conn, plant=None, company=None):
    q = "SELECT name, plant, rest_day, COALESCE(company,'') AS company FROM employees WHERE 1=1"
    p = []
    if plant and plant != "TODAS":
        q += " AND plant=?"; p.append(plant)
    if company and company != "TODAS":
        q += " AND company=?"; p.append(company)
    q += " ORDER BY name;"
    return pd.read_sql_query(q, conn, params=p)

def get_employees(conn, plant=None, company=None):
    df = get_employees_df(conn, plant, company)
    return df["name"].tolist()

def get_companies(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT COALESCE(company,'') FROM employees ORDER BY 1;"
    ).fetchall() if r[0]]

def get_employee_info(conn, name):
    cur = conn.execute(
        "SELECT name, plant, hire_date, days_per_year, rest_day, company FROM employees WHERE name=?",
        (name,))
    r = cur.fetchone()
    if not r: return None
    return {"name": r[0], "plant": r[1], "hire_date": r[2], "days_per_year": r[3], "rest_day": r[4], "company": r[5]}

# --- incidences
def insert_incidence(conn, dt, employee, plant, inc_type, notes=""):
    """Inserta una incidencia (sin horas)."""
    conn.execute(
        "INSERT INTO incidences(dt, employee, plant, inc_type, hours, notes) VALUES(?,?,?,?,?,?)",
        (dt, employee.strip(), plant.strip().upper(), inc_type.strip().upper(), None, notes.strip() if notes else None)
    )
    conn.commit()

def replace_incidence_day(conn, dt_iso, employee, inc_type, notes=""):
    """Borra lo que haya ese día para el empleado y vuelve a grabar si inc_type no está vacío."""
    conn.execute("DELETE FROM incidences WHERE employee=? AND DATE(dt)=DATE(?)",
                 (employee, dt_iso))
    if inc_type and inc_type != "—":
        plant = get_employee_info(conn, employee)["plant"]
        insert_incidence(conn, dt_iso, employee, plant, inc_type, notes)
    conn.commit()

def read_incidents_df(conn, start_dt=None, end_dt=None, plant=None, company=None):
    base = """
      SELECT i.dt, i.employee, i.plant, i.inc_type, COALESCE(e.company,'') AS company, i.notes
      FROM incidences i
      LEFT JOIN employees e ON e.name = i.employee
      WHERE 1=1
    """
    params = []
    if start_dt:
        base += " AND DATE(i.dt) >= DATE(?)"; params.append(start_dt)
    if end_dt:
        base += " AND DATE(i.dt) <= DATE(?)"; params.append(end_dt)
    if plant and plant != "TODAS":
        base += " AND i.plant = ?"; params.append(plant)
    if company and company != "TODAS":
        base += " AND COALESCE(e.company,'') = ?"; params.append(company)
    base += " ORDER BY DATE(i.dt) DESC, i.plant, i.employee;"
    return pd.read_sql_query(base, conn, params=params)

def to_excel_bytes(df_data, df_summary):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_data.to_excel(writer, sheet_name="Datos", index=False)
        df_summary.to_excel(writer, sheet_name="Resumen", index=False)
    output.seek(0)
    return output.getvalue()

# ------------------------------
# CSV Maestro transform
# ------------------------------
import re

def _normalize_rest_day(raw: str) -> str:
    if not raw:
        return ""
    s = str(raw).strip().upper()
    s = (s.replace("Á","A").replace("É","E").replace("Í","I")
           .replace("Ó","O").replace("Ú","U"))
    mapa = {
        "LUNES":"LUN","MARTES":"MAR","MIERCOLES":"MIE","MIÉRCOLES":"MIE",
        "JUEVES":"JUE","VIERNES":"VIE","SABADO":"SAB","SÁBADO":"SAB","DOMINGO":"DOM",
        "LUN":"LUN","MAR":"MAR","MIE":"MIE","JUE":"JUE","VIE":"VIE","SAB":"SAB","DOM":"DOM"
    }
    return mapa.get(s, "")

def _parse_ddmmyyyy(s: str) -> str:
    if not s: return ""
    s = str(s).strip().replace("-", "/")
    try:
        dt = datetime.strptime(s, "%d/%m/%Y")
        return dt.date().isoformat()
    except Exception:
        return ""

def transform_employees_csv(df):
    """
    Espera columnas (al menos):
      EMPRESA, ZONA, NOMBRE, PATERNO, MATERNO, INGRESO, DIA DE DESCANSO, DIAS CORRESPONDIENTES
    Extra (ignora sin fallar):
      VAN A TOMAR VACACIONES ESTE MES  SI O NO, FECHA DE SALIDA, FECHA DE REGRESO, DISPONIBLES
    """
    cols = {c.strip().upper(): c for c in df.columns}
    need = {"EMPRESA","ZONA","NOMBRE","PATERNO","MATERNO","INGRESO","DIA DE DESCANSO","DIAS CORRESPONDIENTES"}
    missing = need - set(cols.keys())
    if missing:
        raise ValueError(f"Faltan columnas en el CSV: {', '.join(missing)}")

    out = []
    for _, r in df.iterrows():
        company = str(r[cols["EMPRESA"]]).strip().upper()
        plant   = str(r[cols["ZONA"]]).strip().upper()
        nombre  = str(r[cols["NOMBRE"]]).strip()
        paterno = str(r[cols["PATERNO"]]).strip()
        materno = str(r[cols["MATERNO"]]).strip()
        full_name = " ".join([nombre, paterno, materno]).upper().replace("  ", " ").strip()

        hire_iso = _parse_ddmmyyyy(r[cols["INGRESO"]])
        rest_day = _normalize_rest_day(r[cols["DIA DE DESCANSO"]])
        try:
            dpy = int(re.sub(r"[^0-9]", "", str(r[cols["DIAS CORRESPONDIENTES"]])))
            if dpy <= 0: dpy = 12
        except Exception:
            dpy = 12

        if not full_name or not plant or not hire_iso:
            continue

        out.append({
            "name": full_name,
            "plant": plant,
            "hire_date": hire_iso,
            "days_per_year": dpy,
            "rest_day": rest_day or None,
            "company": company
        })
    return out

# ------------------------------
# Vacations helpers
# ------------------------------
def months_between(d1, d2):
    return (d2.year - d1.year)*12 + (d2.month - d1.month) - (0 if d2.day >= d1.day else 1)

def current_year_period():
    today = date.today()
    return date(today.year,1,1), date(today.year,12,31)

def prev_year_period():
    today = date.today()
    return date(today.year-1,1,1), date(today.year-1,12,31)

def get_vacations_taken(conn, name, start, end):
    """Cuenta 1 por cada registro de VACACIONES en el periodo."""
    df = pd.read_sql_query("""
        SELECT dt FROM incidences
        WHERE employee=? AND inc_type='VACACIONES'
          AND DATE(dt) BETWEEN DATE(?) AND DATE(?)
    """, conn, params=(name, start.isoformat(), end.isoformat()))
    return float(len(df))

def vacation_status_for_all(conn):
    today = date.today()
    cy_start, cy_end = current_year_period()
    py_start, py_end = prev_year_period()

    cur = conn.execute("SELECT name, plant, hire_date, days_per_year FROM employees ORDER BY plant, name;")
    out = []
    for name, plant, hire, dpy in cur.fetchall():
        try:
            hd = datetime.fromisoformat(hire).date()
        except Exception:
            continue
        start_for_months = max(hd, cy_start)
        m = max(0, months_between(start_for_months, today))
        entitlement_cy = round((dpy/12.0)*min(m,12), 2)
        taken_cy = get_vacations_taken(conn, name, cy_start, cy_end)
        remaining_cy = round(entitlement_cy - taken_cy, 2)

        entitlement_py = 0.0
        if hd <= py_end:
            start_for_py = max(hd, py_start)
            m_py = max(0, months_between(start_for_py, py_end))
            entitlement_py = round((dpy/12.0)*min(m_py,12), 2)
        taken_py = get_vacations_taken(conn, name, py_start, py_end)
        remaining_py = round(entitlement_py - taken_py, 2)

        expiry_date = py_end + timedelta(days=365)
        days_to_expiry = (expiry_date - today).days
        will_expire = remaining_py > 0 and days_to_expiry <= 60

        out.append({
            "Empleado": name, "Planta": plant, "Ingreso": hd.isoformat(),
            "Dias/Año": dpy, "Meses trabajados (año)": m,
            "Derecho año actual": entitlement_cy,
            "Tomado año actual": taken_cy,
            "Saldo año actual": remaining_cy,
            "Saldo año anterior": remaining_py,
            "Vence saldo anterior": expiry_date.isoformat(),
            "Días para vencer": days_to_expiry,
            "ALERTA": "⚠️" if will_expire else ""
        })
    return pd.DataFrame(out)

# ------------------------------
# UI
# ------------------------------
st.set_page_config(page_title="Incidencias Semanales", page_icon="🗂️", layout="wide")

conn = get_conn()
seed_plants(conn)
ensure_company_column(conn)

# Sidebar
st.sidebar.title("🛠️ Configuración")
section = st.sidebar.radio(
    "Sección",
    ["Capturar incidencia",
     "Matriz semanal",
     "Consolidado / Exportar",
     "Catálogo de Empleados",
     "Catálogo de Plantas",
     "Vacaciones",
     "Gráficos",
     "Configuración"]
)

# Manage incidence types in session_state
if "inc_types" not in st.session_state:
    st.session_state.inc_types = DEFAULT_TYPES.copy()

with st.sidebar.expander("Tipos de incidencia"):
    inc_types_text = st.text_area("Define los tipos (uno por línea)",
                                  value="\n".join(st.session_state.inc_types),
                                  height=120)
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
        dt_in = st.date_input("Fecha", value=date.today())
        employee = st.text_input("Nombre del empleado")
        plant_list = get_plants(conn)
        plant = st.selectbox("zona", options=plant_list)
    with col2:
        inc_type = st.selectbox("Tipo de incidencia", options=st.session_state.inc_types + ["VACACIONES"])
        notes = st.text_area("Observaciones", placeholder="Opcional")

    if st.button("Guardar incidencia", type="primary", use_container_width=True):
        if not employee.strip():
            st.error("El nombre del empleado es obligatorio.")
        else:
            insert_incidence(conn, dt_in.isoformat(), employee, plant, inc_type, notes or "")
            st.success("Incidencia guardada correctamente ✅")

    st.divider()
    st.subheader("Últimas capturas")
    recent_df = read_incidents_df(conn)[:50]
    if recent_df.empty:
        st.info("Aún no hay incidencias capturadas.")
    else:
        st.dataframe(recent_df, use_container_width=True)

elif section == "Matriz semanal":
    from st_aggrid import AgGrid, GridOptionsBuilder, JsCode, GridUpdateMode

    st.header("📅 Matriz semanal (semana actual)")

    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # lunes
    days = [week_start + timedelta(days=i) for i in range(7)]
    col_labels = [d.strftime("%a %d-%b") for d in days]   # visibles
    day_key = [d.isoformat() for d in days]               # para guardar

    company = st.selectbox("Empresa", ["TODAS"] + get_companies(conn))
    zona = st.selectbox("Zona", ["TODAS"] + get_zonas(conn))
    emps_df = get_employees_df(conn,
                               None if zona=="TODAS" else zona,
                               None if company=="TODAS" else company)
    if emps_df.empty:
        st.info("No hay empleados para los filtros seleccionados.")
        st.stop()

    # Base
    grid = pd.DataFrame({"Empleado": emps_df["name"].tolist()})
    for lab in col_labels:
        grid[lab] = "—"

    # Prellenar con incidencias de la semana
    dfw = pd.read_sql_query("""
        SELECT employee, dt, inc_type
        FROM incidences
        WHERE DATE(dt) BETWEEN DATE(?) AND DATE(?)
    """, conn, params=(days[0].isoformat(), days[-1].isoformat()))
    if not dfw.empty:
        dfw["col"] = pd.to_datetime(dfw["dt"]).dt.strftime("%a %d-%b")
        last = dfw.groupby(["employee", "col"])["inc_type"].last().reset_index()
        for _, r in last.iterrows():
            if r["employee"] in grid["Empleado"].values and r["col"] in col_labels:
                grid.loc[grid["Empleado"] == r["employee"], r["col"]] = r["inc_type"]

    # Editor con select + colores por celda
    options = ["—"] + st.session_state.inc_types + ["VACACIONES"]

    # cellStyle en JS (pone color según valor)
    cell_style_js = JsCode("""
      function(params) {
        const v = (params.value || "").toUpperCase();
        const colors = {
          "FALTA":"%s","RETARDO":"%s","PERMISO":"%s","INCAPACIDAD":"%s","VACACIONES":"%s","OTRO":"%s","—":"white","": "white"
        };
        const bg = colors[v] || "white";
        return { 'backgroundColor': bg, 'textTransform': 'uppercase' };
      }
    """ % (INC_COLOR["FALTA"], INC_COLOR["RETARDO"], INC_COLOR["PERMISO"],
           INC_COLOR["INCAPACIDAD"], INC_COLOR["VACACIONES"], INC_COLOR["OTRO"]))

    gob = GridOptionsBuilder.from_dataframe(grid)
    gob.configure_column("Empleado", editable=False, pinned="left")

    for lab in col_labels:
        gob.configure_column(
            lab,
            editable=True,
            cellEditor="agSelectCellEditor",
            cellEditorParams={"values": options},
            cellStyle=cell_style_js
        )

    # Opciones generales
    gob.configure_grid_options(domLayout='autoHeight')
    grid_options = gob.build()

    grid_resp = AgGrid(
        grid,
        gridOptions=grid_options,
        update_mode=GridUpdateMode.VALUE_CHANGED,
        fit_columns_on_grid_load=True,
        allow_unsafe_jscode=True,
        height=550
    )

    if st.button("💾 Guardar cambios de la semana", type="primary", use_container_width=True):
        edited = grid_resp["data"]
        total = 0
        for _, row in edited.iterrows():
            emp = row["Empleado"]
            for lab, iso in zip(col_labels, day_key):
                val = str(row[lab]).strip().upper()
                replace_incidence_day(conn, iso, emp, val if val != "—" else "")
                if val and val != "—":
                    total += 1
        st.success(f"Cambios guardados. Incidencias registradas/actualizadas: {total}.")
        st.rerun()

elif section == "Consolidado / Exportar":
    st.header("📦 Consolidado / Exportar")

    if admin_pin_env:
        pin = st.text_input("PIN de administrador", type="password")
        if pin.strip() != admin_pin_env:
            st.warning("Ingresa el PIN para ver el consolidado.")
            st.stop()

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        start_dt = st.date_input("Desde", value=date.today().replace(day=1))
    with c2:
        end_dt = st.date_input("Hasta", value=date.today())
    with c3:
        company = st.selectbox("Empresa", ["TODAS"] + get_companies(conn))
    with c4:
        plant_filter = st.selectbox("zona", options=["TODAS"] + get_zonas(conn))

    df = read_incidents_df(conn, start_dt.isoformat(), end_dt.isoformat(), plant_filter, company)
    st.dataframe(df, use_container_width=True, height=420)

    if not df.empty:
        summary = (df
                   .groupby(["company","plant","inc_type"], as_index=False)
                   .agg(Incidencias=("inc_type", "count")))
        st.subheader("Resumen por Empresa, Planta y Tipo")
        st.dataframe(summary, use_container_width=True)

        excel_bytes = to_excel_bytes(
            df.rename(columns={"dt": "Fecha", "employee": "Empleado", "plant": "Planta",
                               "inc_type": "Tipo", "notes": "Observaciones", "company":"Empresa"}),
            summary.rename(columns={"company":"Empresa", "plant": "Planta", "inc_type": "Tipo"})
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

elif section == "Catálogo de Empleados":
    st.header("👥 Catálogo de Empleados")
    df = pd.read_sql_query("""
        SELECT name AS Empleado, company AS Empresa, plant AS Planta, hire_date AS Ingreso,
               days_per_year AS Dias_por_anio, COALESCE(rest_day,'') AS Descanso
        FROM employees ORDER BY company, plant, name;
    """, conn)
    st.dataframe(df, use_container_width=True, height=400)
    
    st.subheader("Editar empleado")
    sel = st.selectbox("Selecciona empleado", options=get_employees(conn))
    if sel:
        info = get_employee_info(conn, sel)
        c1,c2,c3 = st.columns(3)
        with c1:
            new_name = st.text_input("Nombre completo", value=info["name"])
            new_company = st.text_input("Empresa", value=info.get("company") or "")
        with c2:
            new_zona = st.text_input("Zona", value=info["plant"])
            new_rest = st.selectbox("Descanso", options=["","LUN","MAR","MIE","JUE","VIE","SAB","DOM"],
                                    index=(["","LUN","MAR","MIE","JUE","VIE","SAB","DOM"].index(info["rest_day"]) if info["rest_day"] else 0))
        with c3:
            new_hire = st.date_input("Ingreso", value=datetime.fromisoformat(info["hire_date"]).date())
            new_dpy  = st.number_input("Días/año", value=int(info["days_per_year"]), min_value=1, step=1)

        if st.button("Guardar cambios", type="primary"):
            seed_employees(conn, [{
                "name": new_name, "plant": new_zona, "hire_date": new_hire.isoformat(),
                "days_per_year": int(new_dpy), "rest_day": new_rest or None, "company": new_company or None
            }])
            # Si cambió el nombre, conviene borrar incidencias antiguas? Lo dejamos igual, solo catálogo.
            st.success("Empleado actualizado.")
            st.rerun()


    st.subheader("Agregar uno")
    c1,c2,c3,c4,c5,c6 = st.columns(6)
    with c1: n = st.text_input("Nombre (completo)")
    with c2: comp = st.text_input("Empresa (opcional)")
    with c3: p = st.selectbox("Planta", options=get_plants(conn))
    with c4: h = st.date_input("Ingreso", value=date(2024,1,1))
    with c5: dpy = st.number_input("Días/año", value=12, min_value=1, step=1)
    with c6: rd = st.selectbox("Descanso", options=["","LUN","MAR","MIE","JUE","VIE","SAB","DOM"])
    if st.button("Agregar"):
        if n.strip():
            seed_employees(conn, [{
                "name": n, "plant": p, "hire_date": h.isoformat(),
                "days_per_year": int(dpy), "rest_day": rd or None, "company": comp or None
            }])
            st.success("Empleado agregado/actualizado.")
            st.rerun()
        else:
            st.error("Nombre requerido.")

    st.subheader("Carga masiva (CSV interno)")
    st.caption("Columnas: name, plant, hire_date (YYYY-MM-DD), days_per_year, rest_day, company")
    up = st.file_uploader("Sube empleados.csv", type=["csv"])
    if up is not None:
        dfu = pd.read_csv(up)
        need = {"name","plant","hire_date","days_per_year"}
        if not need.issubset({c.lower() for c in dfu.columns}):
            st.error("El CSV debe tener al menos: name, plant, hire_date, days_per_year")
        else:
            rows=[]
            for _,r in dfu.iterrows():
                rows.append({
                    "name": str(r["name"]).upper().strip(),
                    "plant": str(r["plant"]).upper().strip(),
                    "hire_date": str(r["hire_date"])[:10],
                    "days_per_year": int(r["days_per_year"]),
                    "rest_day": (str(r.get("rest_day","")).upper().strip() or None),
                    "company": (str(r.get("company","")).upper().strip() or None)
                })
            if st.button("Cargar CSV"):
                seed_employees(conn, rows)
                st.success(f"{len(rows)} empleados cargados/actualizados.")
                st.rerun()

    st.divider()
    st.subheader("Carga masiva desde tu Excel (formato EMPRESA/ZONA/... en CSV)")
    up2 = st.file_uploader("Sube empleados_maestro.csv", type=["csv"], key="csv_maestro")
    if up2 is not None:
        try:
            dfm = pd.read_csv(up2)
            st.write("Vista previa:", dfm.head(5))
            rows = transform_employees_csv(dfm)  # transforma a nuestro esquema
            st.success(f"Archivo válido. {len(rows)} empleados listos para cargar.")
            if st.button("Cargar CSV maestro"):
                seed_employees(conn, rows)
                st.success(f"{len(rows)} empleados cargados/actualizados.")
                st.rerun()
        except Exception as e:
            st.error(f"Error al leer/transformar CSV: {e}")

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
            st.rerun()

elif section == "Vacaciones":
    st.header("🌴 Vacaciones")

    tab1, tab2 = st.tabs(["Resumen anual", "Por mes"])

    with tab1:
        dfv = vacation_status_for_all(conn)
        if dfv.empty:
            st.info("Sin empleados cargados.")
        else:
            # Colores: alerta en rojo, saldo actual >0 en verde claro
            def color_rows(r):
                if r["ALERTA"] == "⚠️":
                    return ["background-color:#ffd6d6"] * len(r)
                if r["Saldo año actual"] > 0:
                    return ["background-color:#eaf7ea"] * len(r)
                return ["" for _ in r]

            st.subheader("Resumen")
            st.dataframe(dfv.style.apply(color_rows, axis=1), use_container_width=True, height=520)

    with tab2:
        st.write("Filtra empleados según el **mes de aniversario de ingreso** y su saldo disponible.")
        mes = st.selectbox("Mes", list(range(1,13)), format_func=lambda m: date(2000, m, 1).strftime("%B").capitalize())
        dfv = vacation_status_for_all(conn)
        if dfv.empty:
            st.info("Sin empleados cargados.")
        else:
            dfv["Mes ingreso"] = pd.to_datetime(dfv["Ingreso"]).dt.month
            dfm = dfv[dfv["Mes ingreso"] == mes].copy()
            if dfm.empty:
                st.info("Nadie con aniversario en este mes.")
            else:
                dfm = dfm[["Empleado","Planta","Ingreso","Saldo año actual","Saldo año anterior","Vence saldo anterior","ALERTA"]]
                def color_rows(r):
                    if r["ALERTA"] == "⚠️":
                        return ["background-color:#ffd6d6"] * len(r)
                    if r["Saldo año actual"] > 0:
                        return ["background-color:#eaf7ea"] * len(r)
                    return ["" for _ in r]
                st.dataframe(dfm.style.apply(color_rows, axis=1), use_container_width=True, height=520)



elif section == "Gráficos":
    st.header("📊 Gráficos")
    since = st.date_input("Desde", value=date.today().replace(month=1, day=1))
    until = st.date_input("Hasta", value=date.today())
    company = st.selectbox("Empresa", ["TODAS"] + get_companies(conn), key="g_company")
    plant = st.selectbox("Planta", ["TODAS"] + get_plants(conn), key="g_plant")
    dfg = read_incidents_df(conn, since.isoformat(), until.isoformat(), plant, company)
    if dfg.empty:
        st.info("No hay datos.")
    else:
        dfg["count"] = 1
        top = (dfg[dfg["inc_type"].str.upper()=="FALTA"]
               .groupby("employee")["count"].sum()
               .sort_values(ascending=False).head(10))
        st.subheader("Top 10 FALTAS")
        st.bar_chart(top)

elif section == "Configuración":
    st.header("⚙️ Configuración")
    st.code(f"DB_PATH = '{DB_PATH}'", language="python")
    st.write("Para proteger el consolidado con un PIN, crea un archivo `.env` y define `ADMIN_PIN`.")
    st.write("**Respaldo de datos**: copia el archivo `incidencias.db`.")
    st.write("**Carga inicial de plantas**: edita `seeds/plants.json` antes del primer arranque.")
