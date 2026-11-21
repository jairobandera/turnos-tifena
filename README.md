# Turnos Tifena

App web para armar, editar, publicar y exportar los turnos mensuales de la estación.

## Cómo usarla (en esta PC)

1. Doble clic en `run.bat` (la primera vez instala todo).
2. Empleados: <http://localhost:5000> · Encargado: <http://localhost:5000/admin>
3. Usuario del encargado: `maycol`. En una instalación nueva, la contraseña inicial se toma de la
   variable de entorno `TURNOS_ADMIN_PASSWORD` al crear la base (si no está definida es `tifena` y la pide cambiar).

Desde el celular en el mismo wifi: `http://IP-DE-LA-PC:5000`.

## Qué hace

- **Generar**: arma el mes partiendo de lo ya armado (rotación Mañana → Noche → Tarde, bloques de 4,
  cajeros 4-4-4 sí o sí, máximo de horas de 4, tope de 6 libres, licencias). Modo *completar* (sólo días
  vacíos) o *rehacer desde una fecha* (respeta lo que tenga 🔒).
- **Editar**: arrastrar nombres entre puestos/turnos/libres, o tocar para agregar, reemplazar, mover, fijar o quitar.
  Los avisos (rojo/amarillo) se recalculan al momento.
- **Otras tareas**: el encargado puede asignarse *a sí mismo* tareas como "Corte de pasto". Ese turno trabaja
  (no suma libre) pero no ocupa puesto. Sólo para el empleado marcado como Encargado y vinculado a su usuario.
- **Armar mes siguiente**: arma el mes que viene completo siguiendo la rotación del actual (queda en borrador).
- **Sugerencias 💡**: detecta días en que conviene sacar a Soledad del spot a limpieza (puesto *Ocasional*) o
  pasar al encargado a otras tareas cuando sobra gente. Cada sugerencia se puede **Simular** (vista previa de la
  semana con los cambios en verde y antes/después de avisos y horas de 4) y **Aceptar**: reacomoda desde ese día
  hasta fin de mes cambiando lo mínimo y guarda.
- **Cierres de la estación** (Configuración): por defecto 24/12 y 31/12 de 20:00 a 20:00 del día siguiente, todos
  los años. Ese día no se asigna a nadie ni cuenta como libre; la tarde del 24/12 y del 31/12 termina a las 20:00 y la noche
  del 26/12 y del 02/01 entra a las 20:00 (10 horas).
- **Turnos fijos por empleado** (Empleados): qué turnos puede hacer cada uno (Belén sólo mañana, Soledad sólo tarde, Maycol nunca noche) y si es **jornalero**: cubre a los demás y no tiene tope de libres (Soledad 15 días por mes en el spot de la tarde; Fabiana cubre los libres de Silvia y Valeria). Nadie más pasa de sus 6 libres: si sobra gente, el encargado va a *Otros servicios* esos días.
- **Sin refuerzos**: cada turno lleva exactamente su dotación (nunca 2 en el spot ni 3 pisteros). Los pisteros extra sólo en los **días especiales** que se cargan en Configuración (fecha, turno, cuántos, todos los años o una vez).
- **Encargado fuera de planilla** (Configuración, apagado por defecto): al generar, el encargado queda en *Otros servicios* y no ocupa puesto; si falta alguien, el hueco queda marcado para que se ponga él a mano.
- **Guardar / Publicar**: los empleados sólo ven los meses publicados.
- **Exportar** semana o mes a PDF/Excel con el formato de la planilla (Turno 3, Turno 1, Turno 2, Libres)
  y compartir por WhatsApp (en el celular abre WhatsApp con el archivo adjunto).
- **Reporte** (sólo encargado, no se exporta): libres y horas de 4 por semana y por mes.
- **Licencias**: alta, edición e historial. Si al guardar una licencia esa persona ya tenía turnos armados en esos días, la app ofrece **Simular** el reacomodo (y queda como sugerencia 💡). Los días de licencia no cuentan como libres y el tope de libres se prorratea.

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
