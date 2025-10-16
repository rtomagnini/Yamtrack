"""
Management command to add external ID mappings for Plex fake TMDB IDs.
"""

from django.core.management.base import BaseCommand, CommandError

from app.models import ExternalIdMapping, MediaTypes


class Command(BaseCommand):
    """Add external ID mapping for Plex fake TMDB IDs."""
    
    help = 'Add mapping from Plex fake TMDB ID to real TMDB ID'

    def add_arguments(self, parser):
        parser.add_argument(
            'plex_tmdb_id',
            help='Fake TMDB ID that Plex is sending'
        )
        parser.add_argument(
            'real_tmdb_id', 
            help='Real TMDB ID that actually exists'
        )
        parser.add_argument(
            '--title',
            required=True,
            help='Series/movie title for reference'
        )
        parser.add_argument(
            '--media-type',
            choices=['tv', 'movie'],
            default='tv',
            help='Media type (default: tv)'
        )
        parser.add_argument(
            '--external-source',
            default='plex',
            help='External source name (default: plex)'
        )

    def handle(self, *args, **options):
        plex_tmdb_id = options['plex_tmdb_id']
        real_tmdb_id = options['real_tmdb_id']
        title = options['title']
        media_type = MediaTypes.TV.value if options['media_type'] == 'tv' else MediaTypes.MOVIE.value
        external_source = options['external_source']

        # Check if mapping already exists
        existing = ExternalIdMapping.objects.filter(
            tmdb_id_plex=plex_tmdb_id,
            external_source=external_source,
            media_type=media_type
        ).first()
        
        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f'Mapping already exists: {existing}'
                )
            )
            
            # Ask if user wants to update
            update = input('Update existing mapping? [y/N]: ').lower().strip()
            if update in ['y', 'yes']:
                existing.real_tmdb_id = real_tmdb_id
                existing.title = title
                existing.save()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Successfully updated mapping: {existing}'
                    )
                )
            else:
                self.stdout.write('Mapping not updated.')
            return

        # Create new mapping
        try:
            mapping = ExternalIdMapping.objects.create(
                tmdb_id_plex=plex_tmdb_id,
                external_source=external_source,
                real_tmdb_id=real_tmdb_id,
                media_type=media_type,
                title=title
            )
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully created mapping: {mapping}'
                )
            )
            
        except Exception as e:
            raise CommandError(f'Error creating mapping: {e}')