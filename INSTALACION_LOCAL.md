# Guía de Instalación y Prueba Local de Yamtrack

## Descripción de los Cambios Implementados

Se ha agregado un **sistema de filtrado de episodios** en la página de detalles de temporada que permite filtrar episodios por:
- **Todos**: Muestra todos los episodios (comportamiento por defecto)
- **Vistos**: Solo episodios marcados como vistos
- **No vistos**: Solo episodios sin marcar como vistos

## Instalación Local con Docker

### Paso 1: Preparar el entorno

```powershell
# Navegar al directorio del proyecto
cd C:\Projects\yamtrack\Yamtrack

# Verificar Docker
docker --version
docker-compose --version
```

### Paso 2: Construir la imagen local

```powershell
# Construir la imagen con los cambios
docker-compose -f docker-compose.dev.yml build

# Iniciar los servicios
docker-compose -f docker-compose.dev.yml up -d
```

### Paso 3: Configurar la aplicación

```powershell
# Verificar que los contenedores están ejecutándose
docker-compose -f docker-compose.dev.yml ps

# Ejecutar migraciones iniciales
docker-compose -f docker-compose.dev.yml exec yamtrack python manage.py migrate

# Crear superusuario (opcional)
docker-compose -f docker-compose.dev.yml exec yamtrack python manage.py createsuperuser
```

### Paso 4: Acceder a la aplicación

- **URL**: http://localhost:8337
- **Admin**: http://localhost:8337/admin (si creaste superusuario)

## Probar la Nueva Funcionalidad

### 1. Configurar datos de prueba

1. Accede a http://localhost:8337
2. Crea una cuenta de usuario o usa el admin
3. Configura tu API key de TMDB en las configuraciones
4. Busca y agrega una serie con múltiples episodios

### 2. Probar el filtro de episodios

1. Ve a la página de detalles de una temporada:
   - Formato URL: `http://localhost:8337/details/tmdb/tv/[ID]/[TITULO]/season/[NUMERO]`
   - Ejemplo: `http://localhost:8337/details/tmdb/tv/63367/equipo-de-investigacion/season/17`

2. Verifica que aparecen los nuevos botones de filtro:
   - **Todos** (azul cuando está activo)
   - **Vistos** (gris cuando no está activo)
   - **No vistos** (gris cuando no está activo)

3. Marca algunos episodios como vistos usando el botón de tracking

4. Prueba los filtros:
   - Clic en "**No vistos**" → Solo muestra episodios sin marcar
   - Clic en "**Vistos**" → Solo muestra episodios marcados
   - Clic en "**Todos**" → Muestra todos los episodios

### 3. Verificar comportamiento

- ✅ Los filtros se reflejan en la URL (`?filter=unwatched`, etc.)
- ✅ Los filtros son enlazables y se pueden marcar como favoritos
- ✅ Mensajes informativos cuando no hay episodios para mostrar
- ✅ Diseño responsivo en móviles y escritorio
- ✅ Preserva otros parámetros de la URL

## Comandos Útiles para Desarrollo

```powershell
# Ver logs en tiempo real
docker-compose -f docker-compose.dev.yml logs -f yamtrack

# Reiniciar el servicio después de cambios
docker-compose -f docker-compose.dev.yml restart yamtrack

# Acceder al contenedor para debugging
docker-compose -f docker-compose.dev.yml exec yamtrack sh

# Parar todos los servicios
docker-compose -f docker-compose.dev.yml down

# Limpiar y reconstruir (si necesitas hacer cambios)
docker-compose -f docker-compose.dev.yml down
docker-compose -f docker-compose.dev.yml build --no-cache
docker-compose -f docker-compose.dev.yml up -d
```

## Estructura de Archivos Modificados

```
src/
├── app/
│   ├── views.py                     # ✏️ Modificado: lógica de filtrado
│   └── templates/app/
│       └── media_details.html       # ✏️ Modificado: interfaz de filtros
└── ...

docker-compose.dev.yml               # 🆕 Nuevo: configuración para desarrollo
EPISODE_FILTER_CHANGES.md           # 🆕 Nuevo: documentación de cambios
```

## Solución de Problemas

### Error: "Port already in use"
```powershell
# Verificar qué está usando el puerto 8337
netstat -ano | findstr :8337

# Cambiar el puerto en docker-compose.dev.yml si es necesario
```

### Error: "Cannot connect to Docker daemon"
```powershell
# Asegurarse de que Docker Desktop está ejecutándose
# Reiniciar Docker Desktop si es necesario
```

### Error: "Image build failed"
```powershell
# Limpiar cache de Docker
docker system prune -a

# Reconstruir sin cache
docker-compose -f docker-compose.dev.yml build --no-cache
```

## Preparación para Git

Una vez que hayas probado que todo funciona correctamente:

```powershell
# Verificar cambios
git status

# Agregar archivos modificados
git add src/app/views.py
git add src/templates/app/media_details.html

# Commit con mensaje descriptivo
git commit -m "feat: add episode filtering by watch status

- Add filter buttons (All, Watched, Unwatched) to season details page
- Filter episodes based on history field
- Preserve URL parameters and make filters linkable
- Add responsive design for mobile and desktop
- Include informative messages for empty results

Closes #[issue-number]"

# Push a tu branch
git push origin feature/episode-filtering
```

## Notas Importantes

- **No incluir** `docker-compose.dev.yml` en el commit final (es solo para desarrollo local)
- **No incluir** archivos de documentación temporal como `EPISODE_FILTER_CHANGES.md`
- Los cambios son **100% compatibles** con la funcionalidad existente
- El filtro funciona tanto con **fuentes TMDB** como **manuales**