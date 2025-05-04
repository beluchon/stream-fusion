import time
import re
import hashlib
import json
import urllib.parse
import zlib
import base64
from fastapi import APIRouter, Depends, HTTPException, Request
import asyncio

from stream_fusion.services.postgresql.dao.apikey_dao import APIKeyDAO
from stream_fusion.services.postgresql.dao.torrentitem_dao import TorrentItemDAO
from stream_fusion.services.redis.redis_config import get_redis_cache_dependency
from stream_fusion.utils.cache.cache import search_public
from stream_fusion.utils.cache.local_redis import RedisCache
from stream_fusion.utils.debrid.get_debrid_service import get_all_debrid_services
from stream_fusion.utils.filter.results_per_quality_filter import (
    ResultsPerQualityFilter,
)
from stream_fusion.utils.filter_results import (
    filter_items,
    merge_items,
    sort_items,
)
from stream_fusion.logging_config import logger
from stream_fusion.utils.jackett.jackett_result import JackettResult
from stream_fusion.utils.jackett.jackett_service import JackettService
from stream_fusion.utils.parser.parser_service import StreamParser
from stream_fusion.utils.sharewood.sharewood_service import SharewoodService
from stream_fusion.utils.yggfilx.yggflix_service import YggflixService
from stream_fusion.utils.metdata.cinemeta import Cinemeta
from stream_fusion.utils.metdata.tmdb import TMDB
from stream_fusion.utils.models.movie import Movie
from stream_fusion.utils.models.series import Series
from stream_fusion.utils.parse_config import parse_config
from stream_fusion.utils.security.security_api_key import check_api_key
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.web.root.search.schemas import SearchResponse, Stream
from stream_fusion.web.root.search.stremio_parser import parse_to_stremio_streams
from stream_fusion.utils.torrent.torrent_service import TorrentService
from stream_fusion.utils.torrent.torrent_smart_container import TorrentSmartContainer
from stream_fusion.utils.zilean.zilean_result import ZileanResult
from stream_fusion.utils.zilean.zilean_service import ZileanService
from stream_fusion.settings import settings
from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
from concurrent.futures import ThreadPoolExecutor
import base64
import json
import urllib.parse
import zlib
import re


# Fonctions de génération de clés de cache au niveau global
def stream_cache_key(media):
    # Utiliser un identifiant générique pour le cache au lieu de dépendre de variables externes
    try:
        # Identifiant générique sécurisé
        cache_user_identifier = 'generic_user'
            
        if isinstance(media, Movie):
            # S'assurer que media.titles est une liste non vide
            title = media.titles[0] if hasattr(media, 'titles') and media.titles else 'unknown_title'
            year = getattr(media, 'year', 'unknown_year')
            language = media.languages[0] if hasattr(media, 'languages') and media.languages else 'unknown_lang'
            key_string = f"stream:{cache_user_identifier}:{title}:{year}:{language}"
        elif isinstance(media, Series):
            # S'assurer que media.titles est une liste non vide
            title = media.titles[0] if hasattr(media, 'titles') and media.titles else 'unknown_title'
            language = media.languages[0] if hasattr(media, 'languages') and media.languages else 'unknown_lang'
            season = getattr(media, 'season', 'S00')
            episode = getattr(media, 'episode', 'E00')
            key_string = f"stream:{cache_user_identifier}:{title}:{language}:{season}{episode}"
        else:
            logger.error("Search: Only Movie and Series are allowed as media!")
            raise HTTPException(
                status_code=500, detail="Only Movie and Series are allowed as media!"
            )
        
        # Générer le hash de manière sécurisée
        hashed_key = hashlib.sha256(key_string.encode("utf-8")).hexdigest()
        return hashed_key[:16]
    except Exception as e:
        logger.error(f"Search: Error generating cache key: {e}")
        # Fallback sécurisé en cas d'erreur
        try:
            return hashlib.md5(str(media.__dict__).encode("utf-8")).hexdigest()[:16]
        except:
            return hashlib.md5(str(time.time()).encode("utf-8")).hexdigest()[:16]


def media_cache_key(media):
    try:
        if isinstance(media, Movie):
            key_string = f"media:{media.titles[0]}:{media.year}:{media.languages[0]}"
        elif isinstance(media, Series):
            key_string = f"media:{media.titles[0]}:{media.languages[0]}:{media.season}"
        else:
            raise TypeError("Only Movie and Series are allowed as media!")
        hashed_key = hashlib.sha256(key_string.encode("utf-8")).hexdigest()
        return hashed_key[:16]
    except Exception as e:
        logger.error(f"Search: Error generating media cache key: {e}")
        # Fallback sécurisé en cas d'erreur
        try:
            return hashlib.md5(str(media.__dict__).encode("utf-8")).hexdigest()[:16]
        except:
            return hashlib.md5(str(time.time()).encode("utf-8")).hexdigest()[:16]


router = APIRouter()

@router.get("/{config}/stream/{stream_type}/{stream_id:path}", response_model=SearchResponse)
async def get_results(
    request: Request,
    config: str,
    stream_type: str,
    stream_id: str,
    redis_cache: RedisCache = Depends(get_redis_cache_dependency),
    apikey_dao: APIKeyDAO = Depends(),
    torrent_dao: TorrentItemDAO = Depends(),
) -> SearchResponse:
    start = time.time()
    logger.info(f"Search: Stream request initiated for {stream_type} - {stream_id}")

    stream_id = stream_id.replace(".json", "")
    config = parse_config(config)
    api_key = config.get("apiKey")
    ip_address = request.client.host
    # Only validate the API key if it exists
    if api_key:
        try:
            await check_api_key(api_key, apikey_dao)
            logger.info(f"Search: Valid API key provided by {ip_address}")
        except HTTPException as e:
             # Re-raise the exception if validation fails
             logger.warning(f"Search: Invalid API key provided by {ip_address}. Error: {e.detail}")
             raise e
    else:
        logger.info(f"Search: No API key provided by {ip_address}. Proceeding without API key validation.")

    debrid_services = get_all_debrid_services(config)
    logger.debug(f"Search: Found {len(debrid_services)} debrid services")
    logger.info(
        f"Search: Debrid services: {[debrid.__class__.__name__ for debrid in debrid_services]}"
    )

    def get_metadata():
        logger.info(f"Search: Fetching metadata from {config['metadataProvider']}")
        if config["metadataProvider"] == "tmdb" and settings.tmdb_api_key:
            metadata_provider = TMDB(config)
        else:
            metadata_provider = Cinemeta(config)
        return metadata_provider.get_metadata(stream_id, stream_type)

    media = await redis_cache.get_or_set(
        get_metadata, stream_id, stream_type, config["metadataProvider"]
    )
    logger.debug(f"Search: Retrieved media metadata for {str(media.titles)}")


    # Vérifier si le cache a été invalidé globalement
    force_refresh = False
    
    try:
        # 1. Vérifier d'abord s'il y a un drapeau global de rafraîchissement forcé
        global_refresh_key = "stremthru:force_refresh:all"
        global_refresh = await redis_cache.get(global_refresh_key)
        if global_refresh:
            logger.info(f"Search: Global refresh flag is set. Forcing cache refresh for all media.")
            force_refresh = True
            # Ne pas supprimer le drapeau global ici pour permettre à d'autres requêtes de bénéficier du rafraîchissement
        
        # Vérifier par IMDB ID
        if hasattr(media, 'imdb_id') and media.imdb_id:
            imdb_key = f"stremthru:imdb:{media.imdb_id}"
            imdb_update = await redis_cache.get(imdb_key)
            if imdb_update:
                logger.info(f"Search: Media with IMDB ID {media.imdb_id} marked for update. Forcing cache refresh.")
                force_refresh = True
        
        # Vérifier par clé de média
        if not force_refresh:
            media_key = media_cache_key(media)
            media_update_key = f"stremthru:global_update_needed:{media_key}"
            media_update = await redis_cache.get(media_update_key)
            if media_update:
                logger.info(f"Search: Media {media_key} marked as needing update. Forcing cache refresh.")
                force_refresh = True
                await redis_cache.delete(media_update_key)
        
        # Vérifier la clé globale d'invalidation de cache pour StremThru
        if not force_refresh:
            global_update_key = "stremthru:global_update_needed"
            global_update = await redis_cache.get(global_update_key)
            if global_update:
                logger.info("Search: Global StremThru update flag found. Forcing cache refresh.")
                force_refresh = True
                # Ne pas supprimer la clé globale ici, car d'autres requêtes pourraient en avoir besoin
    except Exception as e:
        logger.error(f"Search: Error checking for cache invalidation flags: {e}")
    
    cached_result = None if force_refresh else await redis_cache.get(stream_cache_key(media))
    if cached_result is not None:
        logger.info(f"Search: Found cached processed results, checking for StremThru links. Type: {type(cached_result)}, Length: {len(cached_result) if isinstance(cached_result, list) else 'not a list'}")
        if force_refresh:
            logger.info("Search: Force refresh flag is set, but cached results were found anyway. This shouldn't happen.")
        
        # Vérifier s'il y a des streams non mis en cache stockés séparément
        non_cached_key = f"non_cached:{stream_cache_key(media)}"
        non_cached_streams = await redis_cache.get(non_cached_key)
        if non_cached_streams:
            logger.info(f"Search: Found {len(non_cached_streams)} non-cached streams in separate cache")
        else:
            non_cached_streams = []
            logger.info("Search: No non-cached streams found in separate cache")
        
        # Même si nous avons des résultats en cache, vérifions les liens StremThru fonctionnels
        # pour mettre à jour les disponibilités avant de retourner les résultats
        try:
            logger.info(f"Search: Starting to process cached results. Type of first item: {type(cached_result[0]) if cached_result else 'None'}")
            # Vérifier si les objets sont déjà des instances de Stream ou des dictionnaires
            if cached_result and isinstance(cached_result[0], dict):
                logger.info("Search: Converting dictionary items to Stream objects")
                cached_streams = [Stream(**stream) for stream in cached_result]
            else:
                # Si ce sont déjà des objets Stream, les utiliser directement
                logger.info("Search: Using cached items directly (already Stream objects or other type)")
                cached_streams = cached_result
            
            # Faire de même pour les streams non mis en cache
            if non_cached_streams and isinstance(non_cached_streams[0], dict):
                non_cached_stream_objects = [Stream(**stream) for stream in non_cached_streams]
            else:
                non_cached_stream_objects = non_cached_streams
            
            # Combiner les streams mis en cache et non mis en cache
            streams = cached_streams + non_cached_stream_objects
            logger.info(f"Search: Combined {len(cached_streams)} cached streams with {len(non_cached_stream_objects)} non-cached streams")
            updated = False
            processed_streams = [] # List to hold successfully processed streams
            logger.info(f"Search: Successfully processed cached results. Type of streams: {type(streams)}, Length: {len(streams) if isinstance(streams, list) else 'not a list'}")
            
            # Vérifier les liens StremThru fonctionnels pour chaque stream
            logger.info(f"Search: Starting to check StremThru links for {len(streams) if isinstance(streams, list) else 'unknown number of'} streams")
            for i, stream in enumerate(streams):
                try:
                    # 1. Extraire les informations de base du stream (nom, URL, info_hash de l'attribut)
                    stream_name_attr = None
                    stream_url = None
                    stream_info_hash_attr = None
                    updated_stream = False # Flag if this specific stream was updated by Redis check

                    if isinstance(stream, dict):
                        stream_name_attr = stream.get('name') or stream.get('title')
                        stream_url = stream.get('url')
                        stream_info_hash_attr = stream.get('infoHash') or stream.get('info_hash')
                        # logger.debug(f"Search: Stream {i+1} is dict. Name: '{stream_name_attr}', URL: '{stream_url}', InfoHash attr: '{stream_info_hash_attr}'")
                    elif hasattr(stream, 'name') or hasattr(stream, 'url'): # Check for object attributes
                        stream_name_attr = getattr(stream, 'name', None) or getattr(stream, 'title', None)
                        stream_url = getattr(stream, 'url', None)
                        stream_info_hash_attr = getattr(stream, 'infoHash', None) or getattr(stream, 'info_hash', None)
                        # logger.debug(f"Search: Stream {i+1} is object. Name: '{stream_name_attr}', URL: '{stream_url}', InfoHash attr: '{stream_info_hash_attr}'")
                    else:
                        logger.warning(f"Search: Stream {i+1} - Unrecognized stream format: {stream}")
                        continue # Skip this stream if format is unknown

                    # Initialiser les variables pour ce stream dans le try
                    info_hash = None
                    original_name = None
                    store_code = None

                    # 2. Extract and clean original_name
                    if stream_name_attr and isinstance(stream_name_attr, str):
                        # Basic cleaning: remove potential indicators and leading/trailing whitespace
                        name_parts = stream_name_attr.split('\n')
                        if len(name_parts) > 0:
                            potential_name = name_parts[0].strip()
                            # Remove common prefixes only if they are at the beginning
                            prefixes_to_remove = ['[RD+]', '[AD+]', '[PM+]','[ST:', '']
                            for prefix in prefixes_to_remove:
                                if potential_name.startswith(prefix):
                                    # Find the closing ']' for store codes like [ST:RD]
                                    if prefix == '[ST:':
                                        closing_bracket_index = potential_name.find(']')
                                        if closing_bracket_index != -1:
                                            potential_name = potential_name[closing_bracket_index+1:].strip()
                                        else: # If no closing bracket found, remove the prefix part
                                             potential_name = potential_name[len(prefix):].strip()
                                    else:
                                         potential_name = potential_name[len(prefix):].strip()
                                    
                            # Remove quality tags like (4K), (1080p)
                            potential_name = re.sub(r'\s*\(?(4K|2160p|1080p|720p|480p|SD)\)?$', '', potential_name, flags=re.IGNORECASE).strip()
                            original_name = potential_name
                            logger.debug(f"Search: Stream {i+1} - Extracted and cleaned original_name: '{original_name}'")
                        else:
                             logger.warning(f"Search: Stream {i+1} - Failed to extract original_name from parts: {name_parts}")
                    else:
                         logger.warning(f"Search: Stream {i+1} - Missing or invalid name attribute: '{stream_name_attr}'")

                    # 3. Extract info_hash (prioritize attribute, fallback to URL)
                    if stream_info_hash_attr:
                        # Validate and use info_hash from attribute if it's a valid 40-char hex
                        potential_hash = str(stream_info_hash_attr).lower()
                        if len(potential_hash) == 40 and all(c in '0123456789abcdef' for c in potential_hash):
                            info_hash = potential_hash
                            logger.debug(f"Search: Stream {i+1} - Used info_hash from attribute: '{info_hash}'")
                        else:
                             logger.warning(f"Search: Stream {i+1} - Attribute info_hash '{potential_hash}' is invalid. Will try URL parsing.")
                    
                    # Try parsing URL only if info_hash wasn't found or valid in attributes
                    if not info_hash and stream_url:
                        # Handle StremThru specific Base64 encoded URLs
                        if 'sf.stremiofr.com/playback' in stream_url:
                            # Extraire le store_code de l'URL StremThru ou du nom du stream
                            
                            # 1. Vérifier d'abord dans le nom du stream (plus fiable)
                            if stream_name_attr:
                                # Rechercher des patterns comme [ST:XX], ⚡ST:XX+, etc.
                                patterns = [
                                    r'\[ST:([A-Z]{2,})\]',  # [ST:XX]
                                    r'⚡ST:([A-Z]{2,})\+',  # ⚡ST:XX+
                                    r'ST:([A-Z]{2,})',      # ST:XX (sans crochets)
                                ]
                                
                                for pattern in patterns:
                                    st_match = re.search(pattern, stream_name_attr)
                                    if st_match:
                                        store_code = st_match.group(1)
                                        logger.debug(f"Search: Stream {i+1} - Extracted store_code '{store_code}' from stream name using pattern '{pattern}'")
                                        break
                            
                            # 2. Si pas trouvé dans le nom, essayer l'URL
                            if not store_code and 'sf.stremiofr.com/playback' in stream_url:
                                # Format possible: .../playback/{store_code}/...
                                # Le store_code est généralement 2-3 lettres (RD, AD, PM, etc.)
                                store_code_match = re.search(r'sf\.stremiofr\.com/playback/([A-Z]{2,3})(?:/|$)', stream_url, re.IGNORECASE)
                                if store_code_match:
                                    store_code = store_code_match.group(1).upper()  # Normaliser en majuscules
                                    logger.debug(f"Search: Stream {i+1} - Extracted store_code '{store_code}' from StremThru URL")
                            
                            # 3. Dernier recours: utiliser des indices du nom pour deviner le store_code
                            if not store_code and stream_name_attr:
                                # Correspondances connues entre préfixes et store_codes
                                prefix_to_store = {
                                    '[RD+]': 'RD',  # RealDebrid
                                    '[AD+]': 'AD',  # AllDebrid
                                    '[PM+]': 'PM',  # Premiumize
                                }
                                
                                for prefix, code in prefix_to_store.items():
                                    if stream_name_attr.startswith(prefix) or prefix in stream_name_attr:
                                        store_code = code
                                        logger.debug(f"Search: Stream {i+1} - Inferred store_code '{store_code}' from prefix '{prefix}' in name")
                                        break
                            
                            # Si nous avons un store_code mais pas d'info_hash, chercher dans le nom pour des indices
                            if store_code and not info_hash and original_name:
                                # Chercher un pattern de hash dans le nom (peu probable mais possible)
                                hash_pattern = re.search(r'[a-fA-F0-9]{40}', original_name)
                                if hash_pattern:
                                    potential_hash = hash_pattern.group(0).lower()
                                    if all(c in '0123456789abcdef' for c in potential_hash):
                                        info_hash = potential_hash
                                        logger.debug(f"Search: Stream {i+1} - Extracted info_hash from name: '{info_hash}'")
                            
                            # Si nous avons un store_code mais toujours pas d'info_hash, vérifier dans Redis
                            # pour des hash déjà marqués comme fonctionnels avec ce store_code
                            # Cette logique est déjà gérée plus tard dans le code, dans la section "Check Redis for working StremThru links"
                            
                            # Si l'extraction directe a échoué, essayer le décodage des données encodées
                            try: # --- Try interne pour StremThru --- 
                                url_parts = stream_url.split('/playback')
                                if len(url_parts) > 1:
                                    encoded_part = url_parts[1]
                                    if encoded_part.startswith('/'):
                                        encoded_part = encoded_part[1:]
                                    
                                    # Afficher un diagnostic des données encodées pour le débogage
                                    logger.debug(f"Search: Stream {i+1} - Encoded part (first 30 chars): '{encoded_part[:30]}...'")
                                    
                                    # Tentative de décodage avec différents schémas
                                    decoded_data = None
                                    
                                    # Essai 1: URL decode -> B64 -> Gzip -> JSON (si nécessaire)
                                    try:
                                        # Certaines URL peuvent avoir besoin d'un décodage URL d'abord
                                        url_decoded = urllib.parse.unquote(encoded_part)
                                        if url_decoded != encoded_part:
                                            logger.debug(f"Search: Stream {i+1} - URL decoded part (first 30): '{url_decoded[:30]}...'")
                                            encoded_part = url_decoded
                                    except Exception as url_e:
                                        logger.debug(f"Search: Stream {i+1} - URL decoding failed: {url_e}. Using original.")
                                    
                                    try:
                                        # Essai 1: B64 -> Gzip -> JSON
                                        try:
                                            gzipped_data = base64.b64decode(encoded_part)
                                            logger.debug(f"Search: Stream {i+1} - B64 decode successful, data length: {len(gzipped_data)}")
                                            
                                            # Tenter la décompression gzip
                                            decoded_json_str = zlib.decompress(gzipped_data, 16 + zlib.MAX_WBITS).decode('utf-8')
                                            logger.debug(f"Search: Stream {i+1} - Gzip decompression successful")
                                            
                                            # Analyser le JSON
                                            decoded_data = json.loads(decoded_json_str)
                                            logger.debug(f"Search: Stream {i+1} - JSON parsing successful, keys: {list(decoded_data.keys()) if isinstance(decoded_data, dict) else 'not a dict'}")
                                            
                                        except (base64.binascii.Error, zlib.error, json.JSONDecodeError, UnicodeDecodeError) as e1:
                                            logger.debug(f"Search: Stream {i+1} - Failed B64->Gzip->JSON decoding: {e1}. Trying B64->JSON...")
                                            
                                            # Essai 2: B64 -> JSON (sans gzip)
                                            try:
                                                decoded_bytes = base64.b64decode(encoded_part)
                                                decoded_json_str = decoded_bytes.decode('utf-8')
                                                logger.debug(f"Search: Stream {i+1} - B64->UTF8 decode successful, first 30 chars: '{decoded_json_str[:30]}...'")
                                                
                                                decoded_data = json.loads(decoded_json_str)
                                                logger.debug(f"Search: Stream {i+1} - Direct JSON parsing successful")
                                                
                                            except (base64.binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as e2:
                                                logger.warning(f"Search: Stream {i+1} - Failed B64->JSON decoding as well: {e2}. Trying URL-safe B64...")
                                                
                                                # Essai 3: URL-safe B64 -> JSON
                                                try:
                                                    # Certaines implémentations utilisent le base64 URL-safe
                                                    decoded_bytes = base64.urlsafe_b64decode(encoded_part + '=' * (4 - len(encoded_part) % 4))
                                                    decoded_json_str = decoded_bytes.decode('utf-8')
                                                    decoded_data = json.loads(decoded_json_str)
                                                    logger.debug(f"Search: Stream {i+1} - URL-safe B64 decode successful")
                                                except Exception as e3:
                                                    logger.warning(f"Search: Stream {i+1} - All decoding methods failed. Giving up on StremThru URL.")
                                    except Exception as e_inner:
                                         logger.error(f"Search: Stream {i+1} - Unexpected error during inner StremThru decoding: {e_inner}", exc_info=True)
                                    if decoded_data and isinstance(decoded_data, dict):
                                        # Afficher la structure complète du JSON pour le débogage
                                        logger.debug(f"Search: Stream {i+1} - JSON structure: {list(decoded_data.keys())}")
                                        
                                        # Rechercher d'abord dans les clés directes
                                        potential_hash_keys = ['info_hash', 'infoHash', 'hash', 'torrent_hash', 'torrentHash', 'magnetHash']
                                        potential_hash_st = None
                                        
                                        # Chercher dans les clés directes
                                        for key in potential_hash_keys:
                                            if key in decoded_data and decoded_data[key]:
                                                potential_hash_st = decoded_data[key]
                                                logger.debug(f"Search: Stream {i+1} - Found hash in key '{key}': {potential_hash_st}")
                                                break
                                        
                                        # Si pas trouvé dans les clés directes, chercher dans les sous-objets
                                        if not potential_hash_st:
                                            # Chercher dans les sous-objets courants de StremThru
                                            sub_objects = ['torrent', 'magnet', 'stream', 'file', 'data']
                                            for obj_key in sub_objects:
                                                if obj_key in decoded_data and isinstance(decoded_data[obj_key], dict):
                                                    sub_obj = decoded_data[obj_key]
                                                    logger.debug(f"Search: Stream {i+1} - Checking sub-object '{obj_key}': {list(sub_obj.keys())}")
                                                    
                                                    for hash_key in potential_hash_keys:
                                                        if hash_key in sub_obj and sub_obj[hash_key]:
                                                            potential_hash_st = sub_obj[hash_key]
                                                            logger.debug(f"Search: Stream {i+1} - Found hash in sub-object '{obj_key}.{hash_key}': {potential_hash_st}")
                                                            break
                                                    
                                                    if potential_hash_st:
                                                        break
                                        
                                        # Chercher dans les URLs qui pourraient contenir un hash
                                        if not potential_hash_st:
                                            url_keys = ['url', 'stream_url', 'streamUrl', 'magnet', 'magnetUrl', 'link']
                                            for key in url_keys:
                                                if key in decoded_data and isinstance(decoded_data[key], str) and ('btih:' in decoded_data[key] or 'magnet:' in decoded_data[key]):
                                                    url_value = decoded_data[key]
                                                    logger.debug(f"Search: Stream {i+1} - Found URL that might contain hash: {url_value[:50]}...")
                                                    
                                                    # Extraire le hash de l'URL
                                                    magnet_match = re.search(r'(btih:|magnet/|urn:btih:)([a-fA-F0-9]{40})', url_value)
                                                    if magnet_match:
                                                        potential_hash_st = magnet_match.group(2)
                                                        logger.debug(f"Search: Stream {i+1} - Extracted hash from URL in key '{key}': {potential_hash_st}")
                                                        break
                                        
                                        # Vérifier et utiliser le hash trouvé
                                        if potential_hash_st and isinstance(potential_hash_st, str):
                                            potential_hash_st = potential_hash_st.lower()
                                            if len(potential_hash_st) == 40 and all(c in '0123456789abcdef' for c in potential_hash_st):
                                                info_hash = potential_hash_st
                                                logger.debug(f"Search: Stream {i+1} - Successfully extracted info_hash from StremThru JSON: '{info_hash}'")
                                            else:
                                                logger.warning(f"Search: Stream {i+1} - Found hash '{potential_hash_st}' is invalid format.")
                                        else:
                                            # Dernier recours: chercher un pattern de hash dans tout le JSON
                                            json_str = json.dumps(decoded_data)
                                            hash_pattern = re.search(r'[a-fA-F0-9]{40}', json_str)
                                            if hash_pattern:
                                                potential_hash_st = hash_pattern.group(0).lower()
                                                logger.debug(f"Search: Stream {i+1} - Found potential hash pattern in JSON: {potential_hash_st}")
                                                if all(c in '0123456789abcdef' for c in potential_hash_st):
                                                    info_hash = potential_hash_st
                                                    logger.debug(f"Search: Stream {i+1} - Using hash pattern found in JSON as info_hash")
                                            else:
                                                logger.warning(f"Search: Stream {i+1} - No valid hash found in any expected location in StremThru JSON.")
                                    else:
                                        logger.warning(f"Search: Stream {i+1} - Failed to decode StremThru URL or result is not a dict.")
                            except Exception as e_stremthru_url:
                                logger.error(f"Search: Stream {i+1} - Error processing StremThru URL '{stream_url}': {e_stremthru_url}", exc_info=True)
                                # Ne pas 'continue', car on peut encore tenter l'extraction depuis un magnet standard

                         # Fallback for standard magnet/btih links if not found in StremThru data or URL wasn't StremThru
                        elif ('btih:' in stream_url or '/magnet/' in stream_url or 'urn:btih:' in stream_url):
                            # Standard magnet link or link containing btih
                             magnet_match = re.search(r'(btih:|magnet/|urn:btih:)([a-fA-F0-9]{40})', stream_url)
                             if magnet_match:
                                 info_hash = magnet_match.group(2).lower()
                                 logger.debug(f"Search: Stream {i+1} - Extracted info_hash from standard URL: '{info_hash}'")
                             else:
                                 logger.warning(f"Search: Stream {i+1} - Found hash indicator in standard URL but failed during extraction: {stream_url}")

                    # 4. Final info_hash validation
                    if info_hash and not (len(info_hash) == 40 and all(c in '0123456789abcdef' for c in info_hash)):
                        logger.warning(f"Search: Stream {i+1} - Invalid info_hash format extracted: '{info_hash}'. Setting to None.")
                        info_hash = None

                    # 5. Log final extracted values for the stream
                    logger.debug(f"Search: Stream {i+1} - Final Extracted -> info_hash: '{info_hash}', original_name: '{original_name}'")

                    # 6. Check Redis for working StremThru links if hash and name are valid
                    if info_hash and original_name:
                        for store_code in StremThruDebrid.STORE_CODE_TO_NAME.keys():
                            working_hash_key = f"stremthru:working:{store_code}:{info_hash}"
                            backup_key = f"stremthru_working_{store_code}_{info_hash}"
                            # logger.debug(f"Search: Stream {i+1} - Checking Redis for {store_code} key: {working_hash_key} / {backup_key}")

                            is_working = await redis_cache.get(working_hash_key) or await redis_cache.get(backup_key)

                            if is_working:
                                logger.info(f"Search: Found working StremThru link for hash {info_hash} with store_code {store_code} in Redis.")
                                # Utiliser l'éclair jaune et ST:XX + pour les liens mis en cache
                                new_name = f"⚡ ST:{store_code} + {original_name}"
                                
                                # Marquer comme mis en cache
                                if isinstance(stream, dict):
                                    stream['cached'] = True
                                elif hasattr(stream, 'cached'):
                                    stream.cached = True

                                current_name = None
                                if isinstance(stream, dict):
                                    current_name = stream.get('name') or stream.get('title')
                                elif hasattr(stream, 'name') or hasattr(stream, 'title'):
                                    current_name = getattr(stream, 'name', None) or getattr(stream, 'title', None)

                                if current_name != new_name:
                                    if isinstance(stream, dict):
                                        stream['name'] = new_name
                                        stream['available'] = True # Mark as available
                                    elif hasattr(stream, 'name'):
                                        stream.name = new_name
                                        if hasattr(stream, 'available'):
                                            stream.available = True
                                    updated_stream = True # Marquer ce stream comme mis à jour
                                    updated = True # Marquer pour la mise à jour globale du cache
                                    logger.info(f"Search: Updated stream name to: {new_name}")
                                
                                # Refresh Redis keys even if name didn't change
                                await redis_cache.set(working_hash_key, "1", expiration=604800)  # 7 days
                                await redis_cache.set(backup_key, "1", expiration=604800)      # 7 days
                                
                                # Marquer ce média comme nécessitant une mise à jour du cache
                                # Utiliser une clé basée sur le média plutôt que sur le hash
                                media_key = media_cache_key(media) if media else None
                                if media_key:
                                    global_update_key = f"stremthru:global_update_needed:{media_key}"
                                    await redis_cache.set(global_update_key, "1", expiration=3600)  # 1 heure
                                    logger.info(f"Search: Marked media {media_key} as needing cache update")
                                
                                updated = True  # ensure cache is refreshed when a working link is detected
                                break # Exit store_code loop, one indicator is enough

                except Exception as e: # Exception handling for the main stream processing loop
                    logger.error(f"Search: Error processing stream {i+1}: {e}", exc_info=True)
                    continue # Move to the next stream on error

                # Ajouter les détails traités à la liste
                if info_hash: 
                    # Stream avec info_hash valide
                    processed_streams.append({
                        'stream': stream,
                        'info_hash': info_hash,
                        'original_name': original_name,
                        'needs_update': updated_stream
                    })
                # Vérifier si c'est un stream de service de débridage (AD+, PM+, etc.) même sans info_hash
                elif original_name and any(indicator in stream_name_attr for indicator in ['⚡AD+', '⚡PM+', '⚡RD+', '⚡TB+', '⚡DL+']):
                    logger.info(f"Search: Stream {i+1} - Stream de débridage sans info_hash: '{original_name}'. Inclus quand même.")
                    # Générer un hash synthétique basé sur le nom pour permettre le suivi
                    import hashlib
                    synthetic_hash = hashlib.md5(f"{stream_name_attr}:{stream_url or ''}".encode()).hexdigest()[:40]
                    processed_streams.append({
                        'stream': stream,
                        'info_hash': synthetic_hash,  # Utiliser un hash synthétique
                        'original_name': original_name,
                        'needs_update': updated_stream,
                        'synthetic_hash': True  # Marquer comme hash synthétique
                    })
                elif store_code and 'sf.stremiofr.com/playback' in stream_url and original_name:
                    # Stream StremThru sans info_hash mais avec store_code
                    logger.info(f"Search: Stream {i+1} - StremThru stream with store_code '{store_code}' included without hash.")
                    
                    # Générer un hash synthétique basé sur l'URL et le store_code
                    synthetic_hash = hashlib.md5(f"{stream_url}:{store_code}".encode()).hexdigest()[:40]
                    logger.info(f"Search: Stream {i+1} - Generated synthetic hash '{synthetic_hash}' for StremThru stream with store_code '{store_code}'")
                    
                    # Vérifier si ce lien est mis en cache dans Redis
                    is_cached = False
                    try:
                        # Vérifier d'abord avec le hash synthétique
                        working_hash_key = f"stremthru:working:{store_code}:{synthetic_hash}"
                        backup_key = f"stremthru_working_{store_code}_{synthetic_hash}"
                        is_working = await redis_cache.get(working_hash_key) or await redis_cache.get(backup_key)
                        is_cached = bool(is_working)
                        
                        # Si nous avons un hash extrait du magnet, vérifier également avec ce hash
                        if not is_cached and info_hash:
                            working_hash_key_real = f"stremthru:working:{store_code}:{info_hash}"
                            backup_key_real = f"stremthru_working_{store_code}_{info_hash}"
                            is_working_real = await redis_cache.get(working_hash_key_real) or await redis_cache.get(backup_key_real)
                            is_cached = bool(is_working_real)
                        
                        # Vérifier si le lien est mis en cache dans Redis en utilisant le hash synthétique ou le hash réel
                        # is_cached est déjà défini en fonction des résultats de la vérification
                        
                        if is_cached:
                            logger.info(f"Search: Stream {i+1} - Found cached StremThru link with store_code '{store_code}'")
                    except Exception as e:
                        logger.error(f"Search: Stream {i+1} - Error checking cache status for StremThru link: {e}")
                        
                    # Vérifier également si le lien a été récemment utilisé avec succès
                    if not is_cached and info_hash:
                        try:
                            # Vérifier si le magnet a été utilisé avec succès récemment
                            magnet_key = f"magnet:{info_hash}:success"
                            magnet_success = await redis_cache.get(magnet_key)
                            if magnet_success:
                                is_cached = True
                                logger.info(f"Search: Stream {i+1} - Found previously successful magnet with hash '{info_hash}'")
                        except Exception as e:
                            logger.error(f"Search: Stream {i+1} - Error checking magnet success status: {e}")
                    
                    # Marquer comme disponible et potentiellement en cache
                    if isinstance(stream, dict):
                        stream['available'] = True
                        # Forcer l'affichage en fonction de l'état de mise en cache
                        if is_cached:
                            stream['cached'] = True
                            # S'assurer que le nom commence par l'éclair jaune et se termine par +
                            stream['name'] = f"⚡ ST:{store_code} +"
                            logger.info(f"Search: Stream {i+1} - Marked as cached with name '{stream['name']}'")
                            # Forcer la mise à jour du cache pour ce stream
                            updated_stream = True
                            updated = True
                        else:
                            stream['cached'] = False
                            # S'assurer que le nom commence par la flèche de téléchargement et n'a pas de +
                            stream['name'] = f"⬇️ ST:{store_code}"
                            logger.info(f"Search: Stream {i+1} - Marked as non-cached with name '{stream['name']}'")
                    elif hasattr(stream, 'available'):
                        stream.available = True
                        # Forcer l'affichage en fonction de l'état de mise en cache
                        if is_cached and hasattr(stream, 'cached'):
                            stream.cached = True
                            # S'assurer que le nom commence par l'éclair jaune et se termine par +
                            stream.name = f"⚡ ST:{store_code} +"
                            logger.info(f"Search: Stream {i+1} - Marked as cached with name '{stream.name}'")
                        else:
                            if hasattr(stream, 'cached'):
                                stream.cached = False
                            # S'assurer que le nom commence par la flèche de téléchargement et n'a pas de +
                            if hasattr(stream, 'name'):
                                stream.name = f"⬇️ ST:{store_code}"
                                logger.info(f"Search: Stream {i+1} - Marked as non-cached with name '{stream.name}'")
                    
                    # Ajouter à la liste
                    processed_streams.append({
                        'stream': stream,
                        'info_hash': synthetic_hash,  # Utiliser le hash synthétique
                        'original_name': original_name,
                        'needs_update': True
                    })
                else:
                    logger.warning(f"Search: Stream {i+1} - Final info_hash missing/invalid. Skipping.")

            # Ajouter manuellement des streams non mis en cache pour les liens StremThru
            # Cette fonction force l'inclusion des fichiers non mis en cache
            def add_non_cached_streams():
                nonlocal updated  # allow updating parent flag
                logger.info("Search: Adding non-cached streams for StremThru links")
                for i, ps in enumerate(processed_streams):
                    stream = ps['stream']
                    info_hash = ps['info_hash']
                    original_name = ps['original_name']
                    
                    # Vérifier si c'est un lien StremThru
                    if isinstance(stream, dict) and 'url' in stream and 'sf.stremiofr.com/playback' in stream['url']:
                        # Extraire le store_code de l'URL
                        url = stream['url']
                        store_code_match = re.search(r'service=([A-Za-z]{2})', url)
                        if store_code_match:
                            store_code = store_code_match.group(1).upper()
                            
                            # Extraire le hash du magnet si présent dans l'URL
                            info_hash_from_url = None
                            if 'magnet' in url:
                                magnet_match = re.search(r'magnet=([^&]+)', url)
                                if magnet_match:
                                    magnet_encoded = magnet_match.group(1)
                                    try:
                                        import urllib.parse
                                        magnet = urllib.parse.unquote(magnet_encoded)
                                        # Implémentation directe de l'extraction du hash
                                        import re
                                        m = re.search(r'btih:([A-Fa-f0-9]{40})', magnet)
                                        info_hash_from_url = m.group(1).lower() if m else None
                                    except Exception as e:
                                        logger.error(f"Search: Error extracting hash from magnet in URL: {e}")
                            
                            # Vérifier si ce lien est mis en cache dans Redis
                            is_cached = False
                            if info_hash_from_url:
                                try:
                                    # Comme nous sommes dans une fonction non-async, nous ne pouvons pas utiliser await
                                    # Nous allons simplement vérifier si le hash correspond à un hash déjà traité dans la liste processed_streams
                                    # et qui est marqué comme mis en cache
                                    
                                    # Parcourir les streams déjà traités pour voir si ce hash est marqué comme mis en cache
                                    for ps_check in processed_streams:
                                        check_stream = ps_check['stream']
                                        check_hash = ps_check['info_hash']
                                        
                                        if check_hash == info_hash_from_url and isinstance(check_stream, dict) and check_stream.get('cached', False):
                                            is_cached = True
                                            logger.info(f"Search: Found cached StremThru link for hash {info_hash_from_url} with store_code {store_code}")
                                            break
                                except Exception as e:
                                    logger.error(f"Search: Error checking cache status: {e}")
                            
                            # Créer une copie du stream pour la version non mise en cache
                            non_cached_stream = dict(stream)
                            
                            if is_cached:
                                # Afficher un lien mis en cache avec éclair jaune et code ST:XX +
                                non_cached_stream['name'] = f"⚡ ST:{store_code} +"
                                non_cached_stream['cached'] = True
                                logger.info(f"Search: Added cached stream with name '{non_cached_stream['name']}'")
                            else:
                                # Afficher un lien non mis en cache : flèche vers le bas + code ST:XX
                                non_cached_stream['name'] = f"⬇️ ST:{store_code}"
                                non_cached_stream['cached'] = False
                                logger.info(f"Search: Added non-cached stream with name '{non_cached_stream['name']}'")
                                
                            # Marquer comme disponible
                            non_cached_stream['available'] = True
                            
                            # Ajouter à la liste des streams traités
                            processed_streams.append({
                                'stream': non_cached_stream,
                                'info_hash': info_hash,
                                'original_name': original_name,
                                'needs_update': True
                            })
                            updated = True  # ensure cache refresh for new non-cached stream
                            logger.info(f"Search: Added non-cached stream for hash {info_hash} with store_code {store_code}")
            
            # Ajouter les streams non mis en cache
            add_non_cached_streams()
            
            # Mettre à jour le cache une seule fois si un stream a été modifié
            if updated: # Utilise le flag global 'updated' (qui a été mis à True si updated_stream était True)
                logger.info(f"Search: Au moins un stream a été modifié, mise à jour du cache {stream_cache_key(media)}.") 
                # Reconstruire la liste 'streams' à partir de 'processed_streams' pour la sauvegarde
                streams_to_cache = [ps['stream'] for ps in processed_streams] 
                try:
                    # Mettre en cache avec une expiration plus longue (7 jours)
                    await redis_cache.set(stream_cache_key(media), streams_to_cache, expiration=604800)  # 7 jours
                    logger.info(f"Search: Cache ({stream_cache_key(media)}) mis à jour avec succès.")
                except Exception as cache_update_e:
                    logger.error(f"Search: Échec de la mise à jour du cache ({stream_cache_key(media)}) après modifications: {cache_update_e}")

            # Utiliser la liste filtrée et potentiellement modifiée pour la suite
            streams = [ps['stream'] for ps in processed_streams]
            
            # Vérifier une dernière fois que les liens StremThru sont correctement affichés
            for i, stream in enumerate(streams):
                if isinstance(stream, dict) and 'name' in stream and 'cached' in stream:
                    if stream['cached'] and 'ST:' in stream['name'] and not stream['name'].endswith(' +'):
                        stream['name'] = stream['name'] + ' +'
                        logger.info(f"Search: Final fix - Added + to cached stream name: {stream['name']}")
                    elif not stream['cached'] and 'ST:' in stream['name'] and stream['name'].endswith(' +'):
                        stream['name'] = stream['name'].replace(' +', '')
                        logger.info(f"Search: Final fix - Removed + from non-cached stream name: {stream['name']}")
                    
                    # S'assurer que l'icône est correcte
                    if stream['cached'] and not stream['name'].startswith('⚡'):
                        if stream['name'].startswith('⬇️'):
                            stream['name'] = stream['name'].replace('⬇️', '⚡')
                        else:
                            stream['name'] = '⚡ ' + stream['name']
                        logger.info(f"Search: Final fix - Added lightning icon to cached stream name: {stream['name']}")
                    elif not stream['cached'] and not stream['name'].startswith('⬇️'):
                        if stream['name'].startswith('⚡'):
                            stream['name'] = stream['name'].replace('⚡', '⬇️')
                        else:
                            stream['name'] = '⬇️ ' + stream['name']
                        logger.info(f"Search: Final fix - Added download icon to non-cached stream name: {stream['name']}")
            
            logger.info("Search: Returning processed results")
            total_time = time.time() - start
            logger.success(f"Search: Request completed in {total_time:.2f} seconds")
            return SearchResponse(streams=streams)
        except Exception as e:
            logger.error(f"Search: Error processing cached results: {e}")
            logger.info("Search: Returning original cached processed results")
            return SearchResponse(streams=cached_result)

    # La fonction media_cache_key est maintenant définie plus haut dans le code

    async def get_search_results(media, config):
        search_results = []
        torrent_service = TorrentService(config, torrent_dao)

        async def perform_search(update_cache=False):
            nonlocal search_results
            search_results = []

            if config["cache"] and not update_cache:
                public_cached_results = search_public(media)
                if public_cached_results:
                    logger.success(
                        f"Search: Found {len(public_cached_results)} public cached results"
                    )
                    public_cached_results = [
                        JackettResult().from_cached_item(torrent, media)
                        for torrent in public_cached_results
                        if len(torrent.get("hash", "")) == 40
                    ]
                    public_cached_results = filter_items(
                        public_cached_results, media, config=config
                    )
                    public_cached_results = await torrent_service.convert_and_process(
                        public_cached_results
                    )
                    search_results.extend(public_cached_results)

            # Prioriser Zilean en premier
            if config["zilean"]:
                zilean_service = ZileanService(config)
                zilean_search_results = zilean_service.search(media)
                if zilean_search_results:
                    logger.success(
                        f"Search: Found {len(zilean_search_results)} results from Zilean"
                    )
                    zilean_search_results = [
                        ZileanResult().from_api_cached_item(torrent, media)
                        for torrent in zilean_search_results
                        if len(getattr(torrent, "info_hash", "")) == 40
                    ]
                    zilean_search_results = filter_items(
                        zilean_search_results, media, config=config
                    )
                    zilean_search_results = await torrent_service.convert_and_process(
                        zilean_search_results
                    )
                    logger.info(
                        f"Search: Zilean final search results: {len(zilean_search_results)}"
                    )
                    search_results = merge_items(search_results, zilean_search_results)

            # Ensuite YggFlix si pas assez de résultats
            if config["yggflix"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                yggflix_service = YggflixService(config)
                yggflix_search_results = yggflix_service.search(media)
                if yggflix_search_results:
                    logger.success(
                        f"Search: Found {len(yggflix_search_results)} results from YggFlix"
                    )
                    yggflix_search_results = filter_items(
                        yggflix_search_results, media, config=config
                    )
                    yggflix_search_results = await torrent_service.convert_and_process(
                        yggflix_search_results
                    )
                    search_results = merge_items(search_results, yggflix_search_results)

            if config["sharewood"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                try:
                    sharewood_service = SharewoodService(config)
                    sharewood_search_results = sharewood_service.search(media)
                    if sharewood_search_results:
                        logger.success(
                            f"Search: Found {len(sharewood_search_results)} results from Sharewood"
                        )
                        sharewood_search_results = filter_items(
                            sharewood_search_results, media, config=config
                        )
                        sharewood_search_results = (
                            await torrent_service.convert_and_process(
                                sharewood_search_results
                            )
                        )
                        search_results = merge_items(search_results, sharewood_search_results)
                except Exception as e:
                    logger.warning(f"Search: Sharewood search failed, skipping: {str(e)}")

            if config["jackett"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                jackett_service = JackettService(config)
                jackett_search_results = jackett_service.search(media)
                logger.success(
                    f"Search: Found {len(jackett_search_results)} results from Jackett"
                )
                filtered_jackett_search_results = filter_items(
                    jackett_search_results, media, config=config
                )
                if filtered_jackett_search_results:
                    torrent_results = await torrent_service.convert_and_process(
                        filtered_jackett_search_results
                    )
                    search_results = merge_items(search_results, torrent_results)

            if update_cache and search_results:
                logger.info(
                    f"Search: Updating cache with {len(search_results)} results"
                )
                try:
                    cache_key = media_cache_key(media)
                    search_results_dict = [item.to_dict() for item in search_results]
                    await redis_cache.set(cache_key, search_results_dict)
                    logger.success("Search: Cache update successful")
                except Exception as e:
                    logger.error(f"Search: Error updating cache: {e}")

        await perform_search()
        return search_results

    async def get_and_filter_results(media, config):
        min_results = int(config.get("minCachedResults", 5))
        cache_key = media_cache_key(media)

        unfiltered_results = await redis_cache.get(cache_key)
        if unfiltered_results is None:
            logger.debug("Search: No results in cache. Performing new search.")
            nocache_results = await get_search_results(media, config)
            nocache_results_dict = [item.to_dict() for item in nocache_results]
            await redis_cache.set(cache_key, nocache_results_dict)
            logger.info(
                f"Search: New search completed, found {len(nocache_results)} results"
            )
            return nocache_results
        else:
            logger.info(
                f"Search: Retrieved {len(unfiltered_results)} results from redis cache"
            )
            unfiltered_results = [
                TorrentItem.from_dict(item) for item in unfiltered_results
            ]

        filtered_results = filter_items(unfiltered_results, media, config=config)

        if len(filtered_results) < min_results:
            logger.info(
                f"Search: Insufficient filtered results ({len(filtered_results)}). Performing new search."
            )
            await redis_cache.delete(cache_key)
            unfiltered_results = await get_search_results(media, config)
            unfiltered_results_dict = [item.to_dict() for item in unfiltered_results]
            await redis_cache.set(cache_key, unfiltered_results_dict)
            filtered_results = filter_items(unfiltered_results, media, config=config)

        logger.success(
            f"Search: Final number of filtered results: {len(filtered_results)}"
        )
        return filtered_results

    raw_search_results = await get_and_filter_results(media, config)
    logger.debug(f"Search: Filtered search results: {len(raw_search_results)}")
    search_results = ResultsPerQualityFilter(config).filter(raw_search_results)
    logger.info(f"Search: Filtered search results per quality: {len(search_results)}")

    async def stream_processing(search_results, media, config) -> list[dict]:
        # Créer deux conteneurs distincts : un pour les services directs et un pour StremThru
        # Cela permettra d'éviter que les mises à jour de disponibilité d'un service n'affectent l'autre
        direct_container = TorrentSmartContainer(search_results, media)
        stremthru_container = TorrentSmartContainer(search_results, media)

        # Dictionnaire pour stocker les tâches par type de service
        direct_tasks = []
        stremthru_tasks = []

        for service in debrid_services: 
            service_name = service.__class__.__name__
            logger.debug(f"Processing service: {service_name}")

            # Check if StremThruDebrid is the service instance
            if isinstance(service, StremThruDebrid):
                logger.debug(f"Creating StremThru task for {service_name}.get_cached_files_async")
                # StremThru uses get_cached_files_async with all items
                task = asyncio.create_task(service.get_cached_files_async(stremthru_container.get_items()))
                stremthru_tasks.append((service_name, task, service))
            else: 
                # Direct services use get_availability_bulk with unavailable hashes
                hashes_to_check = direct_container.get_unaviable_hashes()
                if not hashes_to_check:
                    logger.debug(f"No unavailable hashes to check with direct service {service_name}. Skipping.")
                    continue 
                logger.debug(f"Creating Direct Debrid task for {service_name}.get_availability_bulk with {len(hashes_to_check)} hashes")
                # Pass unavailable hashes and media context
                task = asyncio.create_task(service.get_availability_bulk(hashes_to_check, media))
                direct_tasks.append((service_name, task, service)) 

        # Gather results from all tasks (StremThru and Direct separately)
        all_tasks = direct_tasks + stremthru_tasks
        if not all_tasks:
            logger.debug("No availability check tasks were created.")
            results = []
        else:
            results = await asyncio.gather(*[task for _, task, _ in all_tasks])
            logger.debug(f"Gathered {len(results)} results from availability checks.")

        # Traiter les résultats des services directs
        direct_results = results[:len(direct_tasks)]
        for i, (service_name, _, service_instance) in enumerate(direct_tasks):
            result = direct_results[i]
            logger.debug(f"Processing result from direct service {service_name} (instance type: {type(service_instance).__name__})")
            
            if isinstance(result, dict):
                logger.debug(f"Updating availability from direct service {service_name}")
                direct_container.update_availability(debrid_response=result, debrid_type=type(service_instance), media=media)
            else:
                logger.warning(f"Received unexpected result type ({type(result)}) from direct service {service_name}. Skipping update.")
        
        # Traiter les résultats de StremThru
        stremthru_results = results[len(direct_tasks):]
        for i, (service_name, _, service_instance) in enumerate(stremthru_tasks):
            result = stremthru_results[i]
            logger.debug(f"Processing result from StremThru service {service_name} (instance type: {type(service_instance).__name__})")
            
            if result:
                result_dict, used_store_name = result
                if result_dict:
                    logger.debug(f"Updating availability from StremThru store '{used_store_name}'")
                    # Mettre à jour avec les données fraîches
                    stremthru_container.update_availability_stremthru(cached_files=result_dict, store_name=used_store_name, media=media, redis_cache=redis_cache)
                    # Stocker les données dans Redis pour les futures sessions
                    await stremthru_container.store_stremthru_availability(cached_files=result_dict, store_name=used_store_name, redis_cache=redis_cache)
                else:
                    logger.info(f"No cached files found by StremThru for store '{used_store_name}'. Trying to load from cache.")
                    # Essayer de charger les données depuis le cache Redis
                    cached_data = await stremthru_container.load_stremthru_availability(store_name=used_store_name, redis_cache=redis_cache)
                    if cached_data:
                        logger.info(f"Using cached StremThru availability data for store '{used_store_name}'")
                        stremthru_container.update_availability_stremthru(cached_files=cached_data, store_name=used_store_name, media=media)
                    else:
                        logger.warning(f"Invalid or empty result received from StremThru {service_name}.")
            else:
                logger.warning(f"Invalid or empty result received from StremThru {service_name}.")
        
        # Fusionner les résultats des deux conteneurs
        # Nous utiliserons le conteneur direct comme base, puis nous y ajouterons les résultats de StremThru
        # Créer un nouveau conteneur pour les résultats fusionnés
        torrent_smart_container = TorrentSmartContainer(search_results, media)
        
        # Récupérer les éléments des deux conteneurs
        direct_items = direct_container.get_items()
        stremthru_items = stremthru_container.get_items()
        
        # Créer un dictionnaire pour faciliter la fusion
        merged_items = {}
        
        # Ajouter d'abord les éléments directs
        for item in direct_items:
            if item.info_hash:
                merged_items[item.info_hash] = item
        
        # Ajouter ensuite les éléments StremThru, mais uniquement s'ils ne sont pas déjà présents
        # ou si leur disponibilité commence par "ST:" (ce qui indique qu'ils viennent de StremThru)
        for item in stremthru_items:
            if item.info_hash and (item.info_hash not in merged_items or 
                                  (item.availability and item.availability.startswith("ST:")) or
                                  hasattr(item, 'download_icon')):
                merged_items[item.info_hash] = item
        
        # Mettre à jour le conteneur fusionné avec les éléments fusionnés
        torrent_smart_container._TorrentSmartContainer__itemsDict = merged_items
        
        logger.debug("--- Entering Availability Check Block ---")
        logger.debug("--- Container Items Availability Check (After Updates) ---")
        container_items = torrent_smart_container.get_items()
        if container_items:
            for i, item in enumerate(container_items[:5]):
                filename_log = item.file_name[:60] if item.file_name else "[No Filename]"
                logger.debug(f"Item {i} Hash: {item.info_hash}, Filename: {filename_log}..., Availability: {item.availability}")
            if len(container_items) > 5:
                logger.debug(f"... (logged first 5 out of {len(container_items)} items)")
        else:
            logger.debug("Container is empty after updates.")
        logger.debug("--- End Availability Check ---")

        if config["cache"]:
            logger.info("Search: Caching public container items")
            torrent_smart_container.cache_container_items()

        best_matching_results = torrent_smart_container.get_best_matching()
        best_matching_results = sort_items(best_matching_results, config)
        logger.info(f"Search: Found {len(best_matching_results)} best matching results")

        stream_list = parse_to_stremio_streams(best_matching_results, config, media)
        logger.success(f"Search: Processed {len(stream_list)} streams for Stremio")

        return stream_list

    stream_list = await stream_processing(search_results, media, config)
    
    # S'assurer que les fichiers non mis en cache mais marqués comme persistants sont conservés
    for stream_item in stream_list:
        if not stream_item.get('cached', True) and stream_item.get('non_cached_persistent', False):
            logger.info(f"Search: Including non-cached persistent stream in results: {stream_item.get('name', 'Unknown')}")
    
    streams = [Stream(**stream) for stream in stream_list]
    
    # Mettre en cache les résultats avec une expiration plus longue (7 jours)
    try:
        cache_key = stream_cache_key(media)
        await redis_cache.set(cache_key, streams, expiration=604800)  # 7 jours
        logger.info(f"Search: Cache ({cache_key}) mis à jour avec succès.")
        
        # Supprimer tout drapeau d'invalidation pour ce média
        media_key = media_cache_key(media) if media else None
        if media_key:
            global_update_key = f"stremthru:global_update_needed:{media_key}"
            await redis_cache.delete(global_update_key)
            logger.debug(f"Search: Cleared update flag for media {media_key}")
    except Exception as cache_update_e:
        logger.error(f"Search: Échec de la mise à jour du cache ({stream_cache_key(media)}): {cache_update_e}")
    
    total_time = time.time() - start
    logger.info(f"Search: Request completed in {total_time:.2f} seconds")
    return SearchResponse(streams=streams)
