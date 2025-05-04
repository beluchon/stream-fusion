import json
import queue
import threading
from typing import List, Dict

from RTN import ParsedData
from stream_fusion.settings import settings
from stream_fusion.utils.models.media import Media
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.string_encoding import encodeb64
from stream_fusion.logging_config import logger

from stream_fusion.utils.parser.parser_utils import (
    detect_french_language,
    extract_release_group,
    filter_by_availability,
    filter_by_direct_torrent,
    get_emoji,
    INSTANTLY_AVAILABLE,
    DOWNLOAD_REQUIRED,
    DIRECT_TORRENT,
)


class StreamParser:
    def __init__(self, config: Dict):
        self.config = config
        self.configb64 = encodeb64(json.dumps(config).replace("=", "%3D"))
        self.logger = logger

    def parse_to_stremio_streams(
        self, torrent_items: List[TorrentItem], media: Media
    ) -> List[Dict]:
        stream_list = []
        threads = []
        thread_results_queue = queue.Queue()

        for torrent_item in torrent_items[: int(self.config["maxResults"])]:
            thread = threading.Thread(
                target=self._parse_to_debrid_stream,
                args=(torrent_item, thread_results_queue, media),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        while not thread_results_queue.empty():
            stream_list.append(thread_results_queue.get())

        if self.config["debrid"]:
            stream_list = sorted(stream_list, key=filter_by_availability)
            stream_list = sorted(stream_list, key=filter_by_direct_torrent)

        return stream_list

    def _parse_to_debrid_stream(
        self, torrent_item: TorrentItem, results: queue.Queue, media: Media
    ) -> None:
        parsed_data: ParsedData = torrent_item.parsed_data
        if not parsed_data.resolution:
            parsed_data.resolution = torrent_item.parsed_data.resolution

        # MODIFICATION: Décider quel nom afficher basé sur la disponibilité
        availability_data = torrent_item.availability if hasattr(torrent_item, 'availability') else None
        if availability_data and isinstance(availability_data, dict):
            first_availability_code = next(iter(availability_data.values()), None)
            if first_availability_code and isinstance(first_availability_code, str):
                if ':' in first_availability_code:
                    name_code = first_availability_code.split(':')[-1]
                else:
                    # Handle case where value might not be in the expected format
                    self.logger.warning(f"Unexpected availability format for {torrent_item.info_hash}: {first_availability_code}. Skipping prefix.")
                    name_code = "??" # Or some default/error indicator
            else:
                # Handle cases where the value isn't a string or is None
                self.logger.debug(f"Availability value for {torrent_item.info_hash} is not a string or is None: {first_availability_code}")
                name_code = "??"
        else:
            name_code = "??" # Default if no availability data

        name = self._create_stream_name(torrent_item, parsed_data)

        # Générer le titre complet (maintenant corrigé pour utiliser le dict)
        title = self._create_stream_title(torrent_item, parsed_data, media)

        queryb64 = encodeb64(
            json.dumps(torrent_item.to_debrid_stream_query(media))
        ).replace("=", "%3D")

        # Déterminer l'URL de lecture en fonction de la disponibilité
        playback_url = f"{self.config['addonHost']}/playback/"
        if availability_data and isinstance(availability_data, dict):
            first_availability_code = next(iter(availability_data.values()), None)
            if first_availability_code and isinstance(first_availability_code, str):
                if first_availability_code.startswith("ST:"):
                    store_code = first_availability_code.split(':')[1] # Extraire le code du store (ex: AD)
                    query_dict = torrent_item.to_debrid_stream_query(media)

                    # Find the file_index associated with the specific StremThru code in availability_data
                    stremthru_file_index = None
                    for index, code in availability_data.items():
                        if code == first_availability_code:
                            stremthru_file_index = index
                            break

                    # Use key 'index' as expected by get_stream_link
                    # Pass the original index received from availability_data (-1 if unknown)
                    query_dict['index'] = stremthru_file_index

                    # Encode without stripping padding
                    query_b64_stremthru = encodeb64(json.dumps(query_dict))

                    playback_url += f"stremthru/{store_code}/{self.configb64}/{query_b64_stremthru}"
                    self.logger.debug(f"Generating Stremthru playback URL for store {store_code} with index {stremthru_file_index}")
                else:
                    # Utiliser le chemin de lecture direct classique
                    # Le handler /playback/{config}/{query} devra déterminer le service basé sur le code 'availability'
                    # Ou on pourrait rendre l'URL plus explicite ici si nécessaire, mais gardons-le simple pour l'instant
                    playback_url += f"{self.configb64}/{queryb64}"
                    self.logger.debug(f"Generating direct playback URL for availability {first_availability_code}")
        else:
            playback_url += f"{self.configb64}/{queryb64}"

        results.put(
            {
                "name": name,
                "description": title,
                "url": playback_url, # Utiliser l'URL déterminée
                "behaviorHints": {
                    "bingeGroup": f"stream-fusion-{torrent_item.info_hash}",
                    "filename": torrent_item.file_name or torrent_item.raw_title,
                },
            }
        )

        if self.config["torrenting"] and torrent_item.privacy == "public":
            self._add_direct_torrent_stream(torrent_item, parsed_data, title, results)

    def _create_stream_name(
        self, torrent_item: TorrentItem, parsed_data: ParsedData
    ) -> str:
        resolution = parsed_data.resolution or "Unknown"
        # For cached streams, show only service code; else show file title
        avail = torrent_item.availability
        self.logger.debug(f"_create_stream_name: availability data: {avail} (type: {type(avail)})")
        
        # Gérer les cas où availability est un dictionnaire (nouveau format)
        if isinstance(avail, dict) and avail:
            # Prendre le premier code de disponibilité du dictionnaire
            first_code = next(iter(avail.values()), None)
            self.logger.debug(f"_create_stream_name: first availability code: {first_code}")
            
            if first_code and isinstance(first_code, str):
                if first_code.startswith("ST:"):
                    # Extraire le code de store (rd, ad, etc.)
                    store_code = first_code.split(":")[1] if len(first_code.split(":")) > 1 else "?"
                    name = f"{INSTANTLY_AVAILABLE}ST:{store_code}+\n({resolution})"
                    self.logger.debug(f"_create_stream_name: using StremThru code: {store_code}")
                    return name
                elif first_code in ["AD", "RD", "TB", "PM"]:
                    name = f"{INSTANTLY_AVAILABLE}{first_code}+\n({resolution})"
                    self.logger.debug(f"_create_stream_name: using direct debrid code: {first_code}")
                    return name
        
        # Gérer les cas où availability est une chaîne (ancien format)
        elif isinstance(avail, str) and avail.strip():
            self.logger.info(f"_create_stream_name: Processing string availability: '{avail}'")
            # Forcer l'affichage de l'icône INSTANTLY_AVAILABLE pour tout code commençant par ST:
            if "ST:" in avail:
                store_code = avail.split(":")[1] if ":" in avail and len(avail.split(":")) > 1 else "?"
                name = f"{INSTANTLY_AVAILABLE}ST:{store_code}+\n({resolution})"
                self.logger.info(f"_create_stream_name: FORCING StremThru instantly available icon for: {avail}")
                return name
            # Codes directs des services de debrid
            elif avail in ["AD", "RD", "TB", "PM"]:
                name = f"{INSTANTLY_AVAILABLE}{avail}+\n({resolution})"
                self.logger.info(f"_create_stream_name: using direct debrid string code: {avail}")
                return name
        
        # Par défaut: non mis en cache
        label = torrent_item.file_name or torrent_item.raw_title
        service = self.config.get('debridDownloader', settings.download_service)
        name = f"{DOWNLOAD_REQUIRED}{label}\n{service}\n({resolution})"
        self.logger.debug(f"_create_stream_name: using download required format")
        return name
        return name

    def _create_stream_title(
        self, torrent_item: TorrentItem, parsed_data: ParsedData, media: Media
    ) -> str:
        """Crée le titre complet du stream affiché dans Stremio."""
        # Composants de base du titre
        quality = f"{parsed_data.quality} " if parsed_data.quality else ""
        langs = f"({'/'.join(get_emoji(lang) for lang in torrent_item.languages)}) " if torrent_item.languages else ""
        source = f"{torrent_item.indexer} "
        size_in_gb = round(int(torrent_item.size) / 1024 / 1024 / 1024, 2)
        size = f"{size_in_gb:.2f} GB"

        # --- Log pour débogage --- 
        self.logger.info(f"_create_stream_title: Processing item {torrent_item.raw_title[:50]}...")
        self.logger.info(f"_create_stream_title: Availability data: {torrent_item.availability} (Type: {type(torrent_item.availability)})")
        # -------------------------

        # --- MODIFICATION: Ajout du préfixe de disponibilité --- 
        availability_prefix = ""
        availability_data = torrent_item.availability if hasattr(torrent_item, 'availability') else None
        if availability_data and isinstance(availability_data, dict):
            first_availability_code = next(iter(availability_data.values()), None)
            if first_availability_code and isinstance(first_availability_code, str):
                if first_availability_code.startswith("ST:"):
                    availability_prefix = f"[ST:{first_availability_code.split(':')[1]}+] "
                else:
                    availability_prefix = f"[{first_availability_code}+] "
        # -----------------------------------------------------

        # --- MODIFICATION: Inclure le préfixe --- 
        title = f"{availability_prefix}{quality}{langs}\n{source}{size}"

        # --- Log pour débogage --- 
        self.logger.info(f"_create_stream_title: Generated title prefix: '{availability_prefix}'")
        self.logger.info(f"_create_stream_title: Final title generated (before season/episode/filename): '{title.replace('\n', ' ')}'")
        # -------------------------

        # --- Always include the file name ---
        if torrent_item.file_name:
            title += f"\n{torrent_item.file_name}"

        title += self._add_language_info(torrent_item, parsed_data)
        title += self._add_torrent_info(torrent_item)
        title += self._add_media_info(parsed_data)

        return title.strip()

    def _add_language_info(
        self, torrent_item: TorrentItem, parsed_data: ParsedData
    ) -> str:
        info = (
            "/".join(get_emoji(lang) for lang in torrent_item.languages)
            if torrent_item.languages
            else "🌐"
        )

        lang_type = detect_french_language(torrent_item.raw_title)
        if lang_type:
            info += f"  ✔ {lang_type} "

        group = extract_release_group(torrent_item.raw_title) or parsed_data.group
        if group:
            info += f"  ☠️ {group}"

        return f"{info}\n"

    def _add_torrent_info(self, torrent_item: TorrentItem) -> str:
        size_in_gb = round(int(torrent_item.size) / 1024 / 1024 / 1024, 2)
        return f"🔍 {torrent_item.indexer} 💾 {size_in_gb}GB 👥 {torrent_item.seeders} \n"

    def _add_media_info(self, parsed_data: ParsedData) -> str:
        info = []
        if parsed_data.codec:
            info.append(f"🎥 {parsed_data.codec}")
        if parsed_data.quality:
            info.append(f"📺 {parsed_data.quality}")
        if parsed_data.audio:
            info.append(f"🎧 {' '.join(parsed_data.audio)}")
        return " ".join(info) + "\n" if info else ""

    def _add_direct_torrent_stream(
        self,
        torrent_item: TorrentItem,
        parsed_data: ParsedData,
        title: str,
        results: queue.Queue,
    ) -> None:
        direct_torrent_name = f"{DIRECT_TORRENT}\n{parsed_data.quality}\n"
        if parsed_data.quality and parsed_data.quality[0] not in ["Unknown", ""]:
            direct_torrent_name += f"({'|'.join(parsed_data.quality)})"

        results.put(
            {
                "name": direct_torrent_name,
                "description": title,
                "infoHash": torrent_item.info_hash,
                "fileIdx": (
                    int(torrent_item.file_index) if torrent_item.file_index else None
                ),
                "behaviorHints": {
                    "bingeGroup": f"stream-fusion-{torrent_item.info_hash}",
                    "filename": torrent_item.file_name or torrent_item.raw_title,
                },
            }
        )
