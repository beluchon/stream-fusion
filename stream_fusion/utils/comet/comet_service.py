import base64
import json
import re
import aiohttp
from typing import List, Union, Optional

from RTN import parse
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings
from stream_fusion.utils.models.movie import Movie
from stream_fusion.utils.models.series import Series
from stream_fusion.utils.torrent.torrent_item import TorrentItem

class CometService:
    def __init__(self):
        self.base_url = settings.comet_url.rstrip('/')
        self.session = aiohttp.ClientSession()

    async def close(self):
        await self.session.close()

    async def search(self, media: Union[Movie, Series]) -> List[TorrentItem]:
        if not settings.comet_enabled:
            return []

        try:
            # On utilise une config vide pour Comet car StreamFusion gérera le débridage
            # Si vous avez besoin de passer une config spécifique (ex: indexers spécifiques), modifiez ici
            comet_config = {}
            config_b64 = base64.b64encode(json.dumps(comet_config).encode()).decode()

            media_type = "movie" if isinstance(media, Movie) else "series"
            
            # Pour les séries, Comet attend souvent id:s:e
            if isinstance(media, Series):
                # Nettoyage des S01E01 pour avoir juste les chiffres
                season = int(media.season.replace('S', ''))
                episode = int(media.episode.replace('E', ''))
                media_id_formatted = f"{media.id}:{season}:{episode}"
            else:
                media_id_formatted = media.id

            url = f"{self.base_url}/{config_b64}/stream/{media_type}/{media_id_formatted}.json"
            
            logger.info(f"Comet: Searching for {media_id_formatted} at {self.base_url}")

            async with self.session.get(url, timeout=15) as response:
                if response.status != 200:
                    logger.warning(f"Comet returned status {response.status}")
                    return []
                
                data = await response.json()
                streams = data.get("streams", [])
                
                logger.debug(f"Comet: Found {len(streams)} streams")
                return self._parse_streams(streams, media)

        except Exception as e:
            logger.error(f"Comet search failed: {str(e)}")
            return []
        finally:
            await self.close()

    def _parse_streams(self, streams: List[dict], media: Union[Movie, Series]) -> List[TorrentItem]:
        torrent_items = []
        
        for stream in streams:
            # On cherche l'infoHash
            info_hash = stream.get("infoHash")
            
            # Si pas d'infoHash, peut-être un lien direct (url) que nous ne gérons pas ici pour le moment
            # sauf s'il contient un magnet
            if not info_hash and "magnet:?" in stream.get("url", ""):
                 # Extraction basique du hash depuis magnet si besoin, ou on utilise le magnet tel quel
                 pass
            
            if not info_hash:
                continue

            # Parsing des infos depuis le titre/name retourné par Comet
            # Format typique Comet: Name="Comet 4k", Title="Title\n💾 10GB..."
            name = stream.get("name", "")
            title_text = stream.get("title", "")
            full_text = f"{name} {title_text}"
            
            # Tentative de parsing de la taille
            size = 0
            size_match = re.search(r'💾\s*([\d\.]+)\s*(GB|MB|GiB|MiB)', title_text)
            if size_match:
                value = float(size_match.group(1))
                unit = size_match.group(2)
                if "GB" in unit or "GiB" in unit:
                    size = int(value * 1024 * 1024 * 1024)
                elif "MB" in unit or "MiB" in unit:
                    size = int(value * 1024 * 1024)

            # Tentative de parsing des seeders
            seeders = 0
            seed_match = re.search(r'👥\s*(\d+)', title_text)
            if seed_match:
                seeders = int(seed_match.group(1))
            
            # Récupération de l'indexeur si présent (parfois noté sous forme [Indexer])
            indexer = "Comet"
            # Si Jackett est utilisé dans Comet, le nom de l'indexeur est souvent dans le titre
            
            # Construction du TorrentItem
            item = TorrentItem(
                raw_title=stream.get("behaviorHints", {}).get("filename", full_text.split('\n')[0]), 
                size=size,
                magnet=f"magnet:?xt=urn:btih:{info_hash}",
                info_hash=info_hash,
                link=f"magnet:?xt=urn:btih:{info_hash}",
                seeders=seeders,
                languages=[], # Difficile à extraire de façon fiable sans parsing complexe
                indexer=indexer,
                privacy="public",
                type=media.type,
                parsed_data=parse(full_text) # RTN fait le gros du travail pour la qualité/résolution
            )
            
            torrent_items.append(item)

        return torrent_items
