import requests
import json

URL = 'https://raw.githubusercontent.com/Kometa-Team/Anime-IDs/refs/heads/master/anime_ids.json'
print('fetching', URL)
r = requests.get(URL, timeout=30)
print('status', r.status_code)
js = r.json()
print('total entries', len(js))

matches = [(k,v) for k,v in js.items() if v.get('tmdb_movie_id')==10494 or (isinstance(v.get('tmdb_movie_ids'), list) and 10494 in v.get('tmdb_movie_ids')) or v.get('tmdb_id')==10494]
print('matches for tmdb_movie_id 10494:', len(matches))
for k,v in matches:
    print('key:', k)
    print('  tmdb_movie_id:', v.get('tmdb_movie_id'))
    print('  tmdb_movie_ids:', v.get('tmdb_movie_ids'))
    print('  mal_id:', v.get('mal_id'))
    print('  sample:', {kk: v.get(kk) for kk in ['title','anime_title','name'] if kk in v})
    break

mal_matches = [(k,v) for k,v in js.items() if 'mal_id' in v and ('437' in str(v.get('mal_id')) or str(v.get('mal_id'))=='437')]
print('entries with mal_id containing 437:', len(mal_matches))
for k,v in mal_matches[:5]:
    print('key',k,'tmdb_movie_id',v.get('tmdb_movie_id'),'mal_id',v.get('mal_id'))

print('done')
