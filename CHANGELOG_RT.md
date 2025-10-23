# CHANGELOG - RT Fork

## [0.25.5.RT] - 2025-10-23

### ✨ Nuevas funcionalidades
- feat(tautulli): Add Tautulli webhook integration for YouTube video auto-creation via "Recently Added" event.
  - New endpoint: `/webhook/tautulli/<token>` (JSON POST).
  - Detects YouTube videos from TubeArchivist file paths (e.g., `/CHANNEL_ID/VIDEO_ID.mp4`).
  - Auto-creates channel (TV), season (by year), and episode Items in Yamtrack.
  - Follows same logic as manual YouTube video creation (no auto-watch, no Episode instance).
  - Prevents duplicates using `youtube_video_id` field.

### 🔄 Cambios
- refactor(plex): Removed Plex `library.new` webhook logic (replaced by more reliable Tautulli integration).
  - Deleted YouTube auto-creation methods from Plex webhook processor.
  - Removed `library.new` event support from Plex webhook.

### 📚 Documentación
- docs(tautulli): Add comprehensive Tautulli YouTube integration guide with configuration steps.

### 🧪 Tests
- test(tautulli): Add 7 comprehensive test cases for Tautulli webhook processor.

---

## [0.25.4.RT] - 2025-10-23

### ✨ Nuevas funcionalidades
- feat(plex): Auto-create YouTube videos in Yamtrack when added to Plex library (`library.new` webhook).
  - Detect YouTube video IDs from Plex GUIDs and from file paths produced by TubeArchivist (e.g. `/CHANNEL_ID/VIDEO_ID.mp4`).
  - Create/reuse channel (TV), season (by year) and episode Items in Yamtrack.
  - Prevent duplicates via `youtube_video_id` and pre-check payloads to avoid false positives.

### 🐛 Fixes y mejoras
- feat(plex): Add robust TubeArchivist file-path detection to support Plex libraries that store downloaded YouTube clips without YouTube GUIDs.
- test(plex): Add unit tests for YouTube creation and TubeArchivist case; mocks YouTube API for fast CI.

---

## [0.25.2.RT] - 2025-10-23

### ✨ Nuevas funcionalidades y mejoras
- feat(youtube): Added persistent `youtube_video_id` to `Item` and used it for duplicate detection; created migration to add the field and DB constraint.
- feat(youtube): When creating a YouTube video item, redirect to `create_entry?media_type=youtube_video` to preselect the correct tab and avoid auto-creating Episodes.
- feat(create): Allow preselecting media type on the create entry page via `?media_type=...` (improves UX).
- feat(youtube): Default filter `unwatched` is applied on YouTube channel details to show pending videos upfront.

### 🐛 Fixes
- fix(youtube): Prevent creating duplicate YouTube videos by using `youtube_video_id` instead of parsing `notes`; store `video_id` on creation.
- fix(youtube): New YouTube channels/seasons now default to `IN_PROGRESS` instead of `COMPLETED` to match expected UX.
- fix(home): Render preferred/remaining sections without unsupported template indexing and pass `media_list` properly to includes (resolves TemplateSyntaxError and empty-state rendering issues).
- fix(models): Persist TMDB episode `air_date` and `runtime` on `Item` creation/update; added tests to cover this behavior.

### 🎨 UI
- style(ui): Reorder sidebar and Home to place YouTube after TV Seasons and move the Youtubes section between Seasons and Movies on Home.
- feat(ui): Show full episode title in a tooltip/popover on hover (desktop) and tap (mobile) in the media details page to handle long titles gracefully.

### 🧪 Chores
- chore: ignore and remove supervisord runtime files from repo.

### ⚙️ Integraciones
- feat(plex): Webhook fallback to detect YouTube videos when Plex payload lacks TMDB/IMDB/TVDB IDs and mark matching YouTube Items as watched automatically.

---

## [0.25.1.RT] - 2025-10-22

### 🛠️ Release notes
- Limpieza: eliminados artefactos y snapshots de pruebas (tests sueltos, `form_response.html`, `supervisord` logs/pid).
- YouTube: soporte completo como media type separado
  - Portadas de canales mostradas en formato 1:1 en la home
  - Videos creados como Items (sin crear `Episode` automáticamente)
  - Progreso y contadores: ahora muestran `vistos / totales` para canales de YouTube
  - Home: los canales que están al día (progress == max_progress) no aparecen en la sección "YouTubes"
  - Separación visual: los canales de YouTube ya no aparecen en TV Shows/TV Seasons
- Varios fixes y mejoras menores: TMDB protection para YouTube, mejoras en templates y en la lógica de progreso.

---

## [0.24.10.RT] - 2025-10-16

### ✨ Nuevas Funcionalidades
- **Intelligent Season Status Assignment**: Nueva lógica inteligente para el estado de temporadas
  - Cuando una serie está completada y se añade una nueva temporada, se establece automáticamente como "In Progress" en lugar de "Planning"
  - Facilita el seguimiento de series que el usuario ya terminó pero obtienen nuevas temporadas
  - Mejora la experiencia de usuario al evitar cambios manuales de estado

---

## [0.24.9.1.RT] - 2025-10-16

### 🐛 Hotfix
- **Parent Season Search Fix**: Corregido problema de búsqueda de temporadas padre
  - Aumentado límite de resultados de 5 a 20 temporadas
  - Agregada ordenación por número de temporada y título
  - Resuelve el problema donde temporadas con números altos (2023, 2024, 2025) no aparecían
  - Mejora la experiencia al crear episodios personalizados

---

## [0.24.9.RT] - 2025-10-16

### 🔧 Correcciones
- **YouTube Episodes Source Fix**: Cambiada la fuente de episodios de YouTube de `youtube` a `manual`
  - Resuelve problemas de restricciones de base de datos
  - Mejor clasificación semántica: episodios personalizados usan fuente `manual`
  - Mantiene toda la funcionalidad de extracción de metadatos de YouTube
  - Compatibilidad con bases de datos que no tienen `youtube` como fuente permitida

---

## [0.24.8.RT] - 2025-10-15

### 🔧 Mejoras
- **Plex Webhook Fix**: Corregido el problema de identificación de series de TV
  - Ahora usa TMDB ID directamente cuando está disponible
  - Extrae season/episode del payload de Plex correctamente
  - Elimina el error "No matching TMDB ID found for TV show"
  - Mejora significativamente la detección de contenido con metadatos de TMDB

### 🌟 Funcionalidades Nuevas
- **YouTube Integration**: Integración completa con YouTube Data API v3
  - Extracción automática de metadatos desde URLs de YouTube
  - Soporte para títulos, duración, thumbnails y fecha de publicación
  - Formulario mejorado con auto-completado de información
  - Configuración segura de API keys via variables de entorno

### 📦 Infraestructura
- **Versionado RT**: Sistema de versionado para el fork con sufijo `.RT`
- **Environment Configuration**: Plantillas mejoradas para desarrollo y producción
- **Documentation**: Documentación actualizada del proceso de instalación

### 🔧 Técnico
- Modificado `_find_tv_media_id()` en `src/integrations/webhooks/base.py`
- Creado `src/app/providers/youtube.py` con extractor completo
- Mejorado manejo de API keys y variables de entorno
- Tests unitarios mantenidos para compatibilidad

---

## Notas de Versionado RT

**Formato**: `{version_base}.RT`
- **Base Version**: Versión original de Yamtrack como referencia  
- **RT Suffix**: Indica fork personalizado de Rodrigo Tomagnini
- **Semantic**: Incrementa version_base para nuevas funcionalidades significativas

**Ejemplo**:
- Original: `0.24.7` → RT Fork: `0.24.8.RT`
- Siguiente: `0.24.8.RT` → `0.24.9.RT` (nueva funcionalidad)
- Bugfix: `0.24.8.RT` → `0.24.8.1.RT` (si es necesario)