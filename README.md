# Mini‑sistema de Incidencias (Streamlit + SQLite)

Un formulario web muy sencillo para que cada gerente capture **incidencias semanales** y que tú puedas **consolidarlas y exportarlas a Excel** con un clic. Sin macros, sin complicaciones.

## 🧩 Características
- **Captura de incidencias** en un formulario estandarizado.
- **Lista desplegable** de Plantas y Tipos de Incidencia.
- **Consolidado/Exportación** a Excel (incluye tabla de datos y resumen por Planta/Tipo).
- **SQLite** como base de datos local (archivo `incidencias.db`).
- **Admin PIN** opcional para proteger la vista de consolidado.

## 🚀 Cómo ejecutar
1. Instala dependencias (ideal en un entorno virtual):
   ```bash
   pip install -r requirements.txt
   ```

2. (Opcional) Copia `.env.example` a `.env` y ajusta `ADMIN_PIN` si deseas proteger el consolidado:
   ```bash
   cp .env.example .env
   ```

3. Inicia la app:
   ```bash
   streamlit run app.py
   ```

4. Abre el navegador en la URL que te muestre Streamlit (por defecto http://localhost:8501).

## 🛠️ Personalización
- **Plantas**: edítalas desde la propia app (sidebar → "Catálogo de Plantas") o modifica `seeds/plants.json` antes del primer arranque.
- **Tipos de incidencia**: puedes editar la lista en la sección de Configuración dentro de la app.

## 💾 Dónde quedan los datos
- Se guardan en `incidencias.db` (SQLite) en la misma carpeta del proyecto.
- Puedes hacer respaldo copiando ese archivo.

## 📦 Exportar a Excel
- En la sección **Consolidado / Exportar**, usa el botón **"Descargar Excel"**.
- El archivo contiene dos hojas: `Datos` (todas las incidencias) y `Resumen` (por Planta/Tipo).

---

_Creado por ChatGPT para Jose Mejía – 2025-09-29_