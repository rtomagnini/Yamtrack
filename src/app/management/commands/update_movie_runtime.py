"""Management command to update runtime for existing movie items."""
from django.core.management.base import BaseCommand
from app.models import Item, MediaTypes, Sources
from app.providers import tmdb


class Command(BaseCommand):
    """Update runtime for existing movie items from TMDB."""

    help = "Update runtime for existing movie items from TMDB"

    def handle(self, *args, **options):
        """Execute the command."""
        movies = Item.objects.filter(
            media_type=MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            runtime__isnull=True,
        )

        total = movies.count()
        self.stdout.write(f"Found {total} movies without runtime")

        updated = 0
        for movie in movies:
            try:
                metadata = tmdb.movie(movie.media_id)
                if metadata.get("runtime"):
                    movie.runtime = metadata["runtime"]
                    movie.save(update_fields=["runtime"])
                    updated += 1
                    self.stdout.write(f"Updated {movie.title}: {metadata['runtime']} min")
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error updating {movie.title}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(f"Successfully updated {updated}/{total} movies")
        )
