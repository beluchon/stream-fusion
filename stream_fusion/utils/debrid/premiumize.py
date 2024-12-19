# Assuming the BaseDebrid class and necessary imports are already defined as shown previously
import json

from stream_fusion.settings import settings
from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.utils.general import get_info_hash_from_magnet, season_episode_in_filename
from stream_fusion.logging_config import logger
import time


class Premiumize(BaseDebrid):
    def __init__(self, config):
        super().__init__(config)
        self.base_url = "https://www.premiumize.me/api"
        self.api_key = config.get('PMToken') or settings.pm_token
        if not self.api_key:
            logger.error("No Premiumize API key found in config or settings")
            raise ValueError("Premiumize API key is required")
        
        # Vérifier la validité du token
        self._check_token()

    def _check_token(self):
        """Vérifier la validité du token en appelant l'API account/info"""
        url = f"{self.base_url}/account/info"
        response = self.json_response(
            url,
            method='post',
            data={'apikey': self.api_key}
        )
        
        if not response or response.get("status") != "success":
            logger.error(f"Invalid Premiumize API key: {self.api_key}")
            raise ValueError("Invalid Premiumize API key")
        
        logger.info("Premiumize API key is valid")

    def add_magnet(self, magnet, ip=None):
        url = f"{self.base_url}/transfer/create?apikey={self.api_key}"
        
        # Vérifier si c'est un pack de saison
        info_hash = get_info_hash_from_magnet(magnet)
        is_season_pack = self._check_if_season_pack(magnet)
        
        form = {
            'src': magnet,
            'folder_name': f"season_pack_{info_hash}" if is_season_pack else None
        }
        
        response = self.json_response(url, method='post', data=form)
        
        if is_season_pack and response and response.get("status") == "success":
            # Si c'est un pack de saison, on attend que tous les fichiers soient disponibles
            self._wait_for_season_pack(response.get("id"))
            
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
            "pack.saison"
        ]
        return any(indicator in name for indicator in season_indicators)

    def _wait_for_season_pack(self, transfer_id, timeout=300):
        """Attend que tous les fichiers d'un pack de saison soient disponibles"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            transfer_info = self.get_folder_or_file_details(transfer_id)
            if transfer_info and transfer_info.get("status") == "finished":
                return True
            time.sleep(5)
        return False

    def add_torrent(self, torrent_file):
        url = f"{self.base_url}/transfer/create?apikey={self.api_key}"
        form = {'file': torrent_file}
        return self.json_response(url, method='post', data=form)

    def list_transfers(self):
        url = f"{self.base_url}/transfer/list?apikey={self.api_key}"
        return self.json_response(url)

    def get_folder_or_file_details(self, item_id, is_folder=True):
        if is_folder:
            logger.info(f"Getting folder details with id: {item_id}")
            url = f"{self.base_url}/folder/list?id={item_id}&apikey={self.api_key}"
        else:
            logger.info(f"Getting file details with id: {item_id}")
            url = f"{self.base_url}/item/details?id={item_id}&apikey={self.api_key}"
        return self.json_response(url)

    def get_availability(self, hash):
        """Get availability for a single hash"""
        if not hash:
            return {"transcoded": [False]}

        url = f"{self.base_url}/cache/check?apikey={self.api_key}&items[]={hash}"
        response = self.json_response(url)

        if not response or response.get("status") != "success":
            logger.error("Invalid response from Premiumize API")
            return {"transcoded": [False]}

        return {
            "transcoded": response.get("transcoded", [False])
        }

    def get_availability_bulk(self, hashes_or_magnets, ip=None):
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
        response = self.json_response(
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

    def start_background_caching(self, magnet, query=None):
        """Start caching a magnet link in the background."""
        logger.info(f"Starting background caching for magnet")
        
        try:
            # Create a transfer without waiting for completion
            response = self.json_response(
                f"{self.base_url}/transfer/create",
                method="post",
                data={"apikey": self.api_key, "src": magnet}
            )

            if not response or response.get("status") != "success":
                logger.error("Failed to start background caching")
                return False

            transfer_id = response.get("id")
            if not transfer_id:
                logger.error("No transfer ID returned")
                return False

            logger.info(f"Successfully started background caching with transfer ID: {transfer_id}")
            return True
        except Exception as e:
            logger.error(f"Error starting background caching: {str(e)}")
            return False

    def get_stream_link(self, query, config, ip=None):
        """Get a stream link for a magnet link"""
        magnet = query.get("magnet")
        if not magnet:
            logger.error("No magnet link provided")
            return "Error: No magnet link provided"

        logger.info(f"Getting stream link for magnet")
        
        # Try direct download first
        try:
            response = self.json_response(
                f"{self.base_url}/transfer/directdl",
                method="post",
                data={"apikey": self.api_key, "src": magnet}
            )

            if response and response.get("status") == "success":
                logger.info("Got direct download link")
                if "content" in response and response["content"]:
                    # Get the largest file from content
                    largest_file = max(response["content"], key=lambda x: x.get("size", 0))
                    stream_link = largest_file.get("stream_link") or largest_file.get("link")
                    if stream_link:
                        logger.info(f"Found stream link: {stream_link[:50]}...")
                        return stream_link
                elif response.get("location"):
                    logger.info(f"Found direct location: {response['location'][:50]}...")
                    return response["location"]
            
            # Check if the file is already being transferred
            transfers = self.list_transfers()
            for transfer in transfers:
                if transfer.get("src") == magnet:
                    status = transfer.get("status")
                    if status == "finished":
                        # Get the stream link from the finished transfer
                        folder_id = transfer.get("folder_id")
                        if folder_id:
                            folder = self.get_folder_or_file_details(folder_id)
                            if folder and folder.get("content"):
                                largest_file = max(folder["content"], key=lambda x: x.get("size", 0))
                                stream_link = largest_file.get("stream_link") or largest_file.get("link")
                                if stream_link:
                                    logger.info(f"Found stream link from finished transfer: {stream_link[:50]}...")
                                    return stream_link
                    elif status in ["queued", "downloading"]:
                        logger.info(f"Transfer is in progress (status: {status})")
                        return settings.no_cache_video_url

            # If we get here, start background caching
            logger.info("Starting background caching")
            if self.start_background_caching(magnet):
                logger.info("Successfully started background caching")
                return settings.no_cache_video_url
            else:
                logger.error("Failed to start background caching")
                return "Error: Failed to start background caching"

        except Exception as e:
            logger.error(f"Error in get_stream_link: {str(e)}")
            return f"Error: {str(e)}"
