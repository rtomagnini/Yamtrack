# Integración Tautulli → Yamtrack para videos de YouTube

## Descripción

Esta integración permite que los videos de YouTube añadidos a la biblioteca de Plex (detectados por Tautulli) se creen automáticamente en Yamtrack, utilizando la misma lógica que el flujo manual "Create Custom → YouTube Videos".

## ¿Por qué Tautulli en lugar de Plex nativo?

Tautulli proporciona notificaciones más confiables y personalizables que los webhooks nativos de Plex:

- ✅ **Evento "Recently Added" más robusto** - Se dispara de manera consistente cuando se añade contenido
- ✅ **Más parámetros disponibles** - Incluye `file`, `filename` y metadata completa del item
- ✅ **Filtros y condiciones** - Control granular sobre qué eventos disparan notificaciones
- ✅ **Mayor flexibilidad** - Personalización de JSON payload enviado al webhook

## Funcionalidad

### ¿Qué hace?

Cuando agregas un video de YouTube a tu biblioteca de Plex (y Tautulli detecta el evento "Recently Added"):

1. **Extrae el video ID** de YouTube desde la ruta del archivo (filename pattern)
2. **Obtiene metadata** del video y canal desde la API de YouTube
3. **Crea/reutiliza el canal** (TV instance) en Yamtrack
4. **Crea/reutiliza la temporada** (Season) basada en el año de publicación
5. **Crea el Item del episodio** con toda la metadata (título, thumbnail, duración, fecha)
6. **Marca como completado** automáticamente (ya que fue añadido a la biblioteca)
7. **Previene duplicados** usando el campo `youtube_video_id`

### Flujo completo

```
Video añadido en Plex
    ↓
Tautulli detecta "Recently Added"
    ↓
Tautulli envía webhook POST (JSON) a Yamtrack
    ↓
Yamtrack detecta YouTube video ID en file path
    ↓
Fetch metadata de YouTube API
    ↓
Crear/reutilizar Channel → Season → Episode
    ↓
Video disponible en Yamtrack (marcado como visto)
```

## Configuración

### 1. Configurar webhook en Tautulli

1. Abre **Tautulli** → **Settings** → **Notification Agents**
2. Click en **Add a new notification agent** → selecciona **Webhook**
3. Configura el webhook:

**Webhook URL:**
```
https://tu-dominio.com/integrations/webhook/tautulli/<TU_TOKEN_DE_YAMTRACK>/
```

> **Nota**: Reemplaza `<TU_TOKEN_DE_YAMTRACK>` con el token de tu usuario en Yamtrack  
> (Disponible en: Profile → Settings → API Token)

**Webhook Method:**
```
POST
```

**Triggers:**
- ✅ Marca **Recently Added**
- ❌ Desmarca todos los demás eventos (play, stop, etc.)

**Conditions (opcional):**

Para **solo videos de YouTube** de TubeArchivist, añade una condición:

- Condition: `File` - `contains` - `/tubearchivist/`

Esto evita que se procesen otros archivos de video.

**JSON Headers:**
```json
{
  "Content-Type": "application/json"
}
```

**JSON Data:**

```json
{
  "action": "{action}",
  "media_type": "{media_type}",
  "title": "{title}",
  "file": "{file}",
  "filename": "{filename}"
}
```

> **Importante**: El campo `{file}` debe contener la ruta completa del archivo.  
> Tautulli lo populate automáticamente con el valor correcto.

4. **Guarda** y haz click en **Send Test Notification** para verificar

### 2. Credenciales de usuario en Yamtrack

Asegúrate de tener configurado tu token en Yamtrack:

1. Inicia sesión en Yamtrack
2. Ve a **Profile** → **Settings**
3. Copia tu **API Token** (lo necesitarás para la URL del webhook)

### 3. Verificar YouTube API

Confirma que tienes configurada la API de YouTube en Yamtrack:

```bash
# En .env o settings
YOUTUBE_API_KEY=tu-youtube-api-key
```

## Formato de archivo soportado (TubeArchivist)

La integración funciona mejor con **TubeArchivist**, que organiza videos descargados de YouTube con el siguiente patrón:

```
/ruta/base/tubearchivist/<CHANNEL_ID>/<VIDEO_ID>.mp4
```

Ejemplo real:
```
/volume1/Servidor/tubearchivist/UC_ATWjZ2hVwEVh4JiDpKccA/S3HTZSTcieQ.mp4
```

Donde:
- `UC_ATWjZ2hVwEVh4JiDpKccA` = YouTube Channel ID (24 caracteres, empieza con "UC")
- `S3HTZSTcieQ` = YouTube Video ID (11 caracteres alfanuméricos)

### Patrón de detección

Yamtrack detecta videos de YouTube verificando:

1. **Filename**: Debe ser exactamente 11 caracteres alfanuméricos (`[A-Za-z0-9_-]{11}`)
2. **Channel ID** (opcional): Directorio padre con patrón `UC[A-Za-z0-9_-]{22}`

Si el filename **no** sigue este patrón, el evento será ignorado.

## Prevención de duplicados

La integración verifica si el video ya existe **antes** de crearlo:

```python
existing_video = Item.objects.filter(
    source=Sources.YOUTUBE.value,
    media_type=MediaTypes.EPISODE.value,
    youtube_video_id=video_id,
).first()
```

Si el video ya existe, se considera "handled" y retorna `True` sin crear duplicado.

## Testing

### Test manual

1. **Añade un video de YouTube** a tu biblioteca de Plex usando TubeArchivist
2. Espera a que **Tautulli detecte el Recently Added**
3. **Revisa los logs de Yamtrack**:
   ```
   INFO: Processing Tautulli webhook: action=created, media_type=movie
   INFO: Tautulli Recently Added: file=/path/to/video.mp4
   INFO: Detected YouTube video ID from Tautulli: VIDEO_ID
   INFO: Created new YouTube channel: CHANNEL_NAME
   INFO: Successfully created YouTube video from Tautulli Recently Added event
   ```
4. **Verifica en Yamtrack** que el video aparece en el canal correspondiente

### Test con Tautulli

En Tautulli, puedes usar **Send Test Notification** para verificar la conexión:

1. Ve a Settings → Notification Agents → Tu webhook
2. Click en **Send Test Notification**
3. Selecciona **Recently Added** como evento
4. Revisa logs de Yamtrack para confirmar recepción

### Logs de debug

Para ver más detalles, habilita logs DEBUG:

```python
LOGGING = {
    'loggers': {
        'integrations.webhooks.tautulli': {
            'level': 'DEBUG',
        },
    },
}
```

## Edge cases y consideraciones

### 1. Videos sin patrón de YouTube

Si el archivo **no sigue el patrón** de TubeArchivist (filename de 11 chars), el evento será ignorado:

```
DEBUG: File path does not appear to be a YouTube video: /path/to/regular_movie.mkv
```

**Solución**: Renombra el archivo al formato `VIDEO_ID.ext` (11 caracteres) o añade manualmente en Yamtrack.

### 2. Errores de API de YouTube

Si la API de YouTube falla:

```
WARNING: Could not fetch YouTube metadata for video ID: VIDEO_ID
```

**Solución**: 
- Verifica que `YOUTUBE_API_KEY` esté configurada
- Confirma que el video sea público/no listado (no privado)
- Revisa cuota de API en Google Cloud Console

### 3. Channel ID no detectado

Si el directorio padre no tiene patrón `UC...`, se intentará obtener del API:

```
INFO: Using channel ID from file path as fallback: UC...
```

Si tampoco está disponible, la creación fallará.

### 4. Videos privados o eliminados

Si el video se vuelve privado después de añadirlo, Yamtrack mantendrá la metadata original.

## Comparación con creación manual

| Aspecto | Manual (Create Entry) | Automático (Tautulli) |
|---------|----------------------|---------------------|
| **Trigger** | Usuario crea formulario | Tautulli "Recently Added" |
| **Input** | URL de YouTube | File path (TubeArchivist) |
| **Metadata** | YouTube API | YouTube API |
| **Duplicados** | Verificado | Verificado |
| **Marcado como visto** | ❌ No | ✅ Sí (status=COMPLETED) |
| **Redirección** | Sí (a create_entry) | N/A |
| **Mensaje de éxito** | Django messages | Logs |

## Flujo de código

### Archivo principal
`src/integrations/webhooks/tautulli.py`

### Método principal
```python
def _create_youtube_video_from_tautulli(self, payload, user):
    """
    Create a YouTube video in Yamtrack when detected from Tautulli Recently Added event.
    
    Returns True if successfully created, False otherwise.
    """
```

### Métodos auxiliares
- `_extract_youtube_id_from_file_path()` - Extrae video ID del filename (11 chars)
- `_extract_youtube_channel_id_from_file_path()` - Extrae channel ID del directorio (UC...)
- `_looks_like_youtube_video()` - Pre-check para detectar patrones de YouTube
- `youtube.fetch_video_metadata()` - Metadata del video
- `youtube.fetch_channel_metadata()` - Metadata del canal

## Diferencias con Plex nativo (deprecado)

Yamtrack anteriormente soportaba webhooks nativos de Plex (`library.new`), pero ha sido reemplazado por Tautulli:

| Plex Nativo | Tautulli |
|-------------|----------|
| Evento `library.new` poco confiable | `Recently Added` más robusto |
| Extracción de GUIDs complicada | File path simple y directo |
| Payload multipart/form-data | JSON puro (más fácil de debuggear) |
| Sin condiciones | Condiciones y filtros avanzados |

**La integración de Plex nativo ha sido eliminada en favor de Tautulli.**

## Troubleshooting

### El video no se crea automáticamente

1. **Verifica logs de Django** para errores del webhook
   ```bash
   docker logs yamtrack | grep -i tautulli
   ```
2. **Confirma que Tautulli envía el evento** (ve a Tautulli → Notification Logs)
3. **Revisa el file path** en el payload (debe seguir patrón de TubeArchivist)
4. **Valida el token** en la URL del webhook
5. **Prueba la creación manual** con la misma URL para descartar problemas de YouTube API

### Error: "Invalid JSON in Tautulli webhook request"

- Verifica que el **Webhook Method** sea `POST`
- Confirma que **JSON Headers** incluye `Content-Type: application/json`
- Prueba con **Send Test Notification** en Tautulli

### Error: "Could not fetch YouTube metadata"

- Verifica `YOUTUBE_API_KEY` en settings/env
- Confirma que el video sea público o no listado
- Revisa cuota de API en Google Cloud Console

### Duplicados creados

Si se crean duplicados:
- Confirma que la migración del campo `youtube_video_id` se aplicó
- Verifica que Items existentes tengan `youtube_video_id` poblado
- Ejecuta script de backfill si es necesario

## Configuración avanzada

### Filtros en Tautulli

Puedes añadir múltiples condiciones en Tautulli para afinar qué videos se envían:

**Ejemplo 1: Solo TubeArchivist**
```
Condition: File - contains - /tubearchivist/
```

**Ejemplo 2: Solo ciertos canales**
```
Condition: File - contains - /UC_ATWjZ2hVwEVh4JiDpKccA/
```

**Ejemplo 3: Excluir por título**
```
Condition: Title - does not contain - [Private]
```

### Custom JSON payload

Puedes incluir más parámetros en el JSON si necesitas metadata adicional:

```json
{
  "action": "{action}",
  "media_type": "{media_type}",
  "title": "{title}",
  "file": "{file}",
  "filename": "{filename}",
  "year": "{year}",
  "added_date": "{added_date}",
  "library_name": "{library_name}"
}
```

> **Nota**: Yamtrack actualmente solo usa `action`, `media_type` y `file`.  
> Los demás campos son ignorados pero pueden ser útiles para debugging.

## Soporte

Para reportar bugs o solicitar features:

1. Abre un issue en el repositorio de Yamtrack
2. Incluye logs relevantes (con nivel DEBUG si es posible)
3. Describe el payload de Tautulli recibido
4. Indica versión de Yamtrack, Tautulli y Plex

---

**Autor**: Implementado en la rama `feat/plex-integration` (migrado a Tautulli)  
**Fecha**: Diciembre 2024  
**Versión**: 0.25.4.RT
