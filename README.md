# GPS Vendedores

Sistema de seguimiento GPS en vivo para vendedores de Tucumán y Catamarca.

## Qué hace

- **Vendedor**: abre su link en el celular, pulsa "Iniciar envío" y el GPS se envía automáticamente cada 10 segundos. Puede registrar los PDV que visita con el botón "Registrar PDV visitado", que además muestra **"Mi ruta de hoy"** con los PDV que le tocan ese día para ir marcándolos. La pantalla no se apaga mientras envía (Wake Lock) y se puede hacer zoom/pinch en el mapa y en la página.
- **Panel**: mapa en vivo con la posición de todos los vendedores, estado (activo/sin señal), historial del recorrido del día, botón para copiar los links de cada vendedor y, en Configuración, la lista de PDV visitados por cada colaborador y día, más el ruteo semanal por merchan.
- **Panel – merchan**: al tocar el nombre de un merchan, el mapa salta al **PDV más cercano** a su última posición (marcador rojo, con distancia, razón social y ruta de venta). Cada tarjeta muestra además las **visitas del día**, el **PDV más cercano** y cuántos PDV le tocan **hoy** (destacado con HOY).
- Los PDV (clientes) se muestran en el mapa con el toggle "Mostrar PDV (clientes)" (apagado por defecto) y el botón "📍 Zoom a la provincia seleccionada" acerca la vista a los vendedores del filtro.

## Links

- Panel de control: `https://TU_APP.onrender.com/` (protegido con PIN)
- Página del vendedor: `https://TU_APP.onrender.com/tracker/CODIGO`

## Códigos de vendedores

| Código | Provincia | Vendedor |
|---|---|---|
| CARRIZO-01 | CATAMARCA | CARRIZO |
| ERAUSQUIN-02 | CATAMARCA | ERAUSQUIN |
| MAYA-03 | CATAMARCA | MAYA |
| NINA-04 | CATAMARCA | NINA |
| OVEJERO-05 | CATAMARCA | OVEJERO |
| PABLO-06 | CATAMARCA | PABLO |
| RAMIREZ-07 | CATAMARCA | RAMIREZ |
| SORIA-08 | CATAMARCA | SORIA |
| ANDRADE-09 | TUCUMAN | ANDRADE |
| APAS-10 | TUCUMAN | APAS |
| CALL-11 | TUCUMAN | CALL |
| CAMPOS-12 | TUCUMAN | CAMPOS |
| CASIVA-13 | TUCUMAN | CASIVA |
| DFERNANDEZ-14 | TUCUMAN | DFERNANDEZ |
| DIAZ-15 | TUCUMAN | DIAZ |
| FERNANDEZ-16 | TUCUMAN | FERNANDEZ |
| FRIAS-17 | TUCUMAN | FRIAS |
| GALBORNOZ-18 | TUCUMAN | GALBORNOZ |
| GALVAN-19 | TUCUMAN | GALVAN |
| GONZALEZ-20 | TUCUMAN | GONZALEZ |
| LUCENA-21 | TUCUMAN | LUCENA |
| MARTINEZ-22 | TUCUMAN | MARTINEZ |
| MELONI-23 | TUCUMAN | MELONI |
| MORALES-24 | TUCUMAN | MORALES |
| MORENO-25 | TUCUMAN | MORENO |
| MUNOZ-26 | TUCUMAN | MUÑOZ |
| ORELLANA-27 | TUCUMAN | ORELLANA |
| PEREZ-28 | TUCUMAN | PEREZ |
| RODRIGUEZ-29 | TUCUMAN | RODRIGUEZ |
| SALINAS-30 | TUCUMAN | SALINAS |

### Usuarios (merchan)

| Código | Provincia | Nombre |
|---|---|---|
| CORBALAN-31 | TUCUMAN | Facundo Corbalan |
| AGUILAR-32 | TUCUMAN | Gonzalo Aguilar |
| LAZO-33 | TUCUMAN | Carlos Lazo |
| ABIB-34 | TUCUMAN | Matias Abib |
| EMETERIO-35 | TUCUMAN | Matias Emeterio |
| VERA-36 | TUCUMAN | Santiago Vera |
| SILVA-37 | TUCUMAN | Leonardo Silva |
| MADRID-38 | TUCUMAN | Augusto Madrid |
| ALBONOZ-39 | TUCUMAN | Julian Albonoz |
| DAVID-40 | CATAMARCA | Saul David |

## Despliegue en Render (gratis)

1. Subí la carpeta `gps-vendedores` a un repositorio de GitHub.
2. En [render.com](https://render.com) creá una cuenta (gratis).
3. **New + → Blueprint** y conectá el repositorio (usa `render.yaml`).
4. Render crea el servicio. Los valores de `SECRET_KEY` y `DASH_PIN` se generan automáticamente:
   - Para ver el PIN del panel: Render → tu servicio → **Environment** → variable `DASH_PIN`.
   - Podés cambiarlo ahí y hacer Deploy.
5. Cuando el deploy termine, entrá a la URL `https://TU_APP.onrender.com/` con el PIN.

**Importante sobre el plan gratis de Render:**
- El servicio se "duerme" tras ~15 min sin uso; la primera visita después de dormirse tarda ~1 min en responder.
- El historial se guarda en un archivo SQLite **efímero**: se pierde si se vuelve a desplegar. Para historial permanente, cambiá a una base Postgres o al plan pago.
- El GPS del celular funciona porque Render entrega HTTPS (requisito obligatorio de los navegadores).

## Probar en local (opcional)

```powershell
python -m pip install -r requirements.txt
python app.py
```

- Panel: http://localhost:5000 (PIN por defecto: `1234`)
- Simular un vendedor: http://localhost:5000/tracker/MORENO-25 (el GPS funciona en localhost)
- O enviar una posición con:
  ```
  Invoke-RestMethod -Method Post -Uri http://localhost:5000/api/track -ContentType application/json -Body '{"code":"MORENO-25","lat":-26.93,"lon":-65.35}'
  ```

## Configuración por variables de entorno

| Variable | Default | Uso |
|---|---|---|
| `SECRET_KEY` | `cambiar-esta-clave-por-una-segura` | Clave de sesión de Flask |
| `DASH_PIN` | `1234` | PIN para entrar al panel |
| `DB_PATH` | `gps.db` | Ruta del archivo SQLite |
| `PORT` | `5000` | Puerto del servidor |

## Registro de PDV visitados

- El vendedor marca sus visitas desde su tracker (`/tracker/<codigo>` → "Registrar PDV visitado"), buscando por razón social, número de cliente, calle o ruta. La sección **"Mi ruta de hoy"** le muestra los PDV asignados para ese día y los marca/quita con un toque.
- El panel las consulta en **Configuración ⚙ → "PDV visitados por día"** (elige colaborador y fecha).
- API: `POST /api/visitas`, `GET /api/visitas?code=X&fecha=YYYY-MM-DD`, `DELETE /api/visitas`, `GET /api/visitas/resumen` (cantidad por vendedor).
- `pdv.json` incluye por cada cliente: `c` (número de cliente), `r` (razón social), `calle`, `altura`, `lat`, `lon`, `prov` y `vta` (ruta de venta / vendedor).

## Ruteo semanal por merchan

- El panel muestra en **Configuración ⚙ → "PDV asignados por día (ruteo semanal)"** qué PDV le toca visitar a cada merchan y qué día (Lunes a Sábado); el día actual aparece resaltado con "HOY". El ruteo también alimenta el popup del merchan y la sección "Mi ruta de hoy" del tracker.
- La asignación se genera desde los Excel de ruta semanal de la carpeta `C:\Users\gborrasar\Desktop\ruteos` (cada archivo se identifica por el nombre del merchan; se leen solo las hojas de día, se ignora la de Faltantes).
- Para regenerar después de actualizar los Excel: `python gen_rutas.py` (escribe `rutas.json` con la estructura `merchan → día → [[código cliente, razón social]]`).
- La razón social se completa desde `pdv.json` por número de cliente; si el cliente no está en el maestro, se usa la razón social que trae el Excel.
- API: `GET /api/rutas?merchan=LAZO-33&dia=MIERCOLES` (filtros opcionales), `GET /api/nearest-pdv?code=X` (PDV más cercano a la última posición), `GET /api/merchan-pdv` (PDV más cercano de todos los vendedores).

## Cómo funciona

1. El vendedor abre `/tracker/<codigo>` en el celular (HTTPS) y pulsa **Iniciar envío**.
2. El navegador pide permiso de ubicación y reporta posición cada ~10 s a `POST /api/track`.
3. El panel consulta `GET /api/positions` cada 5 s y muestra cada vendedor en el mapa.
4. `GET /api/history?code=X&days=1` devuelve el recorrido del día para dibujar la ruta.
