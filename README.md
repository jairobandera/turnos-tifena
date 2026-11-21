# Turnos Tifena

App web para armar, editar, publicar y exportar los turnos mensuales de la estación.

## Cómo usarla (en esta PC)

1. Doble clic en `run.bat` (la primera vez instala todo).
2. Empleados: <http://localhost:5000> · Encargado: <http://localhost:5000/admin>
3. Usuario inicial: `maycol` / contraseña `tifena` (la pide cambiar al entrar).

Desde el celular en el mismo wifi: `http://IP-DE-LA-PC:5000`.

## Qué hace

- **Generar**: arma el mes partiendo de lo ya armado (rotación Mañana → Noche → Tarde, bloques de 4,
  cajeros 4-4-4 sí o sí, máximo de horas de 4, tope de 6 libres, licencias). Modo *completar* (sólo días
  vacíos) o *rehacer desde una fecha* (respeta lo que tenga 🔒).
- **Editar**: arrastrar nombres entre puestos/turnos/libres, o tocar para agregar, reemplazar, mover, fijar o quitar.
  Los avisos (rojo/amarillo) se recalculan al momento.
- **Otras tareas**: el encargado puede asignarse *a sí mismo* tareas como "Corte de pasto". Ese turno trabaja
  (no suma libre) pero no ocupa puesto. Sólo para el empleado marcado como Encargado y vinculado a su usuario.
- **Guardar / Publicar**: los empleados sólo ven los meses publicados.
- **Exportar** semana o mes a PDF/Excel con el formato de la planilla (Turno 3, Turno 1, Turno 2, Libres)
  y compartir por WhatsApp (en el celular abre WhatsApp con el archivo adjunto).
- **Reporte** (sólo encargado, no se exporta): libres y horas de 4 por semana y por mes.
- **Licencias**: alta, edición e historial. Los días de licencia no cuentan como libres y el tope de libres se prorratea.

## Reglas

| Turno | Horario | Nota |
|---|---|---|
| Turno 3 (Noche) | 22:00 a 06:00 | La noche del día D empieza el día anterior a las 22:00 |
| Turno 1 (Mañana) | 06:00 a 14:00 | |
| Turno 2 (Tarde) | 14:00 a 22:00 | |

*Hora de 4* = sólo 8 h de descanso entre turnos: Tarde → Mañana, Mañana → Noche siguiente o
Noche → Tarde el mismo día (este último nunca lo usa el generador). Todo se ajusta en **Configuración**.

## Técnica

- Backend: Python + Flask + SQLite (`data/turnos.db`), generador con OR-Tools CP-SAT, Excel con openpyxl, PDF con reportlab.
- Frontend: HTML, CSS y JS sin frameworks (SortableJS para arrastrar).
- Tests: `.venv\Scripts\python.exe -m pytest tests`
- La base arranca con las planillas reales del 16 al 29/11/2026 y la licencia de Lázaro (fecha de regreso a confirmar).
