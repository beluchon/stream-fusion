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
        form = {'src': magnet}
        return self.json_response(url, method='post', data=form)

    # Doesn't work for the time being. Premiumize does not support torrent file torrents
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

    def get_stream_link(self, query, config, ip=None):
        """Get a stream link for a magnet link"""
        magnet = query.get("magnet")
        if not magnet:
            logger.error("No magnet link provided")
            return "Error: No magnet link provided"

        logger.info(f"Getting stream link for magnet")
        
        # Try direct download first
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
                return largest_file.get("stream_link") or largest_file.get("link")
            return response.get("location")

        # If direct download fails, create a transfer
        logger.info("Direct download failed, creating transfer")
        transfer_data = self.json_response(
            f"{self.base_url}/transfer/create",
            method="post",
            data={"apikey": self.api_key, "src": magnet}
        )

        if not transfer_data or transfer_data.get("status") != "success":
            logger.error("Failed to create transfer")
            return "Error: Failed to create transfer"

        transfer_id = transfer_data["id"]
        logger.info(f"Transfer created with ID: {transfer_id}")

        # Wait for the transfer to complete
        for _ in range(6):  # Try for 30 seconds
            transfers = self.json_response(f"{self.base_url}/transfer/list?apikey={self.api_key}")
            if transfers and transfers.get("status") == "success":
                for transfer in transfers.get("transfers", []):
                    if transfer["id"] == transfer_id:
                        if transfer.get("status") == "finished":
                            file_id = transfer.get("file_id")
                            if file_id:
                                details = self.json_response(
                                    f"{self.base_url}/item/details?apikey={self.api_key}&id={file_id}"
                                )
                                if details:
                                    return details.get("stream_link") or details.get("link")
            time.sleep(5)

        logger.warning("Transfer not ready, returning no cache URL")
        return settings.no_cache_video_url
