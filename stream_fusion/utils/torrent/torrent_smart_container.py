import os
import threading

from typing import List, Dict
from RTN import parse

from stream_fusion.utils.debrid.alldebrid import AllDebrid
from stream_fusion.utils.debrid.premiumize import Premiumize
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.cache.cache import cache_public
from stream_fusion.utils.general import season_episode_in_filename
from stream_fusion.logging_config import logger


class TorrentSmartContainer:
    def __init__(self, torrent_items: List[TorrentItem], media):
        self.logger = logger
        self.logger.info(
            f"Initializing TorrentSmartContainer with {len(torrent_items)} items"
        )
        self.__itemsDict: Dict[TorrentItem] = self._build_items_dict_by_infohash(
            torrent_items
        )
        self.__media = media

    def get_unaviable_hashes(self):
        return [item.info_hash for item in self.get_items() if item.availability == ""]

    def get_unavailable_magnets(self):
        """Retourne les liens magnet pour les items qui n'ont pas encore de disponibilité marquée."""
        return [item.magnet for item in self.get_items() if item.availability == "" and item.magnet]

    def get_items(self):
        items = list(self.__itemsDict.values())
        self.logger.debug(f"TorrentSmartContainer: Retrieved {len(items)} items")
        return items

    def get_direct_torrentable(self):
        self.logger.info("TorrentSmartContainer: Retrieving direct torrentable items")
        direct_torrentable_items = []
        for torrent_item in self.__itemsDict.values():
            if torrent_item.privacy == "public" and torrent_item.file_index is not None:
                direct_torrentable_items.append(torrent_item)
        self.logger.info(
            f"TorrentSmartContainer: Found {len(direct_torrentable_items)} direct torrentable items"
        )
        return direct_torrentable_items

    def get_best_matching(self):
        self.logger.info("TorrentSmartContainer: Finding best matching items")
        best_matching = []
        self.logger.debug(
            f"TorrentSmartContainer: Total items to process: {len(self.__itemsDict)}"
        )
        for torrent_item in self.__itemsDict.values():
            # Vérifier d'abord si l'item est marqué comme always_show
            always_show = getattr(torrent_item, 'always_show', False)
            
            self.logger.debug(
                f"TorrentSmartContainer: Processing item: {torrent_item.raw_title} - Has torrent: {torrent_item.torrent_download is not None}, always_show: {always_show}"
            )
            
            # Si l'item est marqué comme always_show, l'ajouter directement
            if always_show and torrent_item.file_index is not None:
                best_matching.append(torrent_item)
                self.logger.info(
                    f"TorrentSmartContainer: Item added to best matching (always_show=True): {torrent_item.raw_title}"
                )
                continue
            
            # Sinon, utiliser la logique habituelle
            if torrent_item.torrent_download is not None:
                self.logger.trace(
                    f"TorrentSmartContainer: Has file index: {torrent_item.file_index is not None}"
                )
                if torrent_item.file_index is not None:
                    best_matching.append(torrent_item)
                    self.logger.trace(
                        "TorrentSmartContainer: Item added to best matching (has file index)"
                    )
                else:
                    matching_file = self._find_matching_file(
                        torrent_item.full_index,
                        self.__media.season,
                        self.__media.episode,
                    )
                    if matching_file:
                        torrent_item.file_index = matching_file["file_index"]
                        torrent_item.file_name = matching_file["file_name"]
                        torrent_item.size = matching_file["size"]
                        best_matching.append(torrent_item)
                        self.logger.trace(
                            f"TorrentSmartContainer: Item added to best matching (found matching file: {matching_file['file_name']})"
                        )
                    else:
                        self.logger.trace(
                            "TorrentSmartContainer: No matching file found, item not added to best matching"
                        )
            else:
                if not (( not torrent_item.availability or torrent_item.availability == "DL" ) and torrent_item.indexer == "DMM - API"):
                    best_matching.append(torrent_item)
                    self.logger.trace(
                        "TorrentSmartContainer: Item added to best matching (magnet link)"
                    )
        
        # Compter et logger les items non mis en cache
        non_cached_count = sum(1 for item in best_matching if hasattr(item, 'cached') and not item.cached)
        self.logger.success(
            f"TorrentSmartContainer: Found {len(best_matching)} best matching items ({non_cached_count} non-cached)"
        )
        return best_matching

    def _find_matching_file(self, full_index, season, episode):
        self.logger.trace(
            f"TorrentSmartContainer: Searching for matching file: Season {season}, Episode {episode}"
        )

        if not full_index:
            self.logger.trace(
                "TorrentSmartContainer: Full index is empty, cannot find matching file"
            )
            return None
        try:
            target_season = int(season.replace("S", ""))
            target_episode = int(episode.replace("E", ""))
        except ValueError:
            self.logger.error(
                f"TorrentSmartContainer: Invalid season or episode format: {season}, {episode}"
            )
            return None

        best_match = None
        for file_entry in full_index:
            if (
                target_season in file_entry["seasons"]
                and target_episode in file_entry["episodes"]
            ):
                if best_match is None or file_entry["size"] > best_match["size"]:
                    best_match = file_entry
                    self.logger.trace(
                        f"TorrentSmartContainer: Found potential match: {file_entry['file_name']}"
                    )

        if best_match:
            self.logger.trace(
                f"TorrentSmartContainer: Best matching file found: {best_match['file_name']}"
            )
            return best_match
        else:
            self.logger.warning(
                f"TorrentSmartContainer: No matching file found for Season {season}, Episode {episode}"
            )
            return None

    def cache_container_items(self):
        self.logger.info(
            "TorrentSmartContainer: Starting cache process for container items"
        )
        threading.Thread(target=self._save_to_cache).start()

    def _save_to_cache(self):
        self.logger.info("TorrentSmartContainer: Saving public items to cache")
        public_torrents = list(
            filter(lambda x: x.privacy == "public", self.get_items())
        )
        self.logger.debug(
            f"TorrentSmartContainer: Found {len(public_torrents)} public torrents to cache"
        )
        cache_public(public_torrents, self.__media)
        self.logger.info("TorrentSmartContainer: Caching process completed")

    def update_availability(self, debrid_response, debrid_type, media):
        if not debrid_response or debrid_response == {} or debrid_response == []:
            self.logger.debug(
                "TorrentSmartContainer: Debrid response is empty : "
                + str(debrid_response)
            )
            return
        self.logger.info(
            f"TorrentSmartContainer: Updating availability for {debrid_type.__name__}"
        )
        if debrid_type is RealDebrid:
            self._update_availability_realdebrid(debrid_response, media)
        elif debrid_type is AllDebrid:
            self._update_availability_alldebrid(debrid_response, media)
        elif debrid_type is Torbox:
            self._update_availability_torbox(debrid_response, media)
        elif debrid_type is Premiumize:
            self._update_availability_premiumize(debrid_response)
        else:
            self.logger.error(
                f"TorrentSmartContainer: Unsupported debrid type: {debrid_type.__name__}"
            )
            raise NotImplementedError(
                f"TorrentSmartContainer: Debrid type {debrid_type.__name__} not implemented"
            )

    def _update_availability_realdebrid(self, response, media):
        self.logger.info("TorrentSmartContainer: Updating availability for RealDebrid")
        for info_hash, details in response.items():
            if "rd" not in details:
                self.logger.debug(
                    f"TorrentSmartContainer: Skipping hash {info_hash}: no RealDebrid data"
                )
                continue
            torrent_item: TorrentItem = self.__itemsDict[info_hash]
            self.logger.debug(
                f"Processing {torrent_item.type}: {torrent_item.raw_title}"
            )
            files = []
            if torrent_item.type == "series":
                self._process_series_files(
                    details, media, torrent_item, files, debrid="RD"
                )
            else:
                self._process_movie_files(details, files)
            self._update_file_details(torrent_item, files, debrid="RD")
        self.logger.info(
            "TorrentSmartContainer: RealDebrid availability update completed"
        )

    def _process_series_files(
        self, details, media, torrent_item, files, debrid: str = "??"
    ):
        for variants in details["rd"]:
            file_found = False
            for file_index, file in variants.items():
                clean_season = media.season.replace("S", "")
                clean_episode = media.episode.replace("E", "")
                numeric_season = int(clean_season)
                numeric_episode = int(clean_episode)
                if season_episode_in_filename(
                    file["filename"], numeric_season, numeric_episode
                ):
                    self.logger.debug(f"Matching file found: {file['filename']}")
                    torrent_item.file_index = file_index
                    torrent_item.file_name = file["filename"]
                    torrent_item.size = file["filesize"]
                    torrent_item.availability = debrid
                    file_found = True
                    files.append(
                        {
                            "file_index": file_index,
                            "title": file["filename"],
                            "size": file["filesize"],
                        }
                    )
                    break
            if file_found:
                break

    def _process_movie_files(self, details, files):
        for variants in details["rd"]:
            for file_index, file in variants.items():
                self.logger.debug(
                    f"TorrentSmartContainer: Adding movie file: {file['filename']}"
                )
                files.append(
                    {
                        "file_index": file_index,
                        "title": file["filename"],
                        "size": file["filesize"],
                    }
                )

    def _update_availability_alldebrid(self, response, media):
        self.logger.info("TorrentSmartContainer: Updating availability for AllDebrid")
        if not response["status"] == "success":
            self.logger.error(f"TorrentSmartContainer: AllDebrid API error: {response}")
            return

        for data in response["data"]["magnets"]:
            torrent_item: TorrentItem = self.__itemsDict[data["hash"]]
            
            # Marquer TOUS les fichiers AllDebrid comme disponibles dès que le hash est trouvé
            torrent_item.availability = "AD"
            
            # Mettre à jour les détails du fichier si possible
            if "files" in data and data["files"]:
                files = []
                self._explore_folders_alldebrid(
                    data["files"], files, 1, torrent_item.type, media
                )
                if files:
                    self._update_file_details(torrent_item, files, debrid="AD")
                else:
                    self.logger.debug(
                        f"No matching AD files for hash {data['hash']}; skipping file detail update."
                    )
            else:
                self.logger.debug(
                    f"No files data for hash {data['hash']}; skipping file detail update."
                )
                
        self.logger.info(
            "TorrentSmartContainer: AllDebrid availability update completed"
        )

    def _update_availability_torbox(self, response, media):
        if response["success"] is False:
            self.logger.error(f"TorrentSmartContainer: Torbox API error: {response}")
            return

        # Créer une copie des torrents pour simuler des torrents non disponibles
        all_items = self.get_items()
        tb_hashes = set()
        
        # D'abord, traiter les torrents retournés par l'API TorBox
        for data in response["data"]:
            hash_value = data.get("hash", "")
            tb_hashes.add(hash_value.lower())
            
            if hash_value not in self.__itemsDict:
                continue
                
            torrent_item: TorrentItem = self.__itemsDict[hash_value]
            
            # Vérifier si le torrent est mis en cache ou non
            cached = data.get("cached", False)
            if cached:
                torrent_item.availability = "TB+"
            else:
                torrent_item.availability = "TB-"
            
            # Mettre à jour les détails du fichier
            files = self._process_torbox_files(data["files"], torrent_item.type, media)
            self._update_file_details(torrent_item, files, debrid="TB")
        
        # Ensuite, marquer comme non disponibles tous les torrents qui n'ont pas été retournés par l'API TorBox
        for item in all_items:
            if item.info_hash and item.info_hash.lower() not in tb_hashes:
                # Marquer comme non disponible, sera affiché avec la flèche bleue
                item.availability = None

    def _process_torbox_files(self, files, type, media):
        processed_files = []
        for index, file in enumerate(files):
            if type == "series":
                if self._is_matching_episode_torbox(file["name"], media):
                    processed_files.append(
                        {
                            "file_index": index,
                            "title": file["name"],
                            "size": file["size"],
                        }
                    )
            elif type == "movie":
                processed_files.append(
                    {
                        "file_index": index,
                        "title": file["name"],
                        "size": file["size"],
                    }
                )
        return processed_files

    def _is_matching_episode_torbox(self, filepath, media):
            # Extract only the filename from the full path
            filename = os.path.basename(filepath)
            
            clean_season = media.season.replace("S", "")
            clean_episode = media.episode.replace("E", "")
            numeric_season = int(clean_season)
            numeric_episode = int(clean_episode)
            
            return season_episode_in_filename(filename, numeric_season, numeric_episode)

    def _update_availability_premiumize(self, response):
        self.logger.info("TorrentSmartContainer: Updating availability for Premiumize")
        if not response:
            self.logger.error(
                f"TorrentSmartContainer: Empty response from Premiumize API"
            )
            return

        torrent_items = self.get_items()
        for hash, status in response.items():
            for item in torrent_items:
                if item.info_hash.lower() == hash.lower():
                    is_available = status.get("transcoded", False)
                    # On définit availability à "PM" pour tous les torrents trouvés dans la réponse Premiumize
                    item.availability = "PM"
                    # Et on utilise pm_cached pour indiquer si le fichier est réellement en cache
                    item.pm_cached = is_available
                    
                    # Mettre à jour les détails du fichier SEULEMENT si PM rapporte qu'il est transcodé
                    if is_available:
                        if item.type == "series":
                            # Pour les séries, vérifier si le fichier sélectionné correspond à l'épisode
                            if "full_index" in item.__dict__ and item.full_index:
                                # Si nous avons l'index complet des fichiers, l'utiliser
                                matching_files = []
                                for file_info in item.full_index:
                                    # Utiliser les attributs de l'item plutôt que self.__media
                                    clean_season = item.season.replace("S", "") if hasattr(item, 'season') and item.season else "0"
                                    clean_episode = item.episode.replace("E", "") if hasattr(item, 'episode') and item.episode else "0"
                                    numeric_season = int(clean_season)
                                    numeric_episode = int(clean_episode)
                                    
                                    if (numeric_season in file_info.get("seasons", []) and 
                                        numeric_episode in file_info.get("episodes", [])):
                                        matching_files.append(file_info)
                                
                                if matching_files:
                                    # Prendre le plus gros fichier parmi ceux qui correspondent
                                    best_match = max(matching_files, key=lambda x: x.get("size", 0))
                                    file_info = {
                                        "file_index": best_match.get("file_index", 0),
                                        "title": best_match.get("file_name", ""),
                                        "size": best_match.get("size", 0)
                                    }
                                    self._update_file_details(item, [file_info], debrid="PM")
                                    self.logger.debug(
                                        f"TorrentSmartContainer: Updated series file details from full_index for {item.raw_title}: {file_info}"
                                    )
                                else:
                                    # Si aucun fichier ne correspond dans l'index, garder quand même le torrent
                                    self.logger.debug(
                                        f"TorrentSmartContainer: No matching file found in full_index for {item.raw_title}, keeping torrent"
                                    )
                                    file_info = {
                                        "file_index": 0,
                                        "title": status.get("filename", item.raw_title),
                                        "size": int(status.get("filesize", 0))
                                    }
                                    self._update_file_details(item, [file_info], debrid="PM")
                            else:
                                # Si pas d'index complet, garder le torrent
                                file_info = {
                                    "file_index": 0,
                                    "title": status.get("filename", item.raw_title),
                                    "size": int(status.get("filesize", 0))
                                }
                                self._update_file_details(item, [file_info], debrid="PM")
                                self.logger.debug(
                                    f"TorrentSmartContainer: No full_index available for {item.raw_title}, keeping torrent"
                                )
                        else:
                            # Pour les films, utiliser les informations de base
                            file_info = {
                                "file_index": 0,
                                "title": status.get("filename") or item.raw_title,
                                "size": int(status.get("filesize", 0))
                            }
                            self._update_file_details(item, [file_info], debrid="PM")
                            self.logger.debug(
                                f"TorrentSmartContainer: Updated movie file details for {item.raw_title}: {file_info}"
                            )
                    
                    self.logger.debug(
                        f"TorrentSmartContainer: Updated availability for {item.raw_title}: {item.availability}"
                    )

        self.logger.info(
            f"TorrentSmartContainer: Premiumize availability update completed. {len([item for item in torrent_items if item.availability == 'PM'])}/{len(torrent_items)} items marked as instant."
        )

    async def store_stremthru_availability(self, cached_files, store_name, redis_cache=None):
        """
        Stocke les informations de disponibilité StremThru dans Redis pour une utilisation future.
        Cela permet de conserver les informations de disponibilité entre les sessions.
        """
        if not redis_cache:
            self.logger.debug("store_stremthru_availability: No Redis cache provided, skipping storage.")
            return
            
        try:
            # Créer une clé unique pour ce store StremThru
            cache_key = f"stremthru:availability:{store_name}"
            
            # Stocker les données dans Redis avec une expiration de 24 heures
            await redis_cache.set(cache_key, cached_files, expiration=86400)  # 24 heures
            
            self.logger.info(f"Stored StremThru availability data for store '{store_name}' in Redis cache.")
        except Exception as e:
            self.logger.error(f"Failed to store StremThru availability in Redis: {e}")
    
    async def load_stremthru_availability(self, store_name, redis_cache=None):
        """
        Charge les informations de disponibilité StremThru depuis Redis.
        Retourne les données si disponibles, sinon None.
        """
        if not redis_cache:
            self.logger.debug("load_stremthru_availability: No Redis cache provided, skipping load.")
            return None
            
        try:
            # Récupérer la clé pour ce store StremThru
            cache_key = f"stremthru:availability:{store_name}"
            
            # Charger les données depuis Redis
            cached_data = await redis_cache.get(cache_key)
            
            if cached_data:
                self.logger.info(f"Loaded StremThru availability data for store '{store_name}' from Redis cache.")
                return cached_data
            else:
                self.logger.debug(f"No cached StremThru availability data found for store '{store_name}'.")
                return None
        except Exception as e:
            self.logger.error(f"Failed to load StremThru availability from Redis: {e}")
            return None
            
    async def check_working_stremthru_hashes(self, store_code, redis_cache=None):
        """
        Vérifie les hashes marqués comme fonctionnels pour un store_code StremThru spécifique.
        Retourne un dictionnaire {hash: [file_info]} pour les hashes fonctionnels.
        """
        if not redis_cache:
            self.logger.debug("check_working_stremthru_hashes: No Redis cache provided, skipping check.")
            return {}
            
        try:
            # Récupérer tous les items du container
            items = self.get_items()
            result = {}
            
            # Vérifier chaque hash
            for item in items:
                info_hash = item.info_hash.lower()
                working_hash_key = f"stremthru:working:{store_code}:{info_hash}"
                
                # Vérifier si ce hash est marqué comme fonctionnel
                is_working = await redis_cache.get(working_hash_key)
                
                if is_working:
                    self.logger.debug(f"Found working StremThru hash: {info_hash} for store_code {store_code}")
                    # Créer une entrée dans le résultat avec un fichier factice
                    result[info_hash] = [{
                        'file_index': 0,  # Index par défaut
                        'title': item.file_name or item.raw_title,
                        'size': item.size
                    }]
            
            self.logger.info(f"Found {len(result)} working StremThru hashes for store_code {store_code}")
            return result
        except Exception as e:
            self.logger.error(f"Error checking working StremThru hashes: {e}")
            return {}
    
    def update_availability_stremthru(self, cached_files, store_name, media, redis_cache=None):
        """
        Met à jour la disponibilité des items basée sur les fichiers retournés par StremThru (via get_cached_files).
        'cached_files' est maintenant un dictionnaire {info_hash: [file_dict, ...], ...}
        'store_name' est le nom du store interne StremThru (ex: 'alldebrid', 'realdebrid').
        
        Si redis_cache est fourni, les informations de disponibilité seront également stockées pour une utilisation future.
        """
        if not cached_files or not isinstance(cached_files, dict):
            self.logger.debug(f"update_availability_stremthru: No cached files provided or not a dict: {type(cached_files)}")
            return

        # --- Calculer le nombre total de fichiers pour le log --- 
        total_files_count = sum(len(files) for files in cached_files.values())
        self.logger.info(f"TorrentSmartContainer: Updating availability from Stremthru for store '{store_name}' ({len(cached_files)} hashes, {total_files_count} files total)")

        # Générer le code court pour la disponibilité
        availability_code = store_name[:2].upper() if store_name else "ST" # Utiliser 'ST' si store_name est None/vide
        if store_name == "alldebrid": availability_code = "AD"
        elif store_name == "easydebrid": availability_code = "ED"
        elif store_name == "realdebrid": availability_code = "RD"
        elif store_name == "premiumize": availability_code = "PM"
        elif store_name == "debridlink": availability_code = "DL"
        elif store_name == "pikpak": availability_code = "PK"
        elif store_name == "offcloud": availability_code = "OC"
        elif store_name == "torbox": availability_code = "TB"
        # Ajouter d'autres si besoin

        # IMPORTANT: Préfixer le code pour indiquer la gestion par Stremthru
        stremthru_availability_code = f"ST:{availability_code}"

        self.logger.info(f"Using availability code '{stremthru_availability_code}' for store '{store_name}' from Stremthru.")

        # Log the raw data received from StremThru
        self.logger.debug(f"TorrentSmartContainer: Received raw cached_files data from Stremthru for store '{store_name}': {cached_files}")

        updated_hashes = set() # Garder trace des hashes uniques mis à jour
        updated_hashes_count = 0
        skipped_non_matching = 0
        non_cached_files_count = 0

        # Vérifier si des liens ont été marqués comme fonctionnels dans Redis
        store_code = None
        for code, name in getattr(StremThruDebrid, 'STORE_CODE_TO_NAME', {}).items():
            if name == store_name:
                store_code = code.upper()
                break

        # Récupérer les hashes marqués comme fonctionnels dans Redis
        working_hashes = set()
        if redis_cache and store_code:
            try:
                # Utiliser une méthode synchrone pour vérifier les clés Redis
                # Comme cette fonction n'est pas async, on ne peut pas utiliser await
                # On peut soit convertir la fonction en async, soit utiliser une approche différente
                # Pour l'instant, on va juste logger que cette fonctionnalité nécessite une fonction async
                self.logger.info(f"Checking Redis for working hashes requires async function. Skipping this check.")
                # Dans une version future, on pourrait convertir cette fonction en async
                # ou créer une fonction auxiliaire async pour cette vérification
            except Exception as e:
                self.logger.error(f"Error checking Redis for working hashes: {e}")
                # Continuer sans les hashes Redis

        # --- Itérer sur les valeurs (listes de fichiers), puis les fichiers --- 
        for info_hash_key, files_list in cached_files.items(): # Itérer sur les paires clé(hash)/valeur(liste de fichiers)
            item = self.__itemsDict.get(info_hash_key) # Récupérer l'item TorrentItem correspondant au hash
            if not item:
                # Ce cas ne devrait pas arriver si get_cached_files a bien utilisé les hashes du container
                self.logger.warning(f"update_availability_stremthru: Infohash {info_hash_key} from StremThru response not found in container.")
                continue

            # Vérifier si ce hash est marqué comme fonctionnel dans Redis
            is_working_hash = info_hash_key in working_hashes

            # Marquer que ce hash a été mis à jour (au moins un fichier trouvé)
            if info_hash_key not in updated_hashes:
                updated_hashes_count += 1
                updated_hashes.add(info_hash_key)

            # Traiter chaque fichier trouvé pour ce hash
            for file_info in files_list: # Maintenant, file_info est bien un dictionnaire
                # Validation basique de file_info
                if not isinstance(file_info, dict):
                    self.logger.warning(f"update_availability_stremthru: Expected dict for file_info, got {type(file_info)} for hash {info_hash_key}. Skipping this file.")
                    continue

                file_index = file_info.get("file_index")
                file_title = file_info.get("title")
                file_size = file_info.get("size")
                is_cached = file_info.get("cached", True)  # Par défaut, on considère que c'est en cache
                always_show = file_info.get("always_show", False)  # Nouvel attribut pour forcer l'affichage

                # Pour les fichiers non mis en cache, on les compte séparément
                if not is_cached:
                    non_cached_files_count += 1
                    self.logger.debug(f"Found non-cached file for hash {info_hash_key}: {file_title}, always_show: {always_show}")

                # Vérifier si le fichier a un index valide
                if file_index is None:
                    self.logger.debug(f"update_availability_stremthru: Skipping file with None index for hash {info_hash_key}.")
                    skipped_non_matching += 1
                    continue
                
                # Si file_index est -1 mais qu'un titre est fourni, on l'accepte quand même
                if file_index < 0 and not file_title:
                    self.logger.debug(f"update_availability_stremthru: Skipping file with negative index and no title for hash {info_hash_key}.")
                    skipped_non_matching += 1
                    continue

                # Tous les fichiers sont considérés comme disponibles, qu'ils soient en cache ou non
                # Les fichiers non mis en cache ont déjà une indication visuelle dans leur nom
                self.logger.debug(f"File for hash {info_hash_key} will be shown (cached: {is_cached})")

                # Update first valid file_info using base method
                if item:
                    # Mettre à jour les détails du fichier
                    self._update_file_details(
                        item,
                        [{'file_index': file_index, 'title': file_title, 'size': file_size}],
                        debrid=availability_code
                    )
                    
                    # Mettre à jour les attributs de l'item
                    item.cached = is_cached
                    
                    # Ajouter l'attribut download_icon pour les fichiers non mis en cache
                    if not is_cached:
                        item.download_icon = True
                    else:
                        item.download_icon = False
                    
                    # Le nom du fichier a déjà été préfixé avec [NON-CACHED] si nécessaire
                    # dans la fonction get_cached_files
                    
                    self.logger.debug(f"StremThru: Updated file details for hash {info_hash_key}, file_index {file_index}, title '{file_title}', cached: {is_cached}, availability '{availability_code}'")
                    # Only use the first matching file
                    break

        # Log final
        self.logger.info(f"TorrentSmartContainer: Availability update from Stremthru completed. {updated_hashes_count} items updated ({non_cached_files_count} non-cached). Skipped {skipped_non_matching} files due to missing index.")
        
        # Si des hashes ont été marqués comme fonctionnels dans Redis, les ajouter au log
        if working_hashes:
            self.logger.info(f"TorrentSmartContainer: Found {len(working_hashes)} hashes marked as working in Redis for store {store_code}")


    def _update_file_details(self, torrent_item, files, debrid: str = "??"):
        if not files:
            self.logger.debug(
                f"TorrentSmartContainer: No files to update for {torrent_item.raw_title}"
            )
            return
        file = max(files, key=lambda file: file["size"])
        torrent_item.availability = debrid
        torrent_item.file_index = file["file_index"]
        torrent_item.file_name = file["title"]
        torrent_item.size = file["size"]
        self.logger.debug(
            f"TorrentSmartContainer: Updated file details for {torrent_item.raw_title}: {file['title']}"
        )

    def _build_items_dict_by_infohash(self, items: List[TorrentItem]):
        self.logger.info(
            f"TorrentSmartContainer: Building items dictionary by infohash ({len(items)} items)"
        )
        items_dict = {}
        for item in items:
            if item.info_hash is not None:
                if item.info_hash not in items_dict:
                    self.logger.debug(f"Adding {item.info_hash} to items dict")
                    items_dict[item.info_hash] = item
                else:
                    self.logger.debug(
                        f"TorrentSmartContainer: Skipping duplicate info hash: {item.info_hash}"
                    )
        self.logger.info(
            f"TorrentSmartContainer: Built dictionary with {len(items_dict)} unique items"
        )
        return items_dict

    def _explore_folders_alldebrid(self, folder, files, file_index, type, media):

        if type == "series":
            for file in folder:
                if "e" in file:
                    file_index = self._explore_folders_alldebrid(
                        file["e"], files, file_index, type, media
                    )
                    continue
                parsed_file = parse(file["n"])
                clean_season = media.season.replace("S", "")
                clean_episode = media.episode.replace("E", "")
                numeric_season = int(clean_season)
                numeric_episode = int(clean_episode)
                if (
                    numeric_season in parsed_file.seasons
                    and numeric_episode in parsed_file.episodes
                ):
                    self.logger.debug(
                        f"TorrentSmartContainer: Matching series file found: {file['n']}"
                    )
                    files.append(
                        {
                            "file_index": file_index,
                            "title": file["n"],
                            "size": file["s"] if "s" in file else 0,
                        }
                    )
                file_index += 1
        elif type == "movie":
            file_index = 1
            for file in folder:
                if "e" in file:
                    file_index = self._explore_folders_alldebrid(
                        file["e"], files, file_index, type, media
                    )
                    continue
                self.logger.debug(
                    f"TorrentSmartContainer: Adding movie file: {file['n']}"
                )
                files.append(
                    {
                        "file_index": file_index,
                        "title": file["n"],
                        "size": file["s"] if "s" in file else 0,
                    }
                )
                file_index += 1
        return file_index
