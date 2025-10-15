# 🚀 Guía de Actualización del Servidor Yamtrack

Esta guía te ayudará a actualizar tu servidor Yamtrack con los nuevos cambios desde tu fork **modificando solo el docker-compose.yml**.

## 📋 Nuevas Funcionalidades Incluidas

1. ✅ **Filtros de episodios** (All/Watched/Unwatched) en detalles de temporada
2. ✅ **Ordenamiento de episodios** (ascendente/descendente) con botón "Order"
3. ✅ **Thumbnails 16:9** para episodios con proporción correcta
4. ✅ **Modal de confirmación** para completar temporadas automáticamente
5. ✅ **Mejoras en Create Custom** con auto-incremento de números de episodio
6. ✅ **Campo Air Date** para episodios (fecha de emisión)
7. ✅ **Campo Runtime** para episodios (duración en minutos con sufijo "min")

## 🎯 MÉTODO RECOMENDADO: Actualización via Docker Compose

### ⚡ Ventajas de este método:
- **Sin descargar código**: Docker construye directamente desde tu repositorio Git
- **Actualización simple**: Solo cambiar una línea en docker-compose.yml
- **Sin conflictos**: No hay riesgo de conflictos Git locales
- **Automático**: Las migraciones se aplican automáticamente

## 🛠️ Instrucciones de Actualización Rápida

### 1. 💾 Backup de la Base de Datos (CRÍTICO)

```bash
# Si usas Docker Compose con PostgreSQL:
docker-compose exec db pg_dump -U yamtrack_user yamtrack_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Si usas PostgreSQL directo:
pg_dump -U tu_usuario -h localhost yamtrack_db > backup_$(date +%Y%m%d_%H%M%S).sql

# Si usas SQLite:
cp db.sqlite3 backup_db_$(date +%Y%m%d_%H%M%S).sqlite3
```

### 2. 🛑 Parar los Servicios

```bash
# Parar todos los contenedores
docker-compose down

# O si usas docker compose (sin guión):
docker compose down
```

### 3. ✏️ Modificar docker-compose.yml

Edita tu archivo `docker-compose.yml` y cambia la sección `build` del servicio web para apuntar a tu fork:

**Antes (construcción local):**
```yaml
services:
  web:
    build: .
    # ... resto de configuración
```

**Después (construcción desde tu fork):**
```yaml
services:
  web:
    build:
      context: https://github.com/rtomagnini/Yamtrack.git
      dockerfile: Dockerfile
    # ... resto de configuración
```

**Alternativamente, puedes usar una imagen pre-construida si la tienes:**
```yaml
services:
  web:
    image: ghcr.io/rtomagnini/yamtrack:latest
    # ... resto de configuración
```

### 4. 🚀 Reconstruir y Aplicar Cambios

```bash
# Reconstruir contenedores con los nuevos cambios desde tu fork
docker-compose up -d --build

# O si usas docker compose (sin guión):
docker compose up -d --build
```

### 5. 🔄 Forzar Reconstrucción (si es necesario)

```bash
# Si Docker usa caché y no ve los cambios, fuerza la reconstrucción:
docker-compose build --no-cache web
docker-compose up -d
```

## 🔧 Migraciones de Base de Datos

### ✅ Las migraciones son AUTOMÁTICAS

Cuando ejecutes `docker-compose up --build`, Django aplicará automáticamente estas migraciones:

- **0052_add_air_date_to_item.py** - Agrega campo `air_date` a episodios
- **0053_add_runtime_to_item.py** - Agrega campo `runtime` a episodios

### 📋 Verificar que las Migraciones se Aplicaron

```bash
# Ver estado de las migraciones
docker-compose exec web python manage.py showmigrations

# Verificar que los nuevos campos existen
docker-compose exec web python manage.py shell -c "
from app.models import Item
episode = Item.objects.first()
print(f'air_date field exists: {hasattr(episode, \"air_date\")}')
print(f'runtime field exists: {hasattr(episode, \"runtime\")}')
"
```

## 🔄 Actualizaciones Futuras

Una vez configurado este método, las actualizaciones futuras serán **súper simples**:

```bash
# 1. Hacer backup (siempre)
docker-compose exec db pg_dump -U yamtrack_user yamtrack_db > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Parar servicios
docker-compose down

# 3. Reconstruir (Docker tomará automáticamente los últimos cambios de tu fork)
docker-compose up -d --build

# ¡Listo! Tu aplicación estará actualizada con los últimos cambios
```

## 📌 Configuración Avanzada: Imagen Pre-construida

### ⚡ Ventajas de usar imagen pre-construida:
- **Actualizaciones súper rápidas**: No necesita construir, solo descargar
- **Menos recursos**: Tu servidor no gasta CPU/memoria construyendo
- **Más confiable**: La imagen se construye en GitHub con recursos dedicados
- **Versionado**: Cada commit genera una imagen etiquetada

### 🏗️ Configurar GitHub Actions (Una sola vez)

**Paso 1:** Crea el archivo `.github/workflows/docker-build.yml` en tu repositorio con el contenido del workflow.

**Paso 2:** En tu repositorio GitHub:
- Ve a **Settings** → **Actions** → **General**
- En **Workflow permissions**, selecciona **"Read and write permissions"**
- Marca **"Allow GitHub Actions to create and approve pull requests"**

**Paso 3:** Haz push del archivo workflow:
```bash
git add .github/workflows/docker-build.yml
git commit -m "ci: Add GitHub Actions workflow for Docker image building"
git push origin master
```

### 🐳 Usar la Imagen Pre-construida

Una vez configurado, modifica tu `docker-compose.yml` en el servidor:

```yaml
services:
  web:
    image: ghcr.io/rtomagnini/yamtrack:master
    pull_policy: always  # Siempre obtener la última versión
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://yamtrack_user:password@db:5432/yamtrack_db
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    volumes:
      - media_files:/yamtrack/media
      - static_files:/yamtrack/staticfiles
```

### 🏷️ Tags Disponibles

Después de cada push a master, se generan automáticamente estos tags:
- `ghcr.io/rtomagnini/yamtrack:master` - Última versión de la rama master
- `ghcr.io/rtomagnini/yamtrack:latest` - Alias para la última versión
- `ghcr.io/rtomagnini/yamtrack:master-HASH` - Versión específica por commit

### 🔄 Actualizaciones con Imagen Pre-construida

```bash
# 1. Backup (siempre)
docker-compose exec db pg_dump -U yamtrack_user yamtrack_db > backup_$(date +%Y%m%d_%H%M%S).sql

# 2. Parar servicios
docker-compose down

# 3. Obtener última imagen y reiniciar
docker-compose pull
docker-compose up -d

# ¡Súper rápido! No necesita construir nada
```

### 🎯 Usando Tags Específicos

Para más control, puedes usar tags específicos:

```yaml
services:
  web:
    image: ghcr.io/rtomagnini/yamtrack:master-7ac4c43  # Versión específica
    # O
    image: ghcr.io/rtomagnini/yamtrack:master  # Última de master
    # O  
    image: ghcr.io/rtomagnini/yamtrack:latest  # Última versión
```

### Actualizaciones con Tags

Para más control, puedes usar tags específicos:

```yaml
services:
  web:
    build:
      context: https://github.com/rtomagnini/Yamtrack.git#v1.2.0  # Tag específico
      dockerfile: Dockerfile
```

## 🧪 Verificación Post-Actualización

### 1. Verificar que la aplicación funciona
```bash
# Ver logs para asegurar que no hay errores
docker-compose logs web

# Verificar que los contenedores están corriendo
docker-compose ps
```

### 2. Probar las nuevas funcionalidades
- Ve a cualquier serie → temporada y verifica los filtros "All", "Watched", "Unwatched"
- Intenta crear un episodio manual y verifica los campos Air Date y Runtime
- Marca el último episodio de una temporada como visto para probar el modal de confirmación

## 🚨 Plan de Recuperación (si algo sale mal)

### Restaurar Base de Datos
```bash
# Si algo sale mal, restaurar desde backup
docker-compose exec db psql -U yamtrack_user yamtrack_db < backup_YYYYMMDD_HHMMSS.sql
```

### Volver a Versión Anterior

**Método 1: Cambiar a commit específico**
```yaml
# En docker-compose.yml, especifica un commit anterior:
services:
  web:
    build:
      context: https://github.com/rtomagnini/Yamtrack.git#COMMIT_HASH_ANTERIOR
      dockerfile: Dockerfile
```

**Método 2: Usar repositorio original**
```yaml
# En docker-compose.yml, volver al repositorio original:
services:
  web:
    build:
      context: https://github.com/FuzzyGrim/Yamtrack.git
      dockerfile: Dockerfile
```

Luego ejecuta:
```bash
docker-compose down
docker-compose up -d --build
```

## 📝 Ejemplos de docker-compose.yml

### Opción 1: Construcción desde Git (Método actual)

```yaml
version: '3.8'

services:
  web:
    build:
      context: https://github.com/rtomagnini/Yamtrack.git
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://yamtrack_user:password@db:5432/yamtrack_db
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    volumes:
      - media_files:/yamtrack/media
      - static_files:/yamtrack/staticfiles

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=yamtrack_db
      - POSTGRES_USER=yamtrack_user
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
  media_files:
  static_files:
```

### Opción 2: Imagen Pre-construida (Recomendado)

```yaml
version: '3.8'

services:
  web:
    image: ghcr.io/rtomagnini/yamtrack:master
    pull_policy: always
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgres://yamtrack_user:password@db:5432/yamtrack_db
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    volumes:
      - media_files:/yamtrack/media
      - static_files:/yamtrack/staticfiles

  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_DB=yamtrack_db
      - POSTGRES_USER=yamtrack_user
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
  media_files:
  static_files:
```

## 📝 Notas Importantes

1. **Backup obligatorio**: SIEMPRE haz backup antes de actualizar
2. **Migraciones automáticas**: Django las aplicará automáticamente
3. **Sin pérdida de datos**: Los nuevos campos son opcionales (null=True, blank=True)
4. **Compatibilidad**: Compatible con episodios existentes
5. **Actualización simple**: Solo cambiar la URL del repositorio
6. **Sin Git local**: No necesitas clonar ni manejar Git en el servidor

## 🎉 Después de la Actualización

Una vez completada la actualización, tendrás acceso a:

- **Filtros de episodios** (All/Watched/Unwatched) con botón "Order"
- **Thumbnails 16:9** perfectamente proporcionadas
- **Modals de confirmación** cuando completes temporadas
- **Auto-incremento** de números de episodio en Create Custom
- **Campos Air Date y Runtime** en la creación de episodios
- **Interfaz en inglés** y mejor UX general

## 🔮 Actualizaciones Futuras

Con este método configurado, cada vez que subas nuevos cambios a tu fork, solo necesitarás:

```bash
docker-compose down
docker-compose up -d --build
```

**¡Docker automáticamente tomará los últimos cambios!**

---

## 📞 Soporte

Si encuentras algún problema durante la actualización:

1. Verifica los logs: `docker-compose logs web`
2. Asegúrate de que todos los contenedores estén corriendo: `docker-compose ps`
3. Si hay errores de migración, verifica: `docker-compose exec web python manage.py showmigrations`

---

**¡Buena suerte con la actualización! 🚀**