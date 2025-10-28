# Yamtrack Copilot Instructions

## Project Overview
Yamtrack is a Django-based, self-hosted media tracker for movies, TV shows, anime, manga, games, and books. It supports multi-user accounts, custom lists, calendar integration, notifications, and integrations with external services (Jellyfin, Plex, Emby, Trakt, Simkl, MyAnimeList, AniList, Kitsu).

## Architecture & Key Components
- **Backend:** Django app in `src/app/` (models, views, admin, migrations, providers, management commands)
- **Frontend:** Django templates in `src/templates/`, static assets in `src/static/`, Tailwind CSS for styling
- **Celery:** Background tasks configured in `src/config/celery.py`
- **Database:** SQLite (default) or PostgreSQL (see Docker configs)
- **Integrations:** External APIs (TMDB, MAL, IGDB, Steam, etc.) via provider modules in `src/app/providers/`
- **Docker:** Multiple `docker-compose` files for different environments

## Developer Workflows
- **Local development:**
  1. Install dependencies: `python -m pip install -U -r requirements-dev.txt`
  2. Start Redis (required for Celery): `docker run -d --name redis -p 6379:6379 --restart unless-stopped redis:7-alpine`
  3. Run migrations: `cd src && python manage.py migrate`
  4. Start services (from `src/`):
     - Django: `python manage.py runserver`
     - Celery: `celery -A config worker --beat --scheduler django --loglevel DEBUG`
     - Tailwind: `tailwindcss -i ./static/css/input.css -o ./static/css/tailwind.css --watch`
- **Testing:**
  - Use `pytest` (configured via `pytest.ini`).
  - Test files are in `src/app/tests/`.
- **Docker:**
  - Use `docker-compose.dev.yml` (SQLite) or `docker-compose.postgres.yml` (Postgres) for deployment.

## Project-Specific Conventions
- **App structure:** All Django app code is under `src/app/`.
- **Settings:** Environment variables are required (see `.env` example in README). Use the correct `URLS` variable for reverse proxy setups.
- **Custom management commands:** Place in `src/app/management/commands/`.
- **Providers:** External API integrations are modularized in `src/app/providers/`.
- **Templates:** Use Django template inheritance. Place app templates in `src/templates/app/`.
- **Static files:** Use Tailwind for CSS. Output is `src/static/css/tailwind.css`.

## Integration Points
- **Celery tasks:** Defined in app modules, registered in `src/config/celery.py`.
- **External APIs:** API keys/secrets are required in `.env`.
- **Reverse proxy:** Set `URLS` env variable to match external URL.

## Examples
- To add a new provider, create a module in `src/app/providers/` and register it.
- To add a new management command, place a Python file in `src/app/management/commands/`.
- To add a new template, use `src/templates/app/` and extend base templates.

## References
- See `README.md` for setup, environment variables, and deployment details.
- See `src/app/` for main Django app code.
- See `src/config/` for Celery and project settings.

---

If you are unsure about a workflow or convention, check the README or existing code in the referenced directories. Update this file if you discover new project-specific patterns.
