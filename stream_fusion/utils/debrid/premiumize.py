import json

from stream_fusion.settings import settings
from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.utils.general import get_info_hash_from_magnet, season_episode_in_filename
from stream_fusion.logging_config import logger
import time
import asyncio


class Premiumize(BaseDebrid):
    def __init__(self, config, session=None):
        super().__init__(config, session=session)
        self.base_url = "https://www.premiumize.me/api"
        self.api_key = config.get('PMToken') or settings.pm_token
        if not self.api_key:
            logger.error("No Premiumize API key found in config or settings")
            raise ValueError("Premiumize API key is required")
        
        # Note: La vérification du token est maintenant asynchrone et ne peut pas être appelée dans __init__
        # Elle sera appelée à la première utilisation de l'API

    async def _check_token(self):
        """Vérifier la validité du token en appelant l'API account/info"""
        url = f"{self.base_url}/account/info"
        response = await self.json_response(
            url,
            method='post',
            data={'apikey': self.api_key}
        )
        
        if not response or response.get("status") != "success":
            logger.error(f"Invalid Premiumize API key: {self.api_key}")
            raise ValueError("Invalid Premiumize API key")
        
        logger.info("Premiumize API key is valid")

    async def add_magnet(self, magnet, ip=None):
        url = f"{self.base_url}/transfer/create?apikey={self.api_key}"
        
        # Vérifier si c'est un pack de saison
        info_hash = get_info_hash_from_magnet(magnet)
        is_season_pack = self._check_if_season_pack(magnet)
        
        # Créer le dictionnaire de formulaire
        form = {'src': magnet}
        
        # Ajouter folder_name seulement si c'est un pack de saison
        if is_season_pack:
            form['folder_name'] = f"season_pack_{info_hash}"
        
        # Ajouter des logs pour le débogage
        logger.debug(f"Premiumize: Sending add_magnet request with data: {form}")
        
        response = await self.json_response(url, method='post', data=form)
        
        if is_season_pack and response and response.get("status") == "success":
            # Si c'est un pack de saison, on attend que tous les fichiers soient disponibles
            transfer_id = response.get("id")
            if transfer_id:
                # Attendre que le transfert soit terminé
                if await self._wait_for_season_pack(transfer_id):
                    # Une fois terminé, récupérer les détails du dossier
                    folder_details = await self.get_folder_or_file_details(transfer_id)
                    if folder_details and folder_details.get("content"):
                        # Trier les fichiers par taille pour prendre le plus gros fichier vidéo
                        video_files = [f for f in folder_details["content"] 
                                     if f.get("mime_type", "").startswith("video/")]
                        if video_files:
                            largest_file = max(video_files, key=lambda x: x.get("size", 0))
                            response["selected_file"] = {
                                "id": largest_file.get("id"),
                                "name": largest_file.get("name"),
                                "size": largest_file.get("size"),
                                "link": largest_file.get("link"),
                                "stream_link": largest_file.get("stream_link")
                            }
            
        return response

    def _check_if_season_pack(self, magnet):
        """Vérifie si le magnet link correspond à un pack de saison"""
        # Vérifie les patterns communs dans le nom du torrent
        name = magnet.lower()
        season_indicators = [
            "complete.season", 
            "season.complete",
            "s01.complete",
            "saison.complete",
            "season.pack",
            "pack.saison",
            ".s01.",
            ".s02.",
            ".s03.",
            ".s04.",
            ".s05.",
            "saison"
        ]
        return any(indicator in name for indicator in season_indicators)

    async def _wait_for_season_pack(self, transfer_id, timeout=300):
        """Attend que tous les fichiers d'un pack de saison soient disponibles"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            transfer_info = await self.get_folder_or_file_details(transfer_id)
            if transfer_info and transfer_info.get("status") == "success":
                # Vérifier si des fichiers vidéo sont présents
                if transfer_info.get("content"):
                    video_files = [f for f in transfer_info["content"] 
                                 if f.get("mime_type", "").startswith("video/")]
                    if video_files:
                        return True
            await asyncio.sleep(5)  # Utiliser asyncio.sleep au lieu de time.sleep
        return False

    async def add_torrent(self, torrent_file):
        url = f"{self.base_url}/transfer/create?apikey={self.api_key}"
        form = {'file': torrent_file}
        return await self.json_response(url, method='post', data=form)

    async def list_transfers(self):
        url = f"{self.base_url}/transfer/list?apikey={self.api_key}"
        return await self.json_response(url)

    async def get_folder_or_file_details(self, item_id, is_folder=True):
        if is_folder:
            logger.info(f"Getting folder details with id: {item_id}")
            url = f"{self.base_url}/folder/list?id={item_id}&apikey={self.api_key}"
        else:
            logger.info(f"Getting file details with id: {item_id}")
            url = f"{self.base_url}/item/details?id={item_id}&apikey={self.api_key}"
            
        return await self.json_response(url)

    async def get_availability(self, hash):
        """Get availability for a single hash"""
        url = f"{self.base_url}/cache/check?apikey={self.api_key}&items[]={hash}"
        response = await self.json_response(url)
        
        if not response or response.get("status") != "success":
            logger.error("Invalid response from Premiumize API")
            return {"transcoded": [False]}
        
        return {
            "transcoded": response.get("transcoded", [False])
        }

    async def get_availability_bulk(self, hashes_or_magnets, ip=None):
        """Get availability for multiple hashes or magnets"""
        if not hashes_or_magnets:
            return {}

        logger.info(f"Checking availability for {len(hashes_or_magnets)} items")
        logger.debug(f"Using Premiumize API key: {self.api_key}")
        
        # Construire l'URL avec les paramètres
        params = []
        for hash in hashes_or_magnets:
            params.append(f"items[]={hash}")
        
        url = f"{self.base_url}/cache/check"
        response = await self.json_response(
            url,
            method='post',
            data={
                'apikey': self.api_key,
                'items[]': hashes_or_magnets
            }
        )
        
        logger.info(f"Raw Premiumize response: {response}")

        if not response or response.get("status") != "success":
            logger.error("Invalid response from Premiumize API")
            return {}

        # Format response to match expected structure
        result = {}
        for i, hash_or_magnet in enumerate(hashes_or_magnets):
            # Vérifier si le fichier est disponible en utilisant le champ response
            is_available = bool(response.get("response", [])[i]) if isinstance(response.get("response", []), list) and i < len(response["response"]) else False
            
            # Récupérer le nom du fichier s'il est disponible
            filename = None
            if isinstance(response.get("filename", []), list) and i < len(response["filename"]):
                filename = response["filename"][i]
            
            # Récupérer la taille du fichier et la convertir en entier
            filesize = 0
            if isinstance(response.get("filesize", []), list) and i < len(response["filesize"]):
                try:
                    filesize = int(response["filesize"][i]) if response["filesize"][i] is not None else 0
                except (ValueError, TypeError):
                    filesize = 0
            
            result[hash_or_magnet] = {
                "transcoded": is_available,
                "filename": filename,
                "filesize": filesize
            }
        
        logger.info(f"Formatted response: {result}")
        logger.info(f"Got availability for {len(result)} items")
        return result

    async def start_background_caching(self, magnet, query=None):
        """Start caching a magnet link in the background."""
        # Afficher le début du magnet dans les logs
        if isinstance(magnet, str):
            magnet_preview = magnet[:50] + "..." if len(magnet) > 50 else magnet
            logger.info(f"Starting background caching for magnet: {magnet_preview}")
        else:
            logger.info(f"Starting background caching for magnet object: {type(magnet)}")
        
        try:
            # Extraire le magnet si c'est un dictionnaire
            if isinstance(magnet, dict) and magnet.get("magnet"):
                magnet = magnet.get("magnet")
            
            # Vérifier si c'est un pack de saison
            info_hash = get_info_hash_from_magnet(magnet)
            is_season_pack = self._check_if_season_pack(magnet)
            
            # Créer le dictionnaire de formulaire
            form = {'src': magnet}
            
            # Ajouter folder_name seulement si c'est un pack de saison
            if is_season_pack:
                form['folder_name'] = f"season_pack_{info_hash}"
            
            # Ajouter l'apikey
            url = f"{self.base_url}/transfer/create"
            
            # Ajouter des logs pour le débogage
            logger.debug(f"Premiumize: Sending background caching request with data: {form}")
            
            response = await self.json_response(url, method='post', data={**form, 'apikey': self.api_key})
            
            if not response:
                logger.error("Premiumize: Aucune réponse reçue de l'API pour start_background_caching")
                return False
            elif response.get("status") != "success":
                logger.error(f"Premiumize: Échec du démarrage du caching en arrière-plan. Réponse: {response}")
                return False

            transfer_id = response.get("id")
            if not transfer_id:
                logger.error("Premiumize: Aucun ID de transfert retourné")
                return False

            logger.info(f"Premiumize: Démarrage réussi du caching en arrière-plan avec l'ID de transfert: {transfer_id}")
            return True
        except Exception as e:
            logger.error(f"Premiumize: Erreur lors du démarrage du caching en arrière-plan: {str(e)}")
            return False

    async def get_stream_link(self, query, config, ip=None):
        """Get a stream link for a magnet link"""
        if not query:
            logger.warning("Premiumize: Aucun query fourni pour get_stream_link")
            return None
            
        # Afficher le début du magnet dans les logs
        if isinstance(query, str):
            query_preview = query[:50] + "..." if len(query) > 50 else query
            logger.info(f"Premiumize: Récupération du lien de streaming pour le magnet: {query_preview}")
        elif isinstance(query, dict) and query.get("magnet"):
            magnet_preview = query["magnet"][:50] + "..." if len(query["magnet"]) > 50 else query["magnet"]
            logger.info(f"Premiumize: Récupération du lien de streaming pour le magnet: {magnet_preview}")
        else:
            logger.info(f"Premiumize: Récupération du lien de streaming pour l'objet query: {type(query)}")
        
        # Vérifier si c'est une série et extraire la saison/épisode
        season = None
        episode = None
        if isinstance(query, dict):
            magnet = query.get("magnet")
            if not magnet:
                logger.error("No magnet link in query")
                return None
                
            # Vérifier si c'est une série
            if query.get("type") == "series" and query.get("season") and query.get("episode"):
                season = query["season"].replace("S", "") if isinstance(query["season"], str) else query["season"]
                episode = query["episode"].replace("E", "") if isinstance(query["episode"], str) else query["episode"]
                try:
                    season = int(season)
                    episode = int(episode)
                except (ValueError, TypeError):
                    logger.error(f"Invalid season/episode format: {season}/{episode}")
                    return None
        else:
            magnet = query

        # Essayer d'abord le téléchargement direct
        try:
            response = await self.json_response(
                f"{self.base_url}/transfer/directdl",
                method="post",
                data={"apikey": self.api_key, "src": magnet}
            )

            if response and response.get("status") == "success":
                logger.info("Got direct download response")
                if "content" in response and response["content"]:
                    # Si c'est une série, chercher l'épisode correspondant
                    if season is not None and episode is not None:
                        matching_files = []
                        for file in response["content"]:
                            filename = file.get("path", "").split("/")[-1]
                            if season_episode_in_filename(filename, season, episode):
                                matching_files.append(file)
                        
                        if matching_files:
                            # Prendre le plus gros fichier parmi ceux qui correspondent
                            selected_file = max(matching_files, key=lambda x: x.get("size", 0))
                            stream_link = selected_file.get("stream_link") or selected_file.get("link")
                            if stream_link:
                                logger.info(f"Found matching episode stream link: {stream_link[:50]}...")
                                return stream_link
                    
                    # Si ce n'est pas une série ou si aucun fichier ne correspond,
                    # prendre le plus gros fichier vidéo
                    video_files = [f for f in response["content"] 
                                 if isinstance(f.get("path", ""), str) and 
                                 f.get("path", "").lower().endswith((".mkv", ".mp4", ".avi", ".m4v"))]
                    
                    if video_files:
                        largest_file = max(video_files, key=lambda x: x.get("size", 0))
                        stream_link = largest_file.get("stream_link") or largest_file.get("link")
                        if stream_link:
                            logger.info(f"Found stream link: {stream_link[:50]}...")
                            return stream_link
                elif response.get("location"):
                    logger.info(f"Found direct location: {response['location'][:50]}...")
                    return response["location"]

        except Exception as e:
            logger.error(f"Error in direct download: {str(e)}")
            # Continue avec la méthode standard si le téléchargement direct échoue
            
        # Si le téléchargement direct a échoué, essayer la méthode standard
        logger.info(f"Premiumize: Tentative d'ajout du magnet: {magnet[:50]}...")
        response = await self.add_magnet(magnet, ip)
        
        if not response:
            logger.error("Premiumize: Aucune réponse reçue de l'API pour add_magnet")
            return None
        elif response.get("status") != "success":
            logger.error(f"Premiumize: Échec de l'ajout du magnet. Réponse: {response}")
            return None
        
        logger.info(f"Premiumize: Magnet ajouté avec succès. ID: {response.get('id')}")

            
        # Récupérer l'ID du transfert
        transfer_id = response.get("id")
        if not transfer_id:
            logger.error("No transfer ID in response")
            return None
            
        # Attendre que le transfert soit terminé
        if not await self._wait_for_season_pack(transfer_id):
            logger.error("Transfer timed out")
            return None
            
        # Récupérer les détails du dossier
        folder_details = await self.get_folder_or_file_details(transfer_id)
        if not folder_details or not folder_details.get("content"):
            logger.error("No content in folder details")
            return None
            
        # Filtrer les fichiers vidéo
        video_files = [f for f in folder_details["content"] 
                      if isinstance(f.get("mime_type", ""), str) and 
                      f.get("mime_type", "").startswith("video/")]
        
        # Si c'est une série, chercher l'épisode correspondant
        if season is not None and episode is not None:
            matching_files = []
            for file in video_files:
                filename = file.get("name", "")
                if season_episode_in_filename(filename, season, episode):
                    matching_files.append(file)
                    
            if matching_files:
                # Prendre le plus gros fichier parmi ceux qui correspondent
                selected_file = max(matching_files, key=lambda x: x.get("size", 0))
                logger.info(f"Selected matching episode file: {selected_file.get('name')}")
                return selected_file.get("stream_link")
        
        # Si ce n'est pas une série ou si aucun fichier ne correspond, 
        # prendre le plus gros fichier vidéo
        if video_files:
            selected_file = max(video_files, key=lambda x: x.get("size", 0))
            logger.info(f"Selected largest video file: {selected_file.get('name')}")
            return selected_file.get("stream_link")
            
        logger.error("No suitable video file found")
        return None
