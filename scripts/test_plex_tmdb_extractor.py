

import json
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

# Inicializar Django
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from integrations.webhooks.plex import PlexWebhookProcessor

# Payload de ejemplo basado en tu XML (simulando el dict que recibe el webhook)
payload = {
    "Metadata": {
        "grandparentTitle": "El hormiguero",
        "parentIndex": 20,
        "index": 54,
        "ratingKey": "58255",
        "Media": [
            {
                "Part": [
                    {
                        "file": "/volume1/Multimedia/Series/El Hormiguero {tmdb-6809}/El Hormiguero.S20E54.Sonsoles Ónega y Verónica Sánchez (02-12-25)..1080p.H264.AAC.Youtube Plus.mp4"
                    }
                ]
            }
        ],
        # Simula que no hay GUID de TMDB
        "Guid": [
            {"id": "local://58255"}
        ]
    }
}

# Instancia el procesador (no requiere user para esta prueba)
plex_proc = PlexWebhookProcessor()

# Prueba la extracción directa del TMDB ID
extracted_tmdb_id = plex_proc._extract_series_tmdb_id(payload)
print(f"TMDB ID extraído: {extracted_tmdb_id}")

# También puedes probar la función interna de file path:
extracted_from_path = plex_proc._extract_tmdb_id_from_file_path(payload)
print(f"TMDB ID extraído del path: {extracted_from_path}")

# Si quieres probar con un payload sin Media/Part, simplemente elimina esa clave y repite la prueba.