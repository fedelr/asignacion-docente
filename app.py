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
    elif '14:00' in horario or '13:30' in horario:
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
                            ('14:00' in curso['Horario'] or '13:30' in curso['Horario']) and turno == 'TARDE' or
                            ('18:30' in curso['Horario']) and turno == 'NOCHE')
                    ) <= max_turno
            except:
                pass

    for j, docente in requisitos_df.iterrows():
        for i1, curso1 in programacion_df.iterrows():
            dias1  = [normalize_n(d.strip()).upper() for d in str(curso1['Dias De Cursada']).split('+')]
            turno1 = ('MANANA' if '08:15' in curso1['Horario'] or '07:45' in curso1['Horario'] or '09:00' in curso1['Horario']
                      else 'TARDE' if '14:00' in curso1['Horario'] or '13:30' in curso1['Horario']
                      else 'NOCHE' if '18:30' in curso1['Horario'] else None)
            if not turno1:
                continue
            for i2, curso2 in programacion_df.iterrows():
                if i1 >= i2:
                    continue
                dias2  = [normalize_n(d.strip()).upper() for d in str(curso2['Dias De Cursada']).split('+')]
                turno2 = ('MANANA' if '08:15' in curso2['Horario'] or '07:45' in curso2['Horario'] or '09:00' in curso2['Horario']
                          else 'TARDE' if '14:00' in curso2['Horario'] or '13:30' in curso2['Horario']
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
                    else 'TARDE' if '14:00' in horario or '13:30' in horario
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
                    else 'TARDE' if '14:00' in horario or '13:30' in horario else 'NOCHE' if '18:30' in horario else None)
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
        'sugerencias':       [],
    }

    # Sugerencias de cambio de día para cursos no asignados
    for _, curso in no_asignados_df.iterrows():
        clase = str(curso['Clase']).strip()
        materia = curso['Materia'].strip().upper()
        sede = curso['Sede'].strip().upper()
        modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'
        dia_actual = normalize_n(str(curso['Dias De Cursada']).strip().upper())
        turno = detectar_turno(curso['Horario'])

        valor_pack = curso.get('Packs Asociados', '')
        tiene_pack = not (pd.isna(valor_pack) or str(valor_pack).strip() in ['', '-'])
        pack_info = "⚠️ Con Pack" if tiene_pack else "✅ Sin Pack"

        if not turno:
            continue

        mejores_por_dia = {}

        for j, docente in requisitos_df.iterrows():
            nombre_docente = obtener_nombre_docente(docente, cols)

            if not modo_virtual:
                modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
                if modalidad not in modalidades_interes:
                    continue

            materias_pref = {m.strip().upper() for m in str(docente.get('Materias de preferencia para dictar', '')).split(';') if m.strip()}
            materias_alt = {m.strip().upper() for m in str(docente.get('Materias alternativas (opcional)', '')).split(';') if m.strip()}
            if materia not in materias_pref and materia not in materias_alt:
                continue

            puntaje_materia = PREF_MAT if materia in materias_pref else ALT_MAT

            if not modo_virtual and modalidad == 'PRESENCIAL':
                sedes_pref = [s.strip() for s in normalize_n(str(docente.get('Sedes preferidas', ''))).upper().split(';')]
                sedes_alt = [s.strip() for s in normalize_n(str(docente.get('Sedes alternativas (opcional)', ''))).upper().split(';')] if pd.notna(docente.get('Sedes alternativas (opcional)', '')) else []
                if sede in sedes_pref:
                    puntaje_sede = PREF_SEDE
                elif sede in sedes_alt:
                    puntaje_sede = ALT_SEDE
                else:
                    continue
            else:
                puntaje_sede = ALT_SEDE

            cursos_docente = asignados_df[asignados_df['Docentes'] == nombre_docente]
            total_actual = len(cursos_docente)
            max_total = int(docente.get('Cantidad máxima de cursos cuatrimestrales', 0) or 0)
            if total_actual >= max_total:
                continue

            total_por_modalidad = len(cursos_docente[cursos_docente['Sede'].str.strip().str.upper() == 'UADE ONLINE']) if modalidad == 'ONLINE' else len(cursos_docente[cursos_docente['Sede'].str.strip().str.upper() != 'UADE ONLINE'])
            try:
                max_mod = int(docente.get(f'Cantidad máxima de cursos {"online" if modalidad=="ONLINE" else "presenciales"} (opcional)', 0) or 0)
                if max_mod > 0 and total_por_modalidad >= max_mod:
                    continue
            except:
                pass

            dias_turnos_docente = []
            for _, c in cursos_docente.iterrows():
                dias_c = [normalize_n(d.strip()).upper() for d in str(c['Dias De Cursada']).split('+')]
                turno_c = detectar_turno(c['Horario'])
                for d in dias_c:
                    dias_turnos_docente.append((d, turno_c))

            dispo_pref = docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')
            dispo_alt = docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')
            disponibilidad_total = set()
            for bloque in [dispo_pref, dispo_alt]:
                if pd.notna(bloque):
                    disponibilidad_total.update(limpiar_valor(b) for b in str(bloque).split(';') if b.strip())

            for nuevo_dia in ['LUNES', 'MARTES', 'MIERCOLES', 'JUEVES', 'VIERNES']:
                if nuevo_dia == dia_actual:
                    continue
                clave = f"{nuevo_dia} {turno}"
                if clave not in disponibilidad_total:
                    continue
                if (nuevo_dia, turno) in dias_turnos_docente:
                    continue

                puntaje_disp = PREF_DISP if clave in {limpiar_valor(d) for d in str(dispo_pref).split(';')} else ALT_DISP
                puntaje_total = puntaje_materia + puntaje_sede + puntaje_disp

                if clave not in mejores_por_dia or puntaje_total > mejores_por_dia[clave][1]:
                    mejores_por_dia[clave] = (nombre_docente, puntaje_total)

        if mejores_por_dia:
            opciones = []
            for dia_turno, (nombre, puntaje) in sorted(mejores_por_dia.items(), key=lambda x: -x[1][1]):
                es_nuevo = nombre not in set(asignados_df['Docentes'].unique())
                opciones.append({'dia_turno': dia_turno, 'docente': nombre, 'puntaje': puntaje, 'nuevo': es_nuevo})
            resultado['sugerencias'].append({
                'clase': clase,
                'materia': materia,
                'dia_actual': dia_actual,
                'turno': turno,
                'pack_info': pack_info,
                'opciones': opciones
            })

    return resultado, requisitos_df, asignados_df, no_asignados_df, logs, False


# ─── UI ──────────────────────────────────────────────────────────────────────
st.markdown("# Asignación Docente")
st.markdown("**Autor:** Federico La Rocca")
st.markdown("Completá los campos, subí los archivos y ejecutá el sistema.")
st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Instructivo ──
with st.expander("📖 ¿Qué hace este sistema?"):
    st.markdown("Asigna automáticamente docentes a cursos usando programación lineal entera (PuLP). A partir de dos archivos CSV —uno con los requisitos de cada docente y otro con la programación de clases— el sistema encuentra la combinación de asignaciones que maximiza un puntaje compuesto, respetando todas las restricciones definidas.")

with st.expander("📋 Formularios para docentes"):
    st.markdown("""
**Formulario solo online**

Para cuatrimestres donde todos los cursos son online. No incluye preguntas de sede ni disponibilidad presencial.
👉 [Link para duplicar — Formulario online](https://www.google.com/url?q=https%3A%2F%2Fforms.office.com%2FPages%2FShareFormPage.aspx%3Fid%3D0HlJNB3TV0yLoEka_0rK7ZA9pFBFJ-BBhnD8WLkExvlUQVRISVpXMlNDMkE3M0xFWlpDRklVUUxMRS4u%26sharetoken%3DEru3ysaovbuzf98ja8Hm)

**Formulario presencial + online**

Para cuatrimestres con cursos presenciales y/o online. El docente puede indicar preferencias de sede y disponibilidad para ambas modalidades.
👉 [Link para duplicar — Formulario presencial + online](https://www.google.com/url?q=https%3A%2F%2Fforms.office.com%2FPages%2FShareFormPage.aspx%3Fid%3D0HlJNB3TV0yLoEka_0rK7ZA9pFBFJ-BBhnD8WLkExvlUMjE2VE5SN1FTMVVEWEpQT1BOODdENUtQNi4u%26sharetoken%3D17Gvd9cnDaWO0FnzR9lL)

---

**Nombres de columnas y valores**

Los nombres de las columnas del formulario **no deben modificarse bajo ningún concepto**. El sistema los busca de forma exacta, y cualquier cambio —aunque sea un espacio de más o una tilde distinta— va a provocar errores. Lo mismo aplica para los valores predefinidos de las preguntas de opción múltiple: no deben modificarse.

**Nombres de materias**

Las materias que se listen en el formulario deben escribirse **exactamente igual** a como figuran en el archivo de programación de clases.

**Columna `Apellido, Nombre`**

Esta columna puede eliminarse del formulario sin problema. Cuando el docente inicia sesión con su cuenta institucional, Microsoft Forms guarda automáticamente su nombre en la columna `Nombre`. Si preferís mantener `Apellido, Nombre` como respaldo, podés hacerlo.

**⚠️ Importante:** avisale a cada docente que complete el formulario **desde su cuenta institucional** (`@uade.edu.ar`).
    """)

with st.expander("⚙️ Antes de empezar"):
    st.markdown("""
**¿Qué archivos necesito?**

1. **Archivo de requisitos docentes** — exportación del formulario que completan los profesores.
2. **Archivo de programación de clases** — planilla con los cursos a asignar.

---

**¿Cómo obtener el CSV a partir del formulario?**

1. Abrí el formulario en Microsoft Forms → pestaña **Respuestas**
2. Hacé clic en **Abrir en Excel**
3. Guardalo como **CSV UTF-8 (delimitado por comas)**
4. Ese es el archivo que subís al sistema

---

**¿Qué formato de fecha deben tener las columnas del formulario?**

El sistema usa `Hora de finalización` para determinar la última respuesta de cada docente. El formato esperado es `dd/mm/yyyy hh:mm` (ejemplo: `23/04/2026 14:17`).

Si al abrir el archivo en Excel ves que las fechas **no tienen ese formato**, podés corregirlo antes de exportar:
1. Seleccioná la columna `Hora de finalización`
2. Clic derecho → **Formato de celdas** → **Personalizada**
3. Escribí: `dd/mm/yyyy hh:mm`
4. Aceptá y volvé a exportar como CSV UTF-8

Si el formato no coincide, el sistema intentará detectarlo automáticamente e informará en pantalla.

---

**⚠️ Importante: cómo abrir el archivo de resultados en Excel**

Si abrís `programacion_actualizada.csv` con doble clic, las tildes y la ñ se van a ver mal. La única manera correcta de abrirlo es:
1. Abrí Excel primero, sin abrir ningún archivo
2. Andá a la pestaña **Datos** → **Obtener datos externos** → **Desde texto/CSV**
3. Seleccioná el archivo y elegí codificación **UTF-8**
4. Finalizá la importación

Una vez abierto correctamente, guardalo como archivo Excel (`.xlsx`) para poder seguir trabajando con él sin perder el formato.
    """)

with st.expander("📊 Columnas del formulario y su comportamiento"):
    st.markdown("""
**¿Cómo detecta el sistema los turnos?**

El sistema determina el turno de cada curso buscando ciertos substrings dentro del campo `Horario` del archivo de programación. No parsea el horario de forma estructurada — simplemente verifica si alguno de estos valores aparece en cualquier parte del texto de ese campo:

- **Mañana:** el campo contiene `08:15`, `07:45` o `09:00`
- **Tarde:** el campo contiene `14:00` o `13:30`
- **Noche:** el campo contiene `18:30`

Por ejemplo, si el campo dice `08:15 - 11:30`, el sistema lo reconoce como mañana porque el string `08:15` está presente. Si el campo tiene un formato distinto o un horario de inicio no incluido en esta lista (por ejemplo `10:00` o `16:00`), el sistema no va a reconocer el turno y ese curso quedará fuera de las restricciones de disponibilidad y superposición.

Si notás que un docente no está matcheando con un curso, verificá que el campo `Horario` del curso contenga exactamente uno de los valores listados arriba.

---

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
    """)

with st.expander("▶ Cómo ejecutar el sistema paso a paso"):
    st.markdown("""
**Paso 1 — Subir los archivos**

Usá los botones de carga para subir los dos archivos CSV.

**Paso 2 — Ingresar el departamento**

Escribí el nombre del departamento exactamente como figura en el archivo de programación. Si lo dejás vacío, se procesarán todos los cursos.

**Paso 3 — Ejecutar**

Hacé clic en **Ejecutar asignación**. Puede tardar varios minutos. No cerrés la pestaña ni interrumpas la ejecución.

**Paso 4 — Ver los resultados y descargar**

Los resultados aparecen en pantalla. Podés descargar `programacion_actualizada.csv` con el botón de descarga.
    """)

with st.expander("🚨 Si algo sale mal"):
    st.markdown("""
**El sistema no encuentra el archivo**

Verificá que el archivo esté correctamente subido y sin caracteres especiales en el nombre.

**Las tildes y la ñ se ven mal**

- **En los archivos originales** — El CSV no está en formato UTF-8. Volvé a exportarlo eligiendo **CSV UTF-8**.
- **En el archivo de resultados** — Abrilo desde Excel → Datos → Desde texto/CSV, eligiendo codificación UTF-8.

**La deduplicación no funcionó correctamente**

Verificá que `Hora de finalización` tenga el formato `dd/mm/yyyy hh:mm`. El sistema informará si lo identificó correctamente o tuvo que inferirlo.

**El sistema dice que una columna no existe**

Los nombres de columnas no deben modificarse. Deben coincidir exactamente con los que espera el sistema.

**El resultado dice 0% de asignación o muy pocos docentes asignados**

Verificá que las disponibilidades y los nombres de materias coincidan exactamente entre el formulario y la programación.

**El sistema tarda mucho**

Es normal. No cerrés la pestaña ni interrumpas la ejecución.

**Un docente no está siendo asignado a un curso que debería poder tomar**

Verificá que el horario de inicio del curso coincida con alguno de los valores que el sistema reconoce: `08:15`, `07:45` o `09:00` para mañana, `14:00` o `13:30` para tarde, y `18:30` para noche. Si el horario es distinto, el sistema no va a reconocer el turno y no va a poder hacer el match con la disponibilidad del docente.

**Aparece un error en rojo y el sistema se detiene**

Copiá el mensaje de error y consultalo con quien administra el sistema.
    """)

st.markdown('<hr class="section-divider">', unsafe_allow_html=True)

# ── Inputs ──
col1, col2 = st.columns(2)
with col1:
    archivo_requisitos = st.file_uploader("Archivo de requisitos docentes (.csv)", type=["csv"])
with col2:
    archivo_programacion = st.file_uploader("Archivo de programación de clases (.csv)", type=["csv"])

departamento = st.text_input("Departamento", placeholder="Ej: DENET").strip().upper()

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

    # ── Sugerencias de cambio de día ──
    if resultado['sugerencias']:
        st.markdown("### 🛠️ Sugerencias para cursos no asignados")
        st.caption("Cursos sin docente que podrían resolverse cambiando el día, manteniendo el mismo turno.")
        for s in resultado['sugerencias']:
            with st.expander(f"Clase {s['clase']} — {s['materia']} | {s['dia_actual']} {s['turno']} | {s['pack_info']}"):
                for op in s['opciones']:
                    nuevo_tag = " ✳️ Docente nuevo" if op['nuevo'] else ""
                    st.markdown(f"➤ **{op['dia_turno']}** con **{op['docente']}**{nuevo_tag} — Puntaje estimado: {op['puntaje']}")

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
