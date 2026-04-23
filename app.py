import streamlit as st
import pandas as pd
import unicodedata
import io
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpStatus

# ─── PAGE CONFIG ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Asignación Docente",
    page_icon="🎓",
    layout="centered"
)

# ─── ESTILOS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

h1, h2, h3 {
    font-family: 'DM Serif Display', serif !important;
}

.block-container {
    max-width: 760px;
    padding-top: 2.5rem;
    padding-bottom: 4rem;
}

.stButton > button {
    background-color: #1a1a2e;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 0.6rem 2rem;
    font-family: 'DM Sans', sans-serif;
    font-weight: 500;
    font-size: 1rem;
    transition: background 0.2s;
    width: 100%;
}
.stButton > button:hover {
    background-color: #16213e;
    color: white;
}

.metric-card {
    background: #f8f8f8;
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    text-align: center;
    border: 1px solid #ebebeb;
}
.metric-card .value {
    font-family: 'DM Serif Display', serif;
    font-size: 2.4rem;
    color: #1a1a2e;
    line-height: 1;
}
.metric-card .label {
    font-size: 0.82rem;
    color: #888;
    margin-top: 0.3rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.status-ok   { color: #2d8a4e; font-weight: 500; }
.status-warn { color: #c0392b; font-weight: 500; }

.section-divider {
    border: none;
    border-top: 1px solid #ebebeb;
    margin: 2rem 0;
}

.tag-online     { background:#e8f4fd; color:#1a6fa8; border-radius:4px; padding:2px 8px; font-size:0.78rem; }
.tag-presencial { background:#fef3e2; color:#b07d1a; border-radius:4px; padding:2px 8px; font-size:0.78rem; }
</style>
""", unsafe_allow_html=True)


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def normalize_n(input_str):
    input_str = input_str.replace('Ñ', 'N').replace('ñ', 'n')
    return ''.join(c for c in unicodedata.normalize('NFD', input_str) if unicodedata.category(c) != 'Mn')

def limpiar_valor(valor):
    if pd.isna(valor):
        return ''
    return normalize_n(str(valor).strip().strip('"').strip()).upper()

def detectar_turno(horario):
    horario = str(horario)
    if any(h in horario for h in ['08:15', '07:45', '09:00']):
        return 'MANANA'
    elif '14:00' in horario:
        return 'TARDE'
    elif '18:30' in horario:
        return 'NOCHE'
    return None

def obtener_nombre_docente(docente, columnas):
    if 'Nombre' in columnas:
        nombre = str(docente.get('Nombre', '')).strip()
        if nombre and nombre.lower() != 'nan':
            return nombre
    apellido_nombre = str(docente.get('Apellido, Nombre', '')).strip()
    if apellido_nombre and apellido_nombre.lower() != 'nan':
        return apellido_nombre
    return '(sin nombre)'

def es_disponible(docente, curso, turno, dias, modalidad):
    dispo_pref = docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')
    dispo_alt  = docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')
    disponibilidad_total = set()
    for bloque in [dispo_pref, dispo_alt]:
        if pd.notna(bloque):
            disponibilidad_total.update(limpiar_valor(b) for b in str(bloque).split(';') if b.strip())
    return all(f"{dia} {turno}" in disponibilidad_total for dia in dias)


# ─── FUNCIÓN PRINCIPAL DE OPTIMIZACIÓN ───────────────────────────────────────
def correr_optimizacion(requisitos_df, programacion_df, departamento_filtro,
                        PREF_MAT, PREF_SEDE, PREF_DISP,
                        ALT_MAT, ALT_SEDE, ALT_DISP,
                        VALOR_ASIGNADO_DOCENTE, NO_ASIGNADO=0):

    logs = []

    # Deduplicar por correo
    if 'Hora de finalización' in requisitos_df.columns and 'Correo electrónico' in requisitos_df.columns:
        try:
            requisitos_df['Hora de finalización'] = pd.to_datetime(
                requisitos_df['Hora de finalización'], format='%d/%m/%Y %H:%M'
            )
            logs.append("✅ Formato de fecha identificado correctamente: dd/mm/yyyy hh:mm")
        except:
            try:
                requisitos_df['Hora de finalización'] = pd.to_datetime(
                    requisitos_df['Hora de finalización'], dayfirst=True
                )
                formato_detectado = requisitos_df['Hora de finalización'].iloc[0]
                logs.append(f"⚠️ Formato de fecha inferido automáticamente. Primera fecha detectada: {formato_detectado}")
            except Exception as e:
                logs.append(f"❌ No se pudo interpretar la columna 'Hora de finalización'. La deduplicación no se aplicará. Detalle: {e}")
        con_correo  = requisitos_df[requisitos_df['Correo electrónico'].notna() & (requisitos_df['Correo electrónico'].str.strip() != '')]
        con_correo  = con_correo.sort_values('Hora de finalización').groupby('Correo electrónico', as_index=False).last()
        sin_correo  = requisitos_df[requisitos_df['Correo electrónico'].isna() | (requisitos_df['Correo electrónico'].str.strip() == '')]
        requisitos_df = pd.concat([con_correo, sin_correo], ignore_index=True)
        logs.append(f"✅ {len(requisitos_df)} docentes únicos cargados tras deduplicar respuestas.")

    # Eliminar filas donde tanto Nombre como Apellido, Nombre están vacíos
    if 'Apellido, Nombre' in requisitos_df.columns:
        requisitos_df['Apellido, Nombre'] = requisitos_df['Apellido, Nombre'].fillna('').str.strip()
    else:
        requisitos_df['Apellido, Nombre'] = ''

    if 'Nombre' in requisitos_df.columns:
        requisitos_df['Nombre'] = requisitos_df['Nombre'].fillna('').str.strip()
        requisitos_df = requisitos_df[
            (requisitos_df['Apellido, Nombre'] != '') | (requisitos_df['Nombre'] != '')
        ]
    else:
        requisitos_df = requisitos_df[requisitos_df['Apellido, Nombre'] != '']

    # Filtros programación
    try:
        programacion_df = programacion_df[programacion_df['Tipo'] == 'ASIGNATURA']
    except:
        pass
    programacion_df['Dias De Cursada'] = programacion_df['Dias De Cursada'].apply(lambda x: normalize_n(str(x)).upper())
    if departamento_filtro:
        filtrado = programacion_df[programacion_df['Departamento'] == departamento_filtro]
        if filtrado.empty:
            logs.append(f"⚠️ No se encontró el departamento '{departamento_filtro}'. Se procesarán todos los cursos.")
        else:
            programacion_df = filtrado
    sedes_validas = ['MONSERRAT', 'BELGRANO', 'RECOLETA', 'UADE ONLINE']
    programacion_df['Sede'] = programacion_df['Sede'].str.strip().str.upper()
    programacion_df = programacion_df[programacion_df['Sede'].isin(sedes_validas)]

    if programacion_df.empty:
        return None, None, None, None, ["❌ No se encontraron cursos para el departamento indicado."], True

    # Modo virtual
    modo_virtual = 'Sedes preferidas' not in requisitos_df.columns
    if modo_virtual:
        logs.append("⚠️ Modo todo-online activado.")

    # Variables LP
    asignacion = {(i, j): LpVariable(f"x_{i}_{j}", cat='Binary') for i in programacion_df.index for j in requisitos_df.index}
    usado      = {j: LpVariable(f"usado_{j}", cat='Binary') for j in requisitos_df.index}
    problema   = LpProblem("AsignacionOptima", LpMaximize)
    puntajes   = []

    for j in requisitos_df.index:
        problema += lpSum(asignacion[(i, j)] for i in programacion_df.index) >= usado[j]
    for i in programacion_df.index:
        problema += lpSum(asignacion[(i, j)] for j in requisitos_df.index) <= 1
    for j, docente in requisitos_df.iterrows():
        max_total = int(docente.get('Cantidad máxima de cursos cuatrimestrales', 0) or 0)
        problema += lpSum(asignacion[(i, j)] for i in programacion_df.index) <= max_total

    for j, docente in requisitos_df.iterrows():
        try:
            max_virt = int(docente['Cantidad máxima de cursos online (opcional)'])
            problema += lpSum(asignacion[(i, j)] for i, curso in programacion_df.iterrows() if curso['Sede'].upper().strip() == 'UADE ONLINE') <= max_virt
        except:
            pass
        if not modo_virtual:
            try:
                max_pres = int(docente['Cantidad máxima de cursos presenciales (opcional)'])
                problema += lpSum(asignacion[(i, j)] for i, curso in programacion_df.iterrows() if curso['Sede'].upper().strip() != 'UADE ONLINE') <= max_pres
            except:
                pass

    for j, docente in requisitos_df.iterrows():
        for turno, col in [('MANANA', 'Cantidad máxima de cursos a la mañana (opcional)'),
                           ('TARDE',  'Cantidad máxima de cursos a la tarde (opcional)'),
                           ('NOCHE',  'Cantidad máxima de cursos a la noche (opcional)')]:
            try:
                max_turno = int(docente.get(col, '').strip())
                if max_turno > 0:
                    problema += lpSum(
                        asignacion[(i, j)]
                        for i, curso in programacion_df.iterrows()
                        if (('08:15' in curso['Horario'] or '07:45' in curso['Horario'] or '09:00' in curso['Horario']) and turno == 'MANANA' or
                            ('14:00' in curso['Horario']) and turno == 'TARDE' or
                            ('18:30' in curso['Horario']) and turno == 'NOCHE')
                    ) <= max_turno
            except:
                pass

    for j, docente in requisitos_df.iterrows():
        for i1, curso1 in programacion_df.iterrows():
            dias1  = [normalize_n(d.strip()).upper() for d in str(curso1['Dias De Cursada']).split('+')]
            turno1 = ('MANANA' if '08:15' in curso1['Horario'] or '07:45' in curso1['Horario'] or '09:00' in curso1['Horario']
                      else 'TARDE' if '14:00' in curso1['Horario']
                      else 'NOCHE' if '18:30' in curso1['Horario'] else None)
            if not turno1:
                continue
            for i2, curso2 in programacion_df.iterrows():
                if i1 >= i2:
                    continue
                dias2  = [normalize_n(d.strip()).upper() for d in str(curso2['Dias De Cursada']).split('+')]
                turno2 = ('MANANA' if '08:15' in curso2['Horario'] or '07:45' in curso2['Horario'] or '09:00' in curso2['Horario']
                          else 'TARDE' if '14:00' in curso2['Horario']
                          else 'NOCHE' if '18:30' in curso2['Horario'] else None)
                if turno1 != turno2 or not turno2:
                    continue
                if any(d1 in dias2 for d1 in dias1):
                    problema += asignacion[(i1, j)] + asignacion[(i2, j)] <= 1

    for i, curso in programacion_df.iterrows():
        dias     = [normalize_n(d.strip()).upper() for d in str(curso['Dias De Cursada']).split('+')]
        horario  = curso['Horario'].upper()
        sede     = curso['Sede'].strip().upper()
        materia  = curso['Materia'].strip().upper()
        turno    = ('MANANA' if ('08:15' in horario or '07:45' in horario or '09:00' in horario)
                    else 'TARDE' if '14:00' in horario
                    else 'NOCHE' if '18:30' in horario else None)
        modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'

        for j, docente in requisitos_df.iterrows():
            materias_pref = {m.strip().upper() for m in str(docente['Materias de preferencia para dictar']).split(';') if m.strip()}
            materias_alt  = set()
            if 'Materias alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Materias alternativas (opcional)', '')):
                materias_alt = {m.strip().upper() for m in str(docente['Materias alternativas (opcional)']).split(';') if m.strip()}

            if materia not in materias_pref and materia not in materias_alt:
                problema += asignacion[(i, j)] == 0
                continue
            if not es_disponible(docente, curso, turno, dias, modalidad):
                problema += asignacion[(i, j)] == 0
                continue
            if not modo_virtual:
                modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
                if modalidad not in modalidades_interes:
                    problema += asignacion[(i, j)] == 0
                    continue

            if not modo_virtual and modalidad == 'PRESENCIAL':
                sedes_pref = [s.strip() for s in normalize_n(str(docente['Sedes preferidas'])).upper().split(';')]
                sedes_alt  = []
                if 'Sedes alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Sedes alternativas (opcional)', '')):
                    sedes_alt = [s.strip() for s in normalize_n(str(docente['Sedes alternativas (opcional)'])).upper().split(';')]
                if sede not in sedes_pref and sede not in sedes_alt:
                    problema += asignacion[(i, j)] == 0
                    continue

            puntaje = 0
            puntaje += PREF_MAT if materia in materias_pref else ALT_MAT

            if not modo_virtual and modalidad == 'PRESENCIAL':
                sedes_pref = [s.strip() for s in normalize_n(str(docente['Sedes preferidas'])).upper().split(';')]
                sedes_alt  = []
                if 'Sedes alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Sedes alternativas (opcional)', '')):
                    sedes_alt = [s.strip() for s in normalize_n(str(docente['Sedes alternativas (opcional)'])).upper().split(';')]
                puntaje += PREF_SEDE if sede in sedes_pref else (ALT_SEDE if sede in sedes_alt else 0)
            else:
                puntaje += ALT_SEDE

            claves    = [f"{dia} {turno}" for dia in dias]
            dispo_pref_set = {limpiar_valor(d) for d in str(docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')).split(';') if d.strip()}
            dispo_alt_set  = {limpiar_valor(d) for d in str(docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')).split(';') if d.strip()}

            if all(k in dispo_pref_set for k in claves):
                puntaje += PREF_DISP
            elif all(k in dispo_alt_set for k in claves):
                puntaje += ALT_DISP
            else:
                problema += asignacion[(i, j)] == 0
                continue

            puntajes.append(asignacion[(i, j)] * puntaje)

    for i in programacion_df.index:
        puntajes.append(NO_ASIGNADO * (1 - lpSum(asignacion[(i, j)] for j in requisitos_df.index)))
    for j in requisitos_df.index:
        puntajes.append(VALOR_ASIGNADO_DOCENTE * usado[j])

    problema += lpSum(puntajes)
    problema.solve()

    status = LpStatus[problema.status]
    if status == 'Infeasible':
        return None, None, None, None, logs + ["❌ El problema es inviable. No se pudieron cumplir todas las restricciones."], False

    # Asignar nombres
    cols = list(requisitos_df.columns)
    programacion_df['Docentes'] = ''
    for i, curso in programacion_df.iterrows():
        for j, docente in requisitos_df.iterrows():
            if asignacion[(i, j)].value() == 1:
                programacion_df.at[i, 'Docentes'] = obtener_nombre_docente(docente, cols)
                break

    programacion_df['Docentes'] = programacion_df['Docentes'].fillna('').astype(str).apply(str.strip)
    asignados_df    = programacion_df[programacion_df['Docentes'] != '']
    no_asignados_df = programacion_df[programacion_df['Docentes'] == '']

    # Resumen por docente
    resumen_docentes = []
    for docente_nombre in asignados_df['Docentes'].unique():
        cursos    = asignados_df[asignados_df['Docentes'] == docente_nombre]
        cantidad  = len(cursos)
        # Buscar por Nombre primero, si no por Apellido, Nombre
        docente_match = pd.DataFrame()
        if 'Nombre' in cols:
            docente_match = requisitos_df[requisitos_df['Nombre'].astype(str).str.strip() == docente_nombre]
        if docente_match.empty:
            docente_match = requisitos_df[requisitos_df['Apellido, Nombre'].astype(str).str.strip() == docente_nombre]
        max_val   = docente_match['Cantidad máxima de cursos cuatrimestrales'].astype(float).fillna(0).values
        max_total = int(max_val[0]) if len(max_val) > 0 else 0
        resumen_docentes.append({
            'Docente': docente_nombre,
            'Asignados': cantidad,
            'Máximo': max_total,
            'OK': cantidad <= max_total
        })

    # Docentes sin cursos
    todos          = requisitos_df.apply(lambda r: obtener_nombre_docente(r, cols), axis=1).dropna().astype(str).apply(str.strip).unique()
    con_curso      = set(asignados_df['Docentes'].unique())
    sin_asignacion = [d for d in todos if d not in con_curso]

    # Cursos sin docentes elegibles
    cursos_sin_docentes = []
    for i, curso in programacion_df.iterrows():
        clase    = str(curso['Clase']).strip()
        materia  = curso['Materia'].strip().upper()
        sede     = curso['Sede'].strip().upper()
        horario  = curso['Horario'].upper()
        dia      = normalize_n(str(curso['Dias De Cursada']).split('+')[0].strip()).upper()
        turno    = ('MANANA' if ('08:15' in horario or '07:45' in horario or '09:00' in horario)
                    else 'TARDE' if '14:00' in horario else 'NOCHE' if '18:30' in horario else None)
        modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'
        elegibles = 0
        for j, docente in requisitos_df.iterrows():
            mp = {m.strip().upper() for m in str(docente.get('Materias de preferencia para dictar', '')).split(';') if m.strip()}
            ma = {m.strip().upper() for m in str(docente.get('Materias alternativas (opcional)', '')).split(';') if m.strip()}
            if materia not in mp and materia not in ma:
                continue
            if not es_disponible(docente, curso, turno, [dia], modalidad):
                continue
            if not modo_virtual and modalidad == 'PRESENCIAL':
                sp = [s.strip().upper() for s in normalize_n(str(docente.get('Sedes preferidas', ''))).split(';') if s.strip()]
                sa = [s.strip().upper() for s in normalize_n(str(docente.get('Sedes alternativas (opcional)', ''))).split(';') if s.strip()]
                if sede not in sp and sede not in sa:
                    continue
            if not modo_virtual:
                mi = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
                if modalidad not in mi:
                    continue
            elegibles += 1
        if elegibles == 0:
            cursos_sin_docentes.append(f"Clase {clase}: {materia} ({sede} — {turno} — {dia})")

    resultado = {
        'programacion_df':   programacion_df,
        'asignados_df':      asignados_df,
        'no_asignados_df':   no_asignados_df,
        'resumen_docentes':  resumen_docentes,
        'sin_asignacion':    sin_asignacion,
        'cursos_sin_docentes': cursos_sin_docentes,
        'modo_virtual':      modo_virtual,
    }

    return resultado, requisitos_df, asignados_df, no_asignados_df, logs, False


# ─── UI ──────────────────────────────────────────────────────────────────────
st.markdown("# Asignación Docente")
st.markdown("**Fecha:** 23/04/2025 · **Autor:** Federico La Rocca")
st.markdown("Completá los campos, subí los archivos y ejecutá el sistema.")
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Instructivo ──
with st.expander("📖 Instrucciones y documentación"):
    st.markdown("""
## ¿Qué hace este sistema?

Asigna automáticamente docentes a cursos usando programación lineal entera (PuLP). A partir de dos archivos CSV —uno con los requisitos de cada docente y otro con la programación de clases— el sistema encuentra la combinación de asignaciones que maximiza un puntaje compuesto, respetando todas las restricciones definidas.

---

## Formularios para docentes

El sistema requiere que cada docente complete un formulario con sus preferencias y disponibilidad. A continuación se proveen dos plantillas según el contexto de asignación.

**Formulario solo online**
Para cuatrimestres donde todos los cursos son online. No incluye preguntas de sede ni disponibilidad presencial.
👉 [Link para duplicar — Formulario online](#)

**Formulario presencial + online**
Para cuatrimestres con cursos presenciales y/o online. El docente puede indicar preferencias de sede y disponibilidad para ambas modalidades.
👉 [Link para duplicar — Formulario presencial + online](#)

---

## Instrucciones para configurar el formulario

**Nombres de columnas y valores**
Los nombres de las columnas del formulario **no deben modificarse bajo ningún concepto**. El sistema los busca de forma exacta, y cualquier cambio —aunque sea un espacio de más o una tilde distinta— va a provocar errores. Lo mismo aplica para los valores predefinidos de las preguntas de opción múltiple (turnos, sedes, modalidades): no deben modificarse.

**Nombres de materias**
Las materias que se listen en el formulario deben escribirse **exactamente igual** a como figuran en el archivo de programación de clases. Una diferencia mínima (mayúsculas, tildes, abreviaturas) va a hacer que el sistema no reconozca la materia y no asigne al docente a ese curso.

**Columna `Apellido, Nombre`**
Esta columna puede eliminarse del formulario sin problema. Cuando el docente inicia sesión con su cuenta institucional, Microsoft Forms guarda automáticamente su nombre en la columna `Nombre`, que es la que usa el sistema en primer lugar. Si preferís mantener `Apellido, Nombre` como respaldo, podés hacerlo. También es útil si necesitás agregar manualmente algún docente que no completó el formulario.

> ⚠️ **Importante:** avisale a cada docente que complete el formulario **desde su cuenta institucional** (`@uade.edu.ar`). Si lo completa desde una cuenta personal, el nombre y el correo electrónico no van a quedar registrados correctamente, lo que puede afectar la deduplicación de respuestas.

---

## Antes de empezar

### ¿Qué archivos necesito?

Necesitás tener dos archivos en formato **CSV**:

1. **Archivo de requisitos docentes** — es la exportación del formulario que completan los profesores. Lo descargás desde Microsoft Forms como Excel y luego lo guardás como CSV.
2. **Archivo de programación de clases** — es la planilla con los cursos a asignar, exportada también como CSV.

### ¿Cómo obtener el CSV a partir del formulario?

1. Abrí el formulario en Microsoft Forms y hacé clic en la pestaña **Respuestas**
2. Hacé clic en **Abrir en Excel** — esto descarga directamente un archivo Excel con todas las respuestas
3. Abrí ese archivo en Excel
4. Guardalo como **CSV UTF-8 (delimitado por comas)** — es importante elegir específicamente esta opción para que las tildes y la ñ se vean correctamente
5. Ese es el archivo que subís al sistema

### ¿Cómo debe estar separado el CSV?

El sistema espera que los valores estén separados por **punto y coma (;)**. Si al abrir el CSV en el Bloc de Notas ves que los valores están separados por comas, tenés que corregirlo antes de usarlo:

1. Abrí el archivo en Excel
2. Guardalo como **CSV UTF-8 (delimitado por comas)**
3. Abrilo en el Bloc de Notas
4. Hacé Ctrl+H (buscar y reemplazar), buscá `,` y reemplazá por `;`
5. Guardá

### ¿Qué formato de fecha deben tener las columnas del formulario?

El sistema usa la columna `Hora de finalización` para determinar cuál es la última respuesta de cada docente en caso de que haya respondido más de una vez. El formato preferido es `dd/mm/yyyy hh:mm` (por ejemplo: `23/04/2026 14:17`). Si al abrir el archivo en Excel ves que las fechas tienen otro formato, podés corregirlo antes de exportar el CSV:

1. Seleccioná la columna `Hora de finalización`
2. Hacé clic derecho → **Formato de celdas**
3. Elegí la categoría **Personalizada**
4. En el campo de formato escribí: `dd/mm/yyyy hh:mm`
5. Aceptá y volvé a exportar el archivo como CSV UTF-8

Si el formato no es exactamente ese, el sistema va a intentar detectarlo automáticamente e informará en pantalla si lo logró correctamente.

### ¿Qué nombres deben tener los archivos?

Podés nombrarlos como quieras, pero **sin espacios ni caracteres especiales** (sin tildes, sin ñ, sin paréntesis).

---

## Columnas del formulario y su comportamiento en el sistema

| Columna | ¿Obligatoria? | ¿Qué pasa si falta o está vacía? |
|---|---|---|
| `Nombre` | No | Se usa `Apellido, Nombre` como respaldo |
| `Apellido, Nombre` | Solo si no hay `Nombre` | Si ambas faltan, el docente no se carga |
| `Correo electrónico` | No | El docente se incluye pero no se deduplica |
| `Hora de finalización` | No | No se deduplican respuestas múltiples |
| `Materias de preferencia para dictar` | **Sí** | Sin materias, el docente no puede ser asignado |
| `Materias alternativas (opcional)` | No | Solo se consideran las materias preferidas |
| `Cantidad máxima de cursos cuatrimestrales` | **Sí** | Sin este valor, el sistema asigna 0 cursos |
| `Disponibilidad online preferida` | **Sí** (modo online) | Sin disponibilidad, el docente no puede ser asignado |
| `Disponibilidad online alternativa (opcional)` | No | Solo se considera la disponibilidad preferida |
| `Disponibilidad presencial preferida` | **Sí** (modo presencial) | Sin disponibilidad, el docente no puede ser asignado |
| `Disponibilidad presencial alternativa (opcional)` | No | Solo se considera la disponibilidad preferida |
| `Sedes preferidas` | **Sí** (modo presencial) | Su ausencia activa el modo todo-online automáticamente |
| `Sedes alternativas (opcional)` | No | Solo se consideran las sedes preferidas |
| `Modalidades de su interés` | No (modo online) | En modo todo-online se omite; en modo mixto es obligatoria |
| `Cantidad máxima de cursos online (opcional)` | No | Sin este valor no hay límite por modalidad online |
| `Cantidad máxima de cursos presenciales (opcional)` | No | Sin este valor no hay límite por modalidad presencial |
| `Cantidad máxima de cursos a la mañana (opcional)` | No | Sin este valor no hay límite por turno mañana |
| `Cantidad máxima de cursos a la tarde (opcional)` | No | Sin este valor no hay límite por turno tarde |
| `Cantidad máxima de cursos a la noche (opcional)` | No | Sin este valor no hay límite por turno noche |

---

## Cómo ejecutar el sistema paso a paso

**Paso 1 — Subir los archivos**
Usá los botones de carga para subir los dos archivos CSV.

**Paso 2 — Ingresar el departamento**
Escribí el nombre del departamento exactamente como figura en el archivo de programación. Si lo dejás vacío, se procesarán todos los cursos.

**Paso 3 — Ejecutar**
Hacé clic en **Ejecutar asignación**. El sistema puede tardar varios minutos dependiendo de la cantidad de docentes y cursos. No cerrés la pestaña ni interrumpas la ejecución.

**Paso 4 — Ver los resultados y descargar**
Una vez finalizado, los resultados aparecen en pantalla. Podés descargar el archivo `programacion_actualizada.csv` con el botón de descarga.

> ⚠️ **Cómo abrir correctamente el archivo de resultados en Excel**
> Si abrís el archivo `programacion_actualizada.csv` con doble clic, Excel va a mostrar mal las tildes, la ñ y otros caracteres especiales. La manera correcta de abrirlo es:
> 1. Abrí Excel primero, sin abrir ningún archivo
> 2. Andá a la pestaña **Datos**
> 3. Hacé clic en **Obtener datos externos** → **Desde texto/CSV**
> 4. Seleccioná el archivo y asegurate de elegir codificación **UTF-8**
> 5. Finalizá la importación

---

## Si algo sale mal

**El sistema no encuentra el archivo**
Verificá que el nombre del archivo no tenga caracteres especiales y que esté correctamente subido.

**Las tildes y la ñ se ven mal**

- **Dentro del sistema o en el análisis (el CSV que subís tiene caracteres rotos)**
  El archivo CSV no está en formato UTF-8. Volvé a exportarlo desde Excel eligiendo específicamente **CSV UTF-8**.

- **En el archivo de resultados que descargás (`programacion_actualizada.csv`)**
  El archivo está correctamente codificado, pero Excel lo interpreta mal cuando se abre con doble clic. Seguí los pasos de la sección anterior para abrirlo correctamente.

**La deduplicación de respuestas no funcionó correctamente**
Verificá que la columna `Hora de finalización` tenga el formato `dd/mm/yyyy hh:mm`. Si no, corregilo en Excel siguiendo los pasos de la sección "¿Qué formato de fecha deben tener las columnas del formulario?". El sistema informará en pantalla si el formato fue identificado correctamente o si tuvo que inferirlo automáticamente.

**El sistema dice que una columna no existe**
Los nombres de las columnas en el CSV deben coincidir exactamente con los que espera el sistema. No los modifiques.

**El resultado dice 0% de asignación o muy pocos docentes asignados**
Puede deberse a que las disponibilidades declaradas por los docentes no coinciden con ningún curso de la programación, o a que los nombres de las materias en el formulario no coinciden exactamente con los de la programación.

**El sistema tarda mucho**
Es normal. Dependiendo de la cantidad de docentes y cursos, puede tardar varios minutos. No cerrés la pestaña ni interrumpas la ejecución.

**Aparece un error en rojo y el sistema se detiene**
Copiá el mensaje de error y consultalo con quien administra el sistema. No modifiques el código por tu cuenta.
    """)

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Inputs ──
col1, col2 = st.columns(2)
with col1:
    archivo_requisitos = st.file_uploader("Archivo de requisitos docentes (.csv)", type=["csv"])
with col2:
    archivo_programacion = st.file_uploader("Archivo de programación de clases (.csv)", type=["csv"])

departamento = st.text_input("Departamento", placeholder="Ej: MARKETING").strip().upper()

# ── Configuración avanzada ──
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)
with st.expander("⚙️ Configuración avanzada de puntajes"):
    st.markdown("""
    El sistema asigna un puntaje a cada combinación docente–curso según tres factores: **materia**, **sede** y **disponibilidad**.
    Cada factor puede ser *preferido* (primera opción del docente) o *alternativo* (segunda opción).
    El sistema busca maximizar el puntaje total de todas las asignaciones.
    También hay un puntaje extra por cada docente que recibe al menos un curso, para incentivar que se use la mayor cantidad posible de docentes.

    Modificar estos valores afecta las prioridades del sistema. Se recomienda dejar los valores predeterminados salvo que haya una razón específica.
    """)
    c1, c2, c3 = st.columns(3)
    with c1:
        PREF_MAT  = st.number_input("Materia preferida",        value=500, step=10)
        ALT_MAT   = st.number_input("Materia alternativa",      value=60,  step=10)
    with c2:
        PREF_SEDE = st.number_input("Sede preferida",           value=100, step=10)
        ALT_SEDE  = st.number_input("Sede alternativa",         value=60,  step=10)
    with c3:
        PREF_DISP = st.number_input("Disponibilidad preferida", value=80,  step=10)
        ALT_DISP  = st.number_input("Disponibilidad alternativa", value=60, step=10)
    VALOR_DOCENTE = st.number_input("Bonus por docente nuevo", value=60, step=10,
        help="Puntaje extra que se suma la primera vez que se asigna un curso a un docente.")

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Botón ejecutar ──
ejecutar = st.button("▶ Ejecutar asignación")

if ejecutar:
    errores_validacion = []
    if not archivo_requisitos:
        errores_validacion.append("Falta el archivo de requisitos docentes.")
    if not archivo_programacion:
        errores_validacion.append("Falta el archivo de programación de clases.")
    if not departamento:
        errores_validacion.append("Ingresá el nombre del departamento.")

    if errores_validacion:
        for e in errores_validacion:
            st.error(e)
        st.stop()

    try:
        requisitos_df    = pd.read_csv(archivo_requisitos,    delimiter=';', dtype=str)
        programacion_df  = pd.read_csv(archivo_programacion,  delimiter=';', dtype=str)
    except Exception as e:
        st.error(f"No se pudo leer uno de los archivos. Verificá que estén en formato CSV UTF-8 separado por punto y coma. Detalle: {e}")
        st.stop()

    with st.spinner("Analizando y optimizando... esto puede tardar unos minutos."):
        resultado, req_df, asignados_df, no_asignados_df, logs, inviable = correr_optimizacion(
            requisitos_df, programacion_df, departamento,
            PREF_MAT, PREF_SEDE, PREF_DISP,
            ALT_MAT, ALT_SEDE, ALT_DISP,
            VALOR_DOCENTE
        )

    for log in logs:
        st.info(log)

    if inviable or resultado is None:
        st.stop()

    prog_df = resultado['programacion_df']

    # ── Métricas principales ──
    total        = len(prog_df)
    asignados    = len(resultado['asignados_df'])
    no_asignados = len(resultado['no_asignados_df'])
    pct          = (asignados / total * 100) if total else 0

    st.markdown("## Resultados")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.markdown(f'<div class="metric-card"><div class="value">{total}</div><div class="label">Cursos totales</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="metric-card"><div class="value">{asignados}</div><div class="label">Asignados</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="metric-card"><div class="value">{no_asignados}</div><div class="label">Sin asignar</div></div>', unsafe_allow_html=True)
    with m4:
        st.markdown(f'<div class="metric-card"><div class="value">{pct:.0f}%</div><div class="label">Cobertura</div></div>', unsafe_allow_html=True)

    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

    # ── Docentes ──
    st.markdown("### Docentes")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"**{len(resultado['resumen_docentes'])}** docentes con al menos un curso asignado")
    with col_b:
        st.markdown(f"**{len(resultado['sin_asignacion'])}** docentes sin ningún curso")

    if resultado['resumen_docentes']:
        filas = []
        for r in resultado['resumen_docentes']:
            estado = "✅" if r['OK'] else "⚠️"
            filas.append({"": estado, "Docente": r['Docente'], "Asignados": r['Asignados'], "Máximo": r['Máximo']})
        st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

    if resultado['sin_asignacion']:
        with st.expander(f"👥 Docentes sin cursos asignados ({len(resultado['sin_asignacion'])})"):
            for d in resultado['sin_asignacion']:
                st.markdown(f"- {d}")

    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

    # ── Cursos sin asignar ──
    if no_asignados > 0:
        st.markdown("### Cursos sin asignar")
        with st.expander(f"Ver los {no_asignados} cursos sin docente"):
            for _, c in resultado['no_asignados_df'].iterrows():
                st.markdown(f"- Clase **{str(c['Clase']).strip()}** — {c['Materia'].strip()} ({c['Sede'].strip()} — {detectar_turno(c['Horario'])} — {c['Dias De Cursada'].strip()})")

    if resultado['cursos_sin_docentes']:
        with st.expander(f"⚠️ Cursos sin ningún docente elegible ({len(resultado['cursos_sin_docentes'])})"):
            st.caption("Estos cursos no tienen ningún docente que cumpla con las condiciones requeridas, independientemente de la asignación.")
            for c in resultado['cursos_sin_docentes']:
                st.markdown(f"- {c}")

    st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

    # ── Descarga ──
    st.markdown("### Descargar resultado")
    csv_out = prog_df.to_csv(index=False, sep=';').encode('utf-8-sig')
    st.download_button(
        label="⬇️ Descargar programacion_actualizada.csv",
        data=csv_out,
        file_name="programacion_actualizada.csv",
        mime="text/csv"
    )
