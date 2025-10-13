# 🚀 Guía de Actualización del Servidor Yamtrack

Esta guía te ayudará a actualizar tu servidor Yamtrack con los nuevos cambios desde tu fork sin pérdida de datos.

## 📋 Nuevas Funcionalidades Incluidas

1. ✅ **Filtros de episodios** (visto/no visto) en detalles de temporada
2. ✅ **Modal de confirmación** para completar temporadas automáticamente
3. ✅ **Mejoras en Create Custom** con auto-incremento de números de episodio
4. ✅ **Campo Air Date** para episodios (fecha de emisión)
5. ✅ **Campo Runtime** para episodios (duración en minutos)

## ⚠️ IMPORTANTE: Proceso de Actualización

### 🔄 Commits Subidos al Fork
Los siguientes commits están ahora disponibles en tu fork:
- `8822ea24` - feat: Add 'min' suffix to episode runtime display
- `d4654057` - feat: Add runtime field for episodes  
- `c71843be` - refactor: Optimize Dockerfile build order
- `f1066c27` - feat: Add episode auto-increment and improve episode creation
- `628325bf` - fix: Display air_date for manual episodes in UI
- `d12bdd27` - feat: Add air_date field for episodes
- `3bf13852` - feat: Add completion confirmation for season progress cards
- `7db0fe09` - feat: Add completion confirmation modal for season's last episode

## 🛠️ Instrucciones para el Servidor

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

### 3. 🔄 Cambiar Remoto a tu Fork

```bash
# Cambiar el remoto origin para apuntar a tu fork
git remote set-url origin https://github.com/rtomagnini/Yamtrack.git

# Verificar que cambió correctamente
git remote -v
```

### 4. 📥 Obtener los Nuevos Cambios

```bash
# Hacer pull de todos los cambios desde tu fork
git pull origin master

# Verificar que tienes todos los commits
git log --oneline -10
```

### 5. 🚀 Reconstruir y Aplicar Cambios

```bash
# Reconstruir contenedores con los nuevos cambios
docker-compose up -d --build

# O si usas docker compose (sin guión):
docker compose up -d --build
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

### Volver al Código Anterior
```bash
# Ver commits disponibles
git log --oneline

# Volver a un commit anterior específico
git reset --hard COMMIT_HASH_ANTERIOR

# Reconstruir con el código anterior
docker-compose up -d --build
```

## 📝 Notas Importantes

1. **Backup obligatorio**: SIEMPRE haz backup antes de actualizar
2. **Migraciones automáticas**: Django las aplicará automáticamente
3. **Sin pérdida de datos**: Los nuevos campos son opcionales (null=True, blank=True)
4. **Compatibilidad**: Compatible con episodios existentes
5. **Nuevos campos**: `air_date` y `runtime` solo aparecen en episodios

## 🎉 Después de la Actualización

Una vez completada la actualización, tendrás acceso a:

- **Filtros de episodios** en `/details/manual/tv/ID/TITLE/season/NUM`
- **Modals de confirmación** cuando completes temporadas
- **Auto-incremento** de números de episodio en Create Custom
- **Campos Air Date y Runtime** en la creación de episodios
- **Mejor experiencia de usuario** en general

---

## 📞 Soporte

Si encuentras algún problema durante la actualización:

1. Verifica los logs: `docker-compose logs web`
2. Asegúrate de que todos los contenedores estén corriendo: `docker-compose ps`
3. Si hay errores de migración, verifica: `docker-compose exec web python manage.py showmigrations`

---

**¡Buena suerte con la actualización! 🚀**