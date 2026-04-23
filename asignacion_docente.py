!pip install pulp
import pandas as pd
import unicodedata
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, LpStatus


# Función para normalizar nombres
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

def obtener_nombre_docente(docente):
    if 'Nombre' in requisitos_df.columns:
        nombre = str(docente.get('Nombre', '')).strip()
        if nombre and nombre.lower() != 'nan':
            return nombre
    apellido_nombre = str(docente.get('Apellido, Nombre', '')).strip()
    if apellido_nombre and apellido_nombre.lower() != 'nan':
        return apellido_nombre
    return '(sin nombre)'

# Ingreso de archivos
requisitos_file = input("Ingrese el nombre del archivo de requisitos docentes: ")
programacion_file = input("Ingrese el nombre del archivo de programación de clases: ")
departamento_filtro = input("Departamento: ").upper()

# Cargar datos
requisitos_df = pd.read_csv(requisitos_file, delimiter=';', dtype=str)
programacion_df = pd.read_csv(programacion_file, delimiter=';', dtype=str)

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

# Quedarse solo con la última respuesta de cada docente (por correo electrónico)
if 'Hora de finalización' in requisitos_df.columns and 'Correo electrónico' in requisitos_df.columns:
    requisitos_df['Hora de finalización'] = pd.to_datetime(
        requisitos_df['Hora de finalización'], format='%d/%m/%Y %H:%M'
    )
    # Docentes con correo: quedarse con la última respuesta
    con_correo = requisitos_df[requisitos_df['Correo electrónico'].notna() & (requisitos_df['Correo electrónico'].str.strip() != '')]
    con_correo = con_correo.sort_values('Hora de finalización').groupby('Correo electrónico', as_index=False).last()
    # Docentes sin correo: incluir todos
    sin_correo = requisitos_df[requisitos_df['Correo electrónico'].isna() | (requisitos_df['Correo electrónico'].str.strip() == '')]
    requisitos_df = pd.concat([con_correo, sin_correo], ignore_index=True)
    print(f"✅ Respuestas procesadas: {len(requisitos_df)} docentes únicos cargados.")

# Filtros
try:
  programacion_df = programacion_df[programacion_df['Tipo'] == 'ASIGNATURA']
except:
  pass
programacion_df['Dias De Cursada'] = programacion_df['Dias De Cursada'].apply(lambda x: normalize_n(str(x)).upper())
if departamento_filtro:
    filtrado = programacion_df[programacion_df['Departamento'] == departamento_filtro]
    if filtrado.empty:
        print(f"⚠️ No se encontró el departamento '{departamento_filtro}' en el archivo de programación. Se procesarán todos los cursos.")
    else:
        programacion_df = filtrado
sedes_validas = ['MONSERRAT', 'BELGRANO', 'RECOLETA', 'UADE ONLINE']
programacion_df['Sede'] = programacion_df['Sede'].str.strip().str.upper()
programacion_df = programacion_df[programacion_df['Sede'].isin(sedes_validas)]

# Detectar modo de operación
modo_virtual = 'Sedes preferidas' not in requisitos_df.columns
if modo_virtual:
    print("⚠️ Modo todo-virtual activado: no se encontraron columnas de sede ni disponibilidad presencial.")

# Variables de decisión
asignacion = {(i, j): LpVariable(f"x_{i}_{j}", cat='Binary') for i in programacion_df.index for j in requisitos_df.index}
usado = {j: LpVariable(f"usado_{j}", cat='Binary') for j in requisitos_df.index}

# Problema de optimización
problema = LpProblem("AsignacionOptima", LpMaximize)

# Puntajes función objetivo
PREF = 100
ALT = 60
NO_ASIGNADO = 0
VALOR_ASIGNADO_DOCENTE = 60

# Puntajes específicos
PREF_DISP = PREF
ALT_DISP = ALT

PREF_SEDE = PREF
ALT_SEDE = ALT

PREF_MAT = PREF
ALT_MAT = ALT

# Modelo calidad
PREF_MAT = 500
PREF_SEDE = 100
PREF_DISP = 80
ALT_DISP = 60
ALT_MAT = 60
ALT_SEDE = 60
VALOR_ASIGNADO_DOCENTE = 60


# Inputs
print('\n\nPUNTAJES‼️ (EXPLICACIÓN) (INGRESE ENTER PARA OMITIR)')
print(f'Cuando se puede asignar un docente, se evalúa si cada factor (disponibilidad, materia y sede) es preferido o alternativo.\nCada uno suma al puntaje total del sistema.\nEl sistema busca maximizar el puntaje total, de todas las asignaciones posibles de todos los docentes.')
print(f'También, hay un puntaje extra que se otorga en la primera asignación de cada docente, para incentivar que se usen más docentes.')
print(f'Modificar los puntajes va a afectar en la combinación de asignaciones resultante.\nPor ejemplo, si el puntaje por docente nuevo es muy alto en comparación a los otros valores, el sistema va a priorizar usar más cantidad de docentes, dejando en un segundo plano otras cuestiones.\n')
print(f'\nPUNTAJES PREDETERMINADOS')
print(f'''  PREF_MAT: {PREF_MAT}
  PREF_SEDE: {PREF_SEDE}
  PREF_DISP: {PREF_DISP}
  ALT_DISP: {ALT_DISP}
  ALT_MAT: {ALT_MAT}
  ALT_SEDE: {ALT_SEDE}
  VALOR_DOCENTE_NUEVO: {VALOR_ASIGNADO_DOCENTE}''')
print('\nA continuación puede elegir modificar los puntajes del sistema:')

valor_config = int(input('Ingrese:\n- Enter para continuar con los valores predeterminados (RECOMENDADO)\n- (1) para determinar puntajes preferidos y alternativos de manera simple\n- (2) para modificar cada puntaje\n') or 0)
if valor_config == 1:
  PREF = int(input(f'Puntaje para cada factor de preferencia, o Enter para omitir (predeterminado {PREF}): ') or PREF)
  ALT = int(input(f'Puntaje para cada factor alternativo, o Enter para omitir (predeterminado {ALT}): ') or ALT)
  VALOR_ASIGNADO_DOCENTE = int(input(f'Puntaje extra por usar docente nuevo (docente con 0 asignaciones), o Enter para omitir (predeterminado {VALOR_ASIGNADO_DOCENTE}): ') or VALOR_ASIGNADO_DOCENTE)
  PREF_DISP = PREF
  ALT_DISP = ALT
  PREF_SEDE = PREF
  ALT_SEDE = ALT
  PREF_MAT = PREF
  ALT_MAT = ALT
elif valor_config == 2:
  print('Puede omitir y dejar el valor predeterminado presionando Enter')
  PREF_DISP = int(input(f'Horarios PREF (predeterminado {PREF_DISP}): ') or PREF_DISP)
  PREF_MAT = int(input(f'Materias PREF (predeterminado {PREF_MAT}): ') or PREF_MAT)
  PREF_SEDE = int(input(f'Sedes PREF (predeterminado {PREF_SEDE}): ') or PREF_SEDE)
  ALT_DISP = int(input(f'Horarios ALT (predeterminado {ALT_DISP}): ') or ALT_DISP)
  ALT_MAT = int(input(f'Materias ALT (predeterminado {ALT_MAT}): ') or ALT_MAT)
  ALT_SEDE = int(input(f'Sedes ALT (predeterminado {ALT_SEDE}): ') or ALT_SEDE)
  VALOR_ASIGNADO_DOCENTE = int(input(f'Puntaje extra por usar un docente nuevo (docente con 0 asignaciones) (predeterminado {VALOR_ASIGNADO_DOCENTE}): ') or VALOR_ASIGNADO_DOCENTE)
else:
  print('Se continuó con los valores predeterminados')


print(f'\nANALIZANDO... 🔄\n')

puntajes = []


# Restricción: usado[j] = 1 si el docente tiene asignación
for j in requisitos_df.index:
    problema += lpSum(asignacion[(i, j)] for i in programacion_df.index) >= usado[j]

# Restricción: Un curso solo puede ser asignado a un docente
for i in programacion_df.index:
    problema += lpSum(asignacion[(i, j)] for j in requisitos_df.index) <= 1

# Restricción: Máximo total de cursos por docente
for j, docente in requisitos_df.iterrows():
    max_total = int(docente.get('Cantidad máxima de cursos cuatrimestrales', 0) or 0)
    problema += lpSum(asignacion[(i, j)] for i in programacion_df.index) <= max_total

# Restricciones por modalidad
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

# Restricción: Cantidad máxima de cursos por turno (mañana, tarde, noche)
for j, docente in requisitos_df.iterrows():
    for turno, columna_turno in [('MANANA', 'Cantidad máxima de cursos a la mañana (opcional)'),
                                 ('TARDE', 'Cantidad máxima de cursos a la tarde (opcional)'),
                                 ('NOCHE', 'Cantidad máxima de cursos a la noche (opcional)')]:
        try:
            max_turno = int(docente.get(columna_turno, '').strip())
            if max_turno > 0:
                problema += lpSum(
                    asignacion[(i, j)]
                    for i, curso in programacion_df.iterrows()
                    if (
                        ('08:15' in curso['Horario'] or '07:45' in curso['Horario'] or '09:00' in curso['Horario']) and turno == 'MANANA' or
                        ('14:00' in curso['Horario']) and turno == 'TARDE' or
                        ('18:30' in curso['Horario']) and turno == 'NOCHE'
                    )
                ) <= max_turno
        except:
            pass


# Restricción: No superposición de turnos por docente y día
for j, docente in requisitos_df.iterrows():
    print(f"Revisando posibles superposiciones para el docente {obtener_nombre_docente(docente)}")
    for i1, curso1 in programacion_df.iterrows():
        dias1 = [normalize_n(d.strip()).upper() for d in str(curso1['Dias De Cursada']).split('+')]
        turno1 = ('MANANA' if '08:15' in curso1['Horario'] or '07:45' in curso1['Horario'] or '09:00' in curso1['Horario'] else
                  'TARDE' if '14:00' in curso1['Horario'] else
                  'NOCHE' if '18:30' in curso1['Horario'] else
                  None)

        if not turno1:
            continue

        for i2, curso2 in programacion_df.iterrows():
            if i1 >= i2:
                continue

            dias2 = [normalize_n(d.strip()).upper() for d in str(curso2['Dias De Cursada']).split('+')]
            turno2 = ('MANANA' if '08:15' in curso2['Horario'] or '07:45' in curso2['Horario'] or '09:00' in curso2['Horario'] else
                      'TARDE' if '14:00' in curso2['Horario'] else
                      'NOCHE' if '18:30' in curso2['Horario'] else
                      None)

            if turno1 != turno2 or not turno2:
                continue

            if any(d1 in dias2 for d1 in dias1):
                problema += asignacion[(i1, j)] + asignacion[(i2, j)] <= 1


# Función de disponibilidad
def es_disponible(docente, curso, turno, dias, modalidad):
    dispo_pref = docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')
    dispo_alt = docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')
    disponibilidad_total = set()
    for bloque in [dispo_pref, dispo_alt]:
        if pd.notna(bloque):
            disponibilidad_total.update(limpiar_valor(b) for b in str(bloque).split(';') if b.strip())
    return all(f"{dia} {turno}" in disponibilidad_total for dia in dias)


for i, curso in programacion_df.iterrows():
    dias = [normalize_n(d.strip()).upper() for d in str(curso['Dias De Cursada']).split('+')]
    horario = curso['Horario'].upper()
    sede = curso['Sede'].strip().upper()
    materia = curso['Materia'].strip().upper()

    turno = ('MANANA' if ('08:15' in horario or '07:45' in horario or '09:00' in horario) else 'TARDE' if '14:00' in horario
             else 'NOCHE' if '18:30' in horario else None)

    modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'

    for j, docente in requisitos_df.iterrows():
        materias_preferidas = {m.strip().upper() for m in str(docente['Materias de preferencia para dictar']).split(';') if m.strip()}
        materias_alternativas = set()
        if 'Materias alternativas (opcional)' in requisitos_df.columns and pd.notna(docente['Materias alternativas (opcional)']):
            materias_alternativas = {m.strip().upper() for m in str(docente['Materias alternativas (opcional)']).split(';') if m.strip()}

        if materia not in materias_preferidas and materia not in materias_alternativas:
            problema += asignacion[(i, j)] == 0
            continue

        if not es_disponible(docente, curso, turno, dias, modalidad):
            problema += asignacion[(i, j)] == 0
            continue

        # Validar modalidad de interés (solo si hay cursos presenciales)
        if not modo_virtual:
            modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
            if modalidad not in modalidades_interes:
                problema += asignacion[(i, j)] == 0
                continue

        # Validación de sede (solo si no estamos en modo todo-virtual)
        if not modo_virtual and modalidad == 'PRESENCIAL':
            sedes_preferidas = [s.strip() for s in normalize_n(str(docente['Sedes preferidas'])).upper().split(';')]
            sedes_alternativas = []
            if 'Sedes alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Sedes alternativas (opcional)', '')):
                sedes_alternativas = [s.strip() for s in normalize_n(str(docente['Sedes alternativas (opcional)'])).upper().split(';')]
            if sede not in sedes_preferidas and sede not in sedes_alternativas:
                problema += asignacion[(i, j)] == 0
                continue

        # PUNTAJES
        puntaje = 0
        if materia in materias_preferidas:
            puntaje += PREF_MAT
        elif materia in materias_alternativas:
            puntaje += ALT_MAT

        # Puntaje por sede o modalidad
        if not modo_virtual and modalidad == 'PRESENCIAL':
            sedes_preferidas = [s.strip() for s in normalize_n(str(docente['Sedes preferidas'])).upper().split(';')]
            sedes_alternativas = []
            if 'Sedes alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Sedes alternativas (opcional)', '')):
                sedes_alternativas = [s.strip() for s in normalize_n(str(docente['Sedes alternativas (opcional)'])).upper().split(';')]
            if sede in sedes_preferidas:
                puntaje += PREF_SEDE
            elif sede in sedes_alternativas:
                puntaje += ALT_SEDE
        else:
            puntaje += ALT_SEDE

        claves = [f"{dia} {turno}" for dia in dias]

        dispo_pref = {
            limpiar_valor(d)
            for d in str(docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')).split(';')
            if d.strip()
        }

        dispo_alt = {
            limpiar_valor(d)
            for d in str(docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')).split(';')
            if d.strip()
        }

        if all(k in dispo_pref for k in claves):
            puntaje += PREF_DISP
        elif all(k in dispo_alt for k in claves):
            puntaje += ALT_DISP
        else:
            problema += asignacion[(i, j)] == 0
            continue

        puntajes.append(asignacion[(i, j)] * puntaje)

# Penalización por no asignar curso
for i in programacion_df.index:
    puntajes.append(NO_ASIGNADO * (1 - lpSum(asignacion[(i, j)] for j in requisitos_df.index)))

# Incentivo por cada docente que fue asignado a al menos un curso
for j in requisitos_df.index:
    puntajes.append(VALOR_ASIGNADO_DOCENTE * usado[j])

problema += lpSum(puntajes)

# Resolver
problema.solve()
print(f'\n{LpStatus[problema.status]}')
if LpStatus[problema.status] == 'Infeasible':
    print("\n❌ El problema es inviable. No se pudieron cumplir todas las restricciones.")



programacion_df['Docentes'] = ''
for i, curso in programacion_df.iterrows():
    for j, docente in requisitos_df.iterrows():
        if asignacion[(i, j)].value() == 1:
            programacion_df.at[i, 'Docentes'] = obtener_nombre_docente(docente)
            break

# Guardar resultado
programacion_df.to_csv("programacion_actualizada.csv", index=False, sep=';')
print("Resultado guardado en 'programacion_actualizada.csv'.\n")

# Limpieza de columna 'Docentes'
programacion_df['Docentes'] = programacion_df['Docentes'].fillna('').astype(str).apply(lambda x: x.strip())

asignados_df = programacion_df[programacion_df['Docentes'] != '']
no_asignados_df = programacion_df[programacion_df['Docentes'] == '']

total_cursos = len(programacion_df)
total_asignados = len(asignados_df)
total_no_asignados = len(no_asignados_df)
porcentaje_asignados = (total_asignados / total_cursos) * 100 if total_cursos else 0

print(f"✅ Cursos asignados: {total_asignados}")
print(f"📌 Total de cursos: {total_cursos}")
print(f"📈 Porcentaje de asignación: {porcentaje_asignados:.2f}%")
print(f"❌ Cursos sin asignar: {total_no_asignados}")
print("\n📊 Análisis de asignaciones por docente:")

docentes_asignados = asignados_df['Docentes'].unique()

for docente in docentes_asignados:
    cursos = asignados_df[asignados_df['Docentes'] == docente]
    cantidad = len(cursos)
    # Buscar por Nombre primero, si no por Apellido, Nombre
    docente_match = pd.DataFrame()
    if 'Nombre' in requisitos_df.columns:
        docente_match = requisitos_df[requisitos_df['Nombre'].astype(str).str.strip() == docente]
    if docente_match.empty:
        docente_match = requisitos_df[requisitos_df['Apellido, Nombre'].str.strip() == docente]
    max_val = docente_match['Cantidad máxima de cursos cuatrimestrales'].astype(float).fillna(0).values
    max_total = int(max_val[0]) if len(max_val) > 0 else 0
    estado = "⚠️" if cantidad > max_total else "✅"
    print(f"{estado} Docente: {docente} | Cursos Asignados: {cantidad} | Máximo Permitido: {max_total}")

# Verificar superposiciones horarias
superposiciones = []

for j, docente in requisitos_df.iterrows():
    dias_y_turnos = []

    for i, curso in programacion_df.iterrows():
        if asignacion[(i, j)].value() == 1:
            dias = [normalize_n(d.strip()).upper() for d in str(curso['Dias De Cursada']).split('+')]
            turno = (
                'MANANA' if '08:15' in curso['Horario'] or '07:45' in curso['Horario'] or '09:00' in curso['Horario']
                else 'TARDE' if '14:00' in curso['Horario']
                else 'NOCHE' if '18:30' in curso['Horario']
                else None
            )

            if turno:
                for dia in dias:
                    dias_y_turnos.append((dia, turno))

    duplicados = [item for item in dias_y_turnos if dias_y_turnos.count(item) > 1]

    if duplicados:
        nombre = obtener_nombre_docente(docente)
        superposiciones.append(nombre)
        print(f"❌ Docente con superposición horaria: {nombre}")
        print("  Superposiciones encontradas en los siguientes días y turnos:")
        for item in set(duplicados):
            print(f"    Día: {item[0]}, Turno: {item[1]}")

if not superposiciones:
    print("\n✅ No se encontraron superposiciones horarias entre los docentes.")


def verificar_asignaciones():
    errores = []

    for i, curso in programacion_df.iterrows():
        materia = curso['Materia'].strip().upper()
        sede = curso['Sede'].strip().upper()
        modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'
        dias = [normalize_n(d.strip()).upper() for d in str(curso['Dias De Cursada']).split('+')]
        turno = ('MANANA' if '08:15' in curso['Horario'] or '07:45' in curso['Horario'] or '09:00' in curso['Horario'] else
                 'TARDE' if '14:00' in curso['Horario'] else
                 'NOCHE' if '18:30' in curso['Horario'] else
                 None)

        for j, docente in requisitos_df.iterrows():
            if asignacion[(i, j)].value() == 1:
                docente_nombre = obtener_nombre_docente(docente)

                dispo_pref = docente.get(f'Disponibilidad {modalidad.lower()} preferida', '')
                dispo_alt = docente.get(f'Disponibilidad {modalidad.lower()} alternativa (opcional)', '')
                disponibilidad_total = set()
                for bloque in [dispo_pref, dispo_alt]:
                    if pd.notna(bloque):
                        disponibilidad_total.update(limpiar_valor(b) for b in str(bloque).split(';') if b.strip())

                if not all(f"{dia} {turno}" in disponibilidad_total for dia in dias):
                    errores.append(f"❌ Docente {docente_nombre} no está disponible para el curso {materia} ({sede}) en los días y turnos: {dias} {turno}.")
                    continue

                # Verificar sede solo si no estamos en modo todo-virtual
                if not modo_virtual and modalidad == 'PRESENCIAL':
                    sedes_preferidas = [s.strip() for s in normalize_n(str(docente['Sedes preferidas'])).upper().split(';')]
                    sedes_alternativas = []
                    if 'Sedes alternativas (opcional)' in requisitos_df.columns and pd.notna(docente.get('Sedes alternativas (opcional)', '')):
                        sedes_alternativas = [s.strip() for s in normalize_n(str(docente['Sedes alternativas (opcional)'])).upper().split(';')]
                    if sede not in sedes_preferidas and sede not in sedes_alternativas:
                        errores.append(f"❌ Docente {docente_nombre} no tiene la sede preferida o alternativa para el curso {materia} ({sede}).")
                        continue

                # Verificar modalidad de interés solo si no estamos en modo todo-online
                if not modo_virtual:
                    modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
                    if modalidad not in modalidades_interes:
                        errores.append(f"❌ Docente {docente_nombre} no está interesado en la modalidad {modalidad}. Clase {str(curso['Clase']).strip()}")
                        continue

                if modalidad == 'PRESENCIAL' and sede == 'UADE ONLINE':
                    errores.append(f"❌ Docente {docente_nombre} está asignado a un curso presencial en modalidad online para {materia}.")
                    continue

                if modalidad == 'ONLINE' and sede != 'UADE ONLINE':
                    errores.append(f"❌ Docente {docente_nombre} está asignado a un curso online en modalidad presencial para {materia}.")
                    continue

    if errores:
        print("\n🚫 Se encontraron asignaciones incorrectas:")
        for error in errores:
            print(error)
    else:
        print("✅ Todas las asignaciones son correctas según las restricciones.")

verificar_asignaciones()

todos_docentes = requisitos_df.apply(lambda r: obtener_nombre_docente(r), axis=1).dropna().astype(str).apply(str.strip).unique()
docentes_con_curso = set(docentes_asignados)
docentes_sin_asignacion = [d for d in todos_docentes if d not in docentes_con_curso]
print(f"\n👥 Docentes sin cursos asignados: {len(docentes_sin_asignacion)}")

if docentes_sin_asignacion:
    for d in docentes_sin_asignacion:
        print(f"❌ {d}")
else:
    print("✅ Todos los docentes recibieron al menos un curso.")

# Verificación de cursos no elegibles
cursos_sin_docentes = []

for i, curso in programacion_df.iterrows():
    clase = str(curso['Clase']).strip()
    materia = curso['Materia'].strip().upper()
    sede = curso['Sede'].strip().upper()
    horario = curso['Horario'].upper()
    dia = normalize_n(str(curso['Dias De Cursada']).split('+')[0].strip()).upper()

    turno = ('MANANA' if ('08:15' in horario or '07:45' in horario or '09:00' in horario) else 'TARDE' if '14:00' in horario
             else 'NOCHE' if '18:30' in horario else None)

    modalidad = 'ONLINE' if sede == 'UADE ONLINE' else 'PRESENCIAL'

    elegibles = 0

    for j, docente in requisitos_df.iterrows():
        materias_pref = {m.strip().upper() for m in str(docente.get('Materias de preferencia para dictar', '')).split(';') if m.strip()}
        materias_alt = {m.strip().upper() for m in str(docente.get('Materias alternativas (opcional)', '')).split(';') if m.strip()}

        if materia not in materias_pref and materia not in materias_alt:
            continue

        if not es_disponible(docente, curso, turno, [dia], modalidad):
            continue

        if not modo_virtual and modalidad == 'PRESENCIAL':
            sedes_pref = [s.strip().upper() for s in normalize_n(str(docente.get('Sedes preferidas', ''))).split(';') if s.strip()]
            sedes_alt = [s.strip().upper() for s in normalize_n(str(docente.get('Sedes alternativas (opcional)', ''))).split(';') if s.strip()]
            if sede not in sedes_pref and sede not in sedes_alt:
                continue

        if not modo_virtual:
            modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
            if modalidad not in modalidades_interes:
                continue

        elegibles += 1

    if elegibles == 0:
        cursos_sin_docentes.append(f"Clase {clase}: {materia} ({sede} - {turno} - {dia})")

print(f"\n📌 Cursos sin docentes elegibles: {len(cursos_sin_docentes)}")
for curso in cursos_sin_docentes:
    print("-", curso)

print("\n🛠️ Sugerencias para cursos no asignados que podrían cambiar de día (mismo turno):")

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
        nombre_docente = obtener_nombre_docente(docente)

        if not modo_virtual:
            modalidades_interes = {m.strip().upper() for m in str(docente.get('Modalidades de su interés', '')).split(';') if m.strip()}
            if modalidad not in modalidades_interes:
                continue

        materias_pref = {m.strip().upper() for m in str(docente.get('Materias de preferencia para dictar', '')).split(';') if m.strip()}
        materias_alt = {m.strip().upper() for m in str(docente.get('Materias alternativas (opcional)', '')).split(';') if m.strip()}
        if materia not in materias_pref and materia not in materias_alt:
            continue

        puntaje_materia = PREF_MAT if materia in materias_pref else ALT_MAT

        # Puntaje por sede (solo si no estamos en modo todo-virtual)
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
        print()
        print(f"Clase {clase} | {materia} | {dia_actual} {turno} | {pack_info}")
        for dia_turno, (docente, puntaje) in sorted(mejores_por_dia.items(), key=lambda x: -x[1][1]):
            if docente not in docentes_asignados:
                print(f"    ➤ {dia_turno} con {docente} (✳️ Nuevo) (Puntaje estimado: {puntaje})")
            else:
                print(f"    ➤ {dia_turno} con {docente} (Puntaje estimado: {puntaje})")
