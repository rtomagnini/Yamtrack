# Filtro de Episodios por Estado de Visualización

## Resumen de Cambios

Se ha implementado un sistema de filtrado de episodios en la página de detalles de temporada que permite a los usuarios filtrar los episodios por su estado de visualización.

## Funcionalidades Agregadas

### 1. **Filtros Disponibles**
- **Todos**: Muestra todos los episodios de la temporada (comportamiento por defecto)
- **Vistos**: Muestra solo los episodios que han sido marcados como vistos
- **No vistos**: Muestra solo los episodios que no han sido vistos

### 2. **Interfaz de Usuario**
- Botones de filtro en la parte superior de la sección "Episodes"
- Diseño responsivo que se adapta a dispositivos móviles
- Estado visual activo para el filtro seleccionado
- Mensajes informativos cuando no hay episodios para mostrar con el filtro seleccionado

### 3. **URL y Parámetros**
- El filtro se controla mediante el parámetro `filter` en la URL
- Ejemplos de URLs:
  - `http://192.168.68.106:8337/details/tmdb/tv/63367/equipo-de-investigacion/season/17` (todos)
  - `http://192.168.68.106:8337/details/tmdb/tv/63367/equipo-de-investigacion/season/17?filter=unwatched` (no vistos)
  - `http://192.168.68.106:8337/details/tmdb/tv/63367/equipo-de-investigacion/season/17?filter=watched` (vistos)

## Archivos Modificados

### 1. `src/app/views.py` - Vista `season_details`
**Cambios realizados:**
- Agregado procesamiento del parámetro `filter` desde `request.GET`
- Implementada lógica de filtrado de episodios basada en el campo `history`
- Agregado `current_filter` al contexto del template

**Lógica de filtrado:**
```python
episode_filter = request.GET.get("filter", "all")
if episode_filter == "unwatched":
    season_metadata["episodes"] = [
        episode for episode in season_metadata["episodes"]
        if not episode.get("history")
    ]
elif episode_filter == "watched":
    season_metadata["episodes"] = [
        episode for episode in season_metadata["episodes"]
        if episode.get("history")
    ]
```

### 2. `src/templates/app/media_details.html`
**Cambios realizados:**
- Agregados botones de filtro con navegación por URL
- Implementada lógica condicional para mostrar/ocultar contenido
- Agregados mensajes informativos para estados sin resultados
- Preservación de otros parámetros GET en los enlaces de filtro

**Características de la interfaz:**
- Botones con estilo Tailwind CSS consistente con el diseño existente
- Diseño responsivo (flex-col en móvil, flex-row en escritorio)
- Estados visuales activos e inactivos
- Transiciones suaves

## Comportamiento del Sistema

### Determinación del Estado de los Episodios
Un episodio se considera "visto" si:
- Tiene entradas en el campo `history` (lista no vacía)

Un episodio se considera "no visto" si:
- No tiene entradas en el campo `history` (lista vacía o null)

### Mensajes de Estado
- **No vistos**: "No hay episodios sin ver en esta temporada."
- **Vistos**: "No hay episodios vistos en esta temporada."
- **Todos**: "No hay episodios disponibles para esta temporada."

### Preservación de Estado
- Los filtros preservan otros parámetros GET existentes en la URL
- El filtro activo se mantiene visible en la interfaz
- La funcionalidad es compatible con el sistema de navegación existente

## Compatibilidad

- ✅ Compatible con fuentes TMDB y Manual
- ✅ Mantiene la funcionalidad existente de seguimiento de episodios
- ✅ Preserva todos los enlaces y funcionalidades existentes
- ✅ Diseño responsivo compatible con el sistema actual
- ✅ No afecta otras páginas o funcionalidades del sistema

## Ejemplo de Uso

1. El usuario navega a una página de temporada
2. Ve todos los episodios por defecto
3. Hace clic en "No vistos" para ver solo episodios sin marcar
4. Puede alternar entre filtros sin perder el contexto
5. Los filtros se reflejan en la URL para compartir o marcar como favorito