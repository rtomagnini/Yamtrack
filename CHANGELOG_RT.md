


## [0.25.21.RT] - 2025-10-28

### 🛠 Statistics Page Improvements
- "Most Watched Shows" section now uses tables with visible lines for both TV Shows and YouTube Channels, matching the style of "Status Distribution by Media Type".
- Unified the style of the "Watch Time Over Time" chart: now uses the same blue color, lighter grid lines, and thinner bars as the "Status Distribution by Media Type" chart.
- The "Watch Time Over Time" chart is always grouped by days (no more week/month grouping).
- Removed values inside the bars for a cleaner look.
- Added a visible y-axis scale on the left, showing tick labels in hours (1h, 2h, 3h, ...).
- Replaced the "Status Distribution" pie chart with a new "Watch Time Distribution" pie chart, showing the percentage of total watch time by type (TV Show, YouTube, Movies).
- Pie chart labels inside the chart now show only the name and percent (e.g., YouTube 55%).
- The legend below the chart now shows only the name and total time in hours/minutes (e.g., TV Show (3h14m)), for a cleaner look.

### 🛠 Media Timeline Tweaks
- Media Timeline thumbnail size increased for better visibility.
- Timeline cards are now more compact: smaller/left-aligned title, left-aligned air date, runtime on a new line, and reduced card height.
- Movie thumbnails now use 2:3 aspect ratio (portrait); all other types remain 16:9.


## [0.25.20.RT] - 2025-10-28

### ✨ Youtube List & Card Redesign
- Youtube video cards now display the channel logo (from TV) instead of the channel name label.
- "Air Date" and "Runtime" labels are now bold for improved readability.
- "End Date" is only shown if it exists for the video.
- Youtube filters redesigned: now use icon-based dropdowns and htmx, matching medialist.
- Double-action bug fixed: filters and search apply changes on first click.
- Refactor: Youtube grid updates via partial for efficient htmx support.
- Added layout toggle: switch between card (grid) and table view for Youtube videos, matching medialist UX.
- Table view for Youtube videos with all relevant fields and channel logo.


## [0.25.19.RT] - 2025-10-27

### ✨ UI Improvements
- YouTube Videos view: Search box and filter/sort comboboxes now perfectly aligned for a cleaner look.
- Removed visible labels from filter/sort comboboxes and added icons for a modern, compact UI (matching the style of other search bars in the app).

---
## [0.25.18.RT] - 2025-10-27
## [0.25.18.RT] - 2025-10-27

### ✨ Improvements & Fixes
- Fix: YouTube episode creation now always calculates the next episode_number before creating the Item, preventing unique constraint errors.
- UI: Updated YouTube and YouTube Video icons for the sidebar and creation UI.
- UI: Added a refresh icon button to the top search bar and unified the style of the "Watch Time Over Time" chart with the "Status Distribution" chart.
- UI: Added a refresh button to the main layout and improved layout toggle controls.


## CHANGELOG - RT Fork

## [0.25.17.RT] - 2025-10-27

### ✨ Improvements
- Plex webhook: Adds fallback logic to mark manual TV Show episodes as watched using youtube_video_id if no match is found by standard IDs. This improves integration for manually tracked TV Shows with YouTube videos.
- Media Timeline: Improved grid layout to always show 3 cards per row on desktop, removed width restrictions, and enhanced responsive design for mobile/desktop.
- Media Timeline: Cards now display runtime, use correct thumbnail aspect ratios (16:9 for TV/YouTube, 2:3 for Movies), and overall visual improvements.
- Statistics view: Default combobox value changed to show today's data.

---

## [0.25.16.RT] - 2025-10-26

### ✨ Improvements
- When creating a manual episode (source = manual) with a YouTube URL, the youtube_video_id is now automatically saved in the Item model. This allows robust identification and linking of manual episodes with YouTube videos.

---

- The section background remains dark style, but text is white for better visual integration.
- Fixed import and grouping errors in statistics backend.

## [0.25.15.RT] - 2025-10-26

### 🐛 Fixes
- Plex webhook for YouTube now correctly associates episodes to the Season of the YouTube channel, avoiding collisions with manual TV Shows or other sources.

---

## [0.25.14.RT] - 2025-10-25

### ✨ Improvements
- Redesigned statistics section: now shows "Most Watched Shows" in two columns (TV Shows and YouTube), grouping and summing episodes watched per TV/channel.

## [0.25.13.RT] - 2025-10-24

### 🐛 Fixes
- Fixed statistics error: LookupError for model 'historicalyoutube'. Now correctly maps to 'historicaltv' to avoid failure when displaying YouTube statistics.

## [0.25.12.RT] - 2025-10-24

### ✨ Improvements
- Episodes in the YouTube channel details view are now sorted by episode number (ascending or descending according to filter).

---

## [0.25.11.RT] - 2025-10-24

### ✨ Improvements
- Added "Pending" filter in YouTube medialist to show only channels with pending episodes.
- The default filter in YouTube medialist is now "Pending".
- Fix: the "Pending" filter no longer appears duplicated.

---

## [0.25.10.RT] - 2025-10-24

### ✨ Improvements
- Allow up to 4 digits (9999) in the "Season Number" field when creating manual seasons.
  - Model, migration, and form updated to accept values up to 9999.
  - Visual and validation limits fixed in the frontend (HTML).

---
- fix(youtube): Fix delete button not working and remove custom lists button for YouTube videos.
  - Fixed HTML structure in media_details.html (incorrect div closing).
  - Removed "Add to custom lists" button for YouTube videos (only show for other media types).
  - Removed conflicting `hx-confirm="false"` attribute in youtube_channel_details.html.
  - Added detailed logging to delete endpoint for debugging.
  - Delete button now properly appears only for YouTube videos with correct HTMX attributes.

---

## [0.25.8.RT] - 2025-01-24

### ✨ Nuevas funcionalidades
- feat(youtube): Add delete button for YouTube videos in channel details view.
  - Red circular button with trash icon on each video in channel details page.
  - Confirmation dialog using Alpine.js before deletion.
  - New DELETE endpoint /api/youtube/video/<video_id>/delete/ to delete videos.
  - Only allows users to delete their own videos for security.
  - Videos are removed from UI with smooth transition after deletion.

---

## [0.25.7.RT] - 2025-10-23

### 🐛 Fixes
- fix(plex): Fix YouTube video marking wrong episode as watched.
  - Extract video ID from file path first (more reliable for TubeArchivist).
  - Create Episode directly with specific Item instead of using season.watch().
  - Prevents marking random YouTube videos when multiple videos share same episode number.
  - Remove unreliable title-based matching that could match wrong videos.

---

## [0.25.6.RT] - 2025-10-23

### ✨ Nuevas funcionalidades
- feat(youtube): Add YouTube channel filtering system for webhooks.
  - New `YouTubeChannelFilter` model to block specific channels from auto-creation.
  - Admins can manage filtered channels via Django admin interface.
  - Tautulli webhook now checks if channel is filtered before creating videos.
  - Per-user channel filters with unique constraint.

### 🧪 Tests
- test(tautulli): Add test case for filtered/blocked YouTube channels.

---

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