# Integración Plex → Yamtrack para videos de YouTube

## Descripción

Esta integración permite que los videos de YouTube añadidos a la biblioteca de Plex se creen automáticamente en Yamtrack, utilizando la misma lógica que el flujo manual "Create Custom → YouTube Videos".

## Funcionalidad

### ¿Qué hace?

Cuando agregas un video de YouTube a tu biblioteca de Plex, el webhook `library.new` detectará automáticamente el video y:

1. **Extrae el video ID** de YouTube desde los GUIDs de Plex
2. **Obtiene metadata** del video y canal desde la API de YouTube
3. **Crea/reutiliza el canal** (TV instance) en Yamtrack
4. **Crea/reutiliza la temporada** (Season) basada en el año de publicación
5. **Crea el Item del episodio** con toda la metadata (título, thumbnail, duración, fecha)
6. **Previene duplicados** usando el campo `youtube_video_id`

### Flujo completo

```
Video añadido en Plex
    ↓
Plex envía webhook library.new
    ↓
Yamtrack detecta YouTube video ID en GUIDs
    ↓
Fetch metadata de YouTube API
    ↓
Crear/reutilizar Channel → Season → Episode
    ↓
Video disponible en Yamtrack (sin marcar como visto)
```

## Configuración

### 1. Webhook de Plex

Configura el webhook en Plex para que envíe eventos a Yamtrack:

**URL del webhook:**
```
https://tu-dominio.com/integrations/webhooks/plex/
```

**Eventos soportados:**
- ✅ `library.new` - Crea videos de YouTube automáticamente
- ✅ `media.scrobble` - Marca episodios como vistos (funcionalidad existente)
- ✅ `media.play` - Detecta reproducción en progreso

### 2. Credenciales de usuario

En el perfil del usuario en Yamtrack, asegúrate de configurar:

- **Plex Usernames**: Nombres de usuario de Plex separados por comas (case-insensitive)
- **Anime Enabled**: Si quieres detección de anime (opcional)

### 3. Variables de entorno (opcionales)

Para funcionalidades avanzadas (consulta Plex API para metadata adicional):

```bash
PLEX_SERVER_URL=http://tu-servidor-plex:32400
PLEX_TOKEN=tu-token-de-plex
```

## Formato de GUIDs en Plex

Plex debe incluir el video ID de YouTube en los GUIDs del item. Formatos soportados:

- `youtube://VIDEO_ID`
- `yt://VIDEO_ID`
- `https://www.youtube.com/watch?v=VIDEO_ID`
- `https://youtu.be/VIDEO_ID`
- URL con parámetros: `...?v=VIDEO_ID&...`

## Prevención de duplicados

La integración verifica si el video ya existe **antes** de crearlo:

```python
existing_video = Item.objects.filter(
    source=Sources.YOUTUBE.value,
    media_type=MediaTypes.EPISODE.value,
    youtube_video_id=video_id,
).first()
```

Si el video ya existe, se considera "handled" y no se crea duplicado.

## Testing

### Test manual

1. Añade un video de YouTube a tu biblioteca de Plex
2. Verifica que Plex envía el webhook `library.new`
3. Revisa los logs de Yamtrack:
   ```
   INFO: Detected YouTube video ID from Plex: VIDEO_ID
   INFO: Created new YouTube channel: CHANNEL_NAME
   INFO: Successfully created YouTube video in Yamtrack: VIDEO_TITLE
   ```
4. Verifica en Yamtrack que el video aparece en el canal correspondiente

### Logs de debug

Para ver más detalles, habilita logs DEBUG en Django:

```python
LOGGING = {
    'loggers': {
        'integrations.webhooks.plex': {
            'level': 'DEBUG',
        },
    },
}
```

### Unit tests (TODO)

Los tests unitarios están pendientes de implementación. Deberían cubrir:

- ✅ Detección correcta de video ID desde distintos formatos de GUID
- ✅ Creación de channel/season/episode con metadata correcta
- ✅ Prevención de duplicados
- ✅ Manejo de errores (API de YouTube no disponible, etc.)

## Edge cases y consideraciones

### 1. Videos sin GUID de YouTube

Si Plex no incluye un GUID válido de YouTube, el video **no se creará automáticamente**. El webhook retornará `False` y se registrará:

```
DEBUG: No YouTube video ID found in Plex payload GUIDs
```

**Solución**: Añadir el video manualmente en Yamtrack usando "Create Custom → YouTube Videos".

### 2. Errores de API de YouTube

Si la API de YouTube falla o no devuelve metadata, el video no se creará. Logs:

```
WARNING: Could not fetch YouTube metadata for video ID: VIDEO_ID
```

**Solución**: Verificar que la API de YouTube esté configurada y tenga cuota disponible.

### 3. Múltiples usuarios

El webhook respeta la configuración de `plex_usernames` del usuario. Solo creará el video para usuarios cuyo nombre de Plex coincida con el del evento.

### 4. Videos privados o eliminados

Si el video se vuelve privado o se elimina de YouTube después de añadirlo a Plex, Yamtrack mantendrá el registro con la metadata que tenía en el momento de creación.

## Comparación con creación manual

| Aspecto | Manual (Create Entry) | Automático (Webhook) |
|---------|----------------------|---------------------|
| **Trigger** | Usuario crea formulario | Plex envía `library.new` |
| **Input** | URL de YouTube | GUID en Plex payload |
| **Metadata** | YouTube API | YouTube API |
| **Duplicados** | Verificado | Verificado |
| **Marcado como visto** | ❌ No | ❌ No (solo crea) |
| **Redirección** | Sí (a create_entry) | N/A |
| **Mensaje de éxito** | Django messages | Logs |

## Flujo de código

### Archivo principal
`src/integrations/webhooks/plex.py`

### Método principal
```python
def _create_youtube_video_from_plex(self, payload, user):
    """
    Create a YouTube video in Yamtrack when detected from Plex library.new event.
    
    Returns True if successfully created, False otherwise.
    """
```

### Métodos auxiliares reutilizados
- `_extract_youtube_id_from_guids()` - Extrae video ID de GUIDs
- `youtube.fetch_video_metadata()` - Metadata del video
- `youtube.fetch_channel_metadata()` - Metadata del canal
- `Item.generate_next_id()` - Genera IDs únicos

### Lógica de creación compartida con
`src/app/views.py :: handle_youtube_video_creation()`

## Próximos pasos (TODO)

- [ ] Añadir tests unitarios para `_create_youtube_video_from_plex`
- [ ] Considerar soporte para playlists de YouTube (crear múltiples videos)
- [ ] Opción de marcar como visto automáticamente al crear (configurable por usuario)
- [ ] Webhook de Plex para eliminación (`library.delete`) → eliminar en Yamtrack
- [ ] Panel de administración para ver webhooks recibidos y errores

## Troubleshooting

### El video no se crea automáticamente

1. **Verifica logs de Django** para errores del webhook
2. **Confirma que Plex envía el evento** `library.new`
3. **Revisa los GUIDs** en el payload de Plex (debe incluir identificador de YouTube)
4. **Valida credenciales** de usuario (`plex_usernames` configurado)
5. **Prueba la creación manual** con la misma URL para descartar problemas de YouTube API

### Error: "Could not fetch YouTube metadata"

- Verifica que la API key de YouTube esté configurada correctamente
- Confirma que el video sea público o no listado (no privado)
- Revisa cuota de la API de YouTube en Google Cloud Console

### Duplicados creados

Si se crean duplicados a pesar de la verificación:
- Confirma que la migración del campo `youtube_video_id` se aplicó correctamente
- Verifica que los Items existentes tengan el campo `youtube_video_id` poblado
- Ejecuta el backfill script (cuando esté disponible) para poblar IDs legacy

## Soporte

Para reportar bugs o solicitar features relacionadas con esta integración:

1. Abre un issue en el repositorio
2. Incluye logs relevantes (con nivel DEBUG si es posible)
3. Describe el payload de Plex recibido (si aplica)
4. Indica la versión de Yamtrack y Plex

---

**Autor**: Implementado en la rama `feat/plex-integration`  
**Fecha**: Octubre 2025  
**Commit**: `f17145a9`
