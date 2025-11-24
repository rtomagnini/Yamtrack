"""Management command to backfill GameSession records from historical data."""
from django.core.management.base import BaseCommand
from app.models import Game, GameSession


class Command(BaseCommand):
    """Backfill GameSession records from Game history."""

    help = "Create GameSession records from existing Game history data"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be created without actually creating records',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No records will be created'))
        
        games = Game.objects.all()
        total_sessions = 0
        
        for game in games:
            history = game.history.all().order_by('history_date')
            previous_play_time = 0
            game_sessions_created = 0
            
            for record in history:
                if hasattr(record, 'play_time') and record.play_time and record.play_time > previous_play_time:
                    session_time = record.play_time - previous_play_time
                    session_date = getattr(record, 'progressed_at', None) or record.history_date
                    percentage = getattr(record, 'percentage_progress', None)
                    
                    if not dry_run:
                        existing = GameSession.objects.filter(
                            game=game,
                            session_date=session_date,
                            minutes=session_time
                        ).exists()
                        
                        if not existing:
                            GameSession.objects.create(
                                game=game,
                                minutes=session_time,
                                percentage_progress=percentage,
                                session_date=session_date,
                                source=GameSession.SessionSource.BACKFILL,
                            )
                            game_sessions_created += 1
                    else:
                        date_str = session_date.strftime('%Y-%m-%d')
                        self.stdout.write(
                            f"  Would create session: {game.item.title} - {session_time} min on {date_str}"
                        )
                        game_sessions_created += 1
                    
                    previous_play_time = record.play_time
            
            if game_sessions_created > 0:
                total_sessions += game_sessions_created
                action = "Would create" if dry_run else "Created"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{action} {game_sessions_created} session(s) for '{game.item.title}'"
                    )
                )
        
        if total_sessions == 0:
            self.stdout.write(self.style.SUCCESS('No sessions need to be backfilled'))
        else:
            action = "would be created" if dry_run else "created"
            self.stdout.write(
                self.style.SUCCESS(f"Total: {total_sessions} session(s) {action}")
            )
