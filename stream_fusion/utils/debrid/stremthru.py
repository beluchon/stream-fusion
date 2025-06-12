import requests
import time
from urllib.parse import quote
import json

from stream_fusion.logging_config import logger
from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.settings import settings
from stream_fusion.utils.general import season_episode_in_filename


class StremThru(BaseDebrid):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.stremthru_url = settings.stremthru_url or "https://stremthru.13377001.xyz"
        self.base_url = f"{self.stremthru_url}/v0/store"
        self.store_name = None
        self.token = None
        self.session = self._create_session()
        
        if not self.store_name:
            self.auto_detect_store()
        
    def _create_session(self):
        session = super()._create_session()
        return session
    
    def auto_detect_store(self):
        """Tente de détecter automatiquement le debrideur à utiliser en fonction des tokens disponibles"""
        priority_order = [
            ("realdebrid", "RDToken"),
            ("premiumize", "PMToken"),
            ("torbox", "TBToken"),
            ("alldebrid", "ADToken"),
            ("debridlink", "DLToken"),
            ("easydebrid", "EDToken"),
            ("offcloud", "OCCredentials"),
            ("pikpak", "PPCredentials")
        ]
        
        for store_name, token_key in priority_order:
            token = self.config.get(token_key)
            if token and len(token.strip()) > 5:
                logger.info(f"StremThru: Utilisation automatique de {store_name} détecté avec le token {token_key}")
                self.set_store_credentials(store_name, token)
                break
        
        if not self.store_name:
            logger.warning("StremThru: Aucun debrideur détecté automatiquement")
    
    def set_store_credentials(self, store_name, token):
        """Configure les informations d'identification du store pour StremThru"""
        self.store_name = store_name
        self.token = token
        self.session.headers["X-StremThru-Store-Name"] = store_name
        self.session.headers["X-StremThru-Store-Authorization"] = f"Bearer {token}"
        self.session.headers["User-Agent"] = "stream-fusion"
        
    @staticmethod
    def get_underlying_debrid_code(store_name=None):
        """Retourne le code du service de debrid sous-jacent (RD, AD, TB, PM, etc.)
        
        Args:
            store_name (str, optional): Nom du store. Si None, retourne None.
        
        Returns:
            str: Code du service de debrid (RD, AD, TB, PM, etc.) ou None si non identifié
        """   
        debrid_codes = {
            "realdebrid": "RD",
            "alldebrid": "AD",
            "torbox": "TB",
            "premiumize": "PM",
            "offcloud": "OC",
            "debridlink": "DL",
            "easydebrid": "ED",
            "pikpak": "PK",
        }
        
        return debrid_codes.get(store_name)
        
    def parse_store_creds(self, token):
        """Parse les informations d'identification du store"""
        if ":" in token:
            parts = token.split(":", 1)
            return parts[0], parts[1]
        return token, ""

    async def check_premium(self, ip=None):
        """Vérifie si l'utilisateur a un compte premium"""
        try:
            client_ip_param = f"&client_ip={ip}" if ip else ""
            response = self.json_response(f"{self.base_url}/user?{client_ip_param}")
            if response and "data" in response:
                return response["data"]["subscription_status"] == "premium"
        except Exception as e:
            logger.warning(f"Exception lors de la vérification du statut premium sur StremThru-{self.store_name}: {e}")
        return False

    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        """Vérifie la disponibilité des torrents avec l'API StremThru"""
        if not hashes_or_magnets:
            return []
            
        results = []
        
        chunk_size = 50
        for i in range(0, len(hashes_or_magnets), chunk_size):
            chunk = hashes_or_magnets[i:i + chunk_size]
            magnets = []

            for hash_or_magnet in chunk:
                if not hash_or_magnet.startswith('magnet:'):
                    clean_hash = hash_or_magnet.lower()
                    if len(clean_hash) > 40:
                        clean_hash = clean_hash[:40]
                    magnet_url = f"magnet:?xt=urn:btih:{clean_hash}"
                else:
                    magnet_url = hash_or_magnet
                magnets.append(magnet_url)
            
            try:
                url = f"{self.base_url}/magnets/check?magnet={','.join([quote(m) for m in magnets])}"
                if ip:
                    url += f"&client_ip={ip}"
                
                logger.debug(f"Vérification de {len(magnets)} magnets sur StremThru-{self.store_name}")
                
                response = self.session.get(url)
                
                if response.status_code == 200:
                    try:
                        json_data = response.json()
                        if json_data and "data" in json_data and "items" in json_data["data"]:
                            for item in json_data["data"]["items"]:
                                if item.get("status") == "cached":
                                    hash_value = item["hash"].lower()
                                    results.append({
                                        "hash": hash_value,
                                        "status": "cached",
                                        "files": item.get("files", []),
                                        "store_name": self.store_name,
                                        "debrid": StremThru.get_underlying_debrid_code(self.store_name)
                                    })
                                    logger.debug(f"Magnet caché trouvé sur StremThru-{self.store_name}: {hash_value}")
                    except Exception as json_e:
                        logger.warning(f"Erreur lors du parsing JSON: {json_e}")
            except Exception as e:
                logger.warning(f"Erreur lors de la vérification des magnets sur StremThru-{self.store_name}: {e}")
                
        return results
    
    def add_magnet(self, magnet, ip=None):
        """Ajoute un magnet à StremThru
        
        Args:
            magnet: URL du magnet ou hash
            ip: Adresse IP du client
        
        Returns:
            dict: Informations sur le magnet ajouté ou None en cas d'erreur
        """
        try:
            if not magnet.startswith('magnet:'):
                magnet = f"magnet:?xt=urn:btih:{magnet}"
                
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/magnets{client_ip_param}"
            
            logger.debug(f"Ajout du magnet sur StremThru-{self.store_name}: {magnet[:60]}...")
            
            response = self.session.post(url, json={"magnet": magnet})
            
            if response.status_code in [200, 201]:
                try:
                    json_data = response.json()
                    if json_data and "data" in json_data:
                        logger.debug(f"Magnet ajouté avec succès sur StremThru-{self.store_name} (code: {response.status_code})")
                        return json_data["data"]
                except Exception as json_e:
                    logger.warning(f"Erreur lors du parsing JSON: {json_e}")
            else:
                logger.error(f"Erreur lors de l'ajout du magnet: {response.status_code} - {response.text}")
        except Exception as e:
            logger.warning(f"Erreur lors de l'ajout du magnet sur StremThru-{self.store_name}: {e}")
        
        return None
    
    def get_magnet_info(self, magnet_info, ip=None):
        """Récupère les informations d'un magnet
        
        Args:
            magnet_info: ID du magnet ou dictionnaire contenant déjà les informations du magnet
            ip: Adresse IP du client
            
        Returns:
            dict: Informations sur le magnet ou None en cas d'erreur
        """
        if isinstance(magnet_info, dict):
            if "files" in magnet_info and "id" in magnet_info:
                logger.debug(f"Utilisation des informations de magnet déjà disponibles pour {magnet_info.get('id')}")
                return magnet_info
            
            magnet_id = magnet_info.get("id")
            if not magnet_id:
                logger.error("Aucun ID de magnet trouvé dans les informations fournies")
                return None
        else:
            magnet_id = magnet_info
        
        try:
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/magnets/{magnet_id}{client_ip_param}"
            
            logger.debug(f"Récupération des informations du magnet {magnet_id} sur StremThru-{self.store_name}")
            
            response = self.session.get(url)
            
            if response.status_code in [200, 201]:
                try:
                    json_data = response.json()
                    if json_data and "data" in json_data:
                        logger.debug(f"Informations du magnet {magnet_id} récupérées avec succès")
                        return json_data["data"]
                except Exception as json_e:
                    logger.warning(f"Erreur lors du parsing JSON: {json_e}")
            else:
                logger.error(f"Erreur lors de la récupération du magnet: {response.status_code} - {response.text}")
                
                if isinstance(magnet_info, dict) and "files" in magnet_info:
                    logger.debug("Utilisation des informations de fichiers déjà disponibles dans le magnet")
                    return magnet_info
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération du magnet {magnet_id}: {e}")
                                                        
            if isinstance(magnet_info, dict) and "files" in magnet_info:
                logger.debug("Utilisation des informations de fichiers déjà disponibles dans le magnet après erreur")
                return magnet_info                                                                          
        
        return None
    
    def get_stream_link(self, query, config=None, ip=None):
        """Génère un lien de streaming à partir d'une requête
        
        Args:
            query: Dictionnaire contenant les informations de la requête
            config: Configuration de l'application
            ip: Adresse IP du client
            
        Returns:
            str: URL du stream ou None en cas d'erreur
        """
        try:
            logger.debug(f"StremThru: Génération d'un lien de streaming pour {query}")
            
            if not self.store_name:
                self.auto_detect_store()
                
                if not self.store_name:
                    logger.error("StremThru: Aucun debrideur configuré pour StremThru")
                    return None
            
            stream_type = query.get('type')
            if not stream_type:
                logger.error("StremThru: Le type de média n'est pas défini dans la requête")
                return None
                
            season = query.get("season")
            episode = query.get("episode")
            
            magnet_url = query.get("magnet")
            info_hash = query.get("infoHash")
            file_idx = query.get("file_index", query.get("fileIdx", -1))
            
            if magnet_url and not info_hash:
                import re
                hash_match = re.search(r'btih:([a-fA-F0-9]+)', magnet_url)
                if hash_match:
                    info_hash = hash_match.group(1).lower()
                    logger.debug(f"StremThru: Hash extrait du magnet: {info_hash}")
            
            if not info_hash:
                logger.error("StremThru: Aucun hash trouvé dans la requête")
                return None
            
            service = query.get("service")
            if service and service != "ST":
                logger.debug(f"StremThru: Utilisation du service {service} spécifié dans la requête")
                
            magnet = magnet_url or f"magnet:?xt=urn:btih:{info_hash}"
            logger.debug(f"StremThru: Ajout direct du magnet {magnet} via le store {self.store_name}")
            magnet_info = self.add_magnet(magnet, ip)
            
            if not magnet_info:
                logger.error(f"StremThru: Impossible d'ajouter le magnet {info_hash}")
                return None
                
            logger.debug(f"StremThru: Utilisation des informations du magnet")
            magnet_data = magnet_info
            
            if not magnet_data or "files" not in magnet_data:
                magnet_id = magnet_info.get("id")
                if magnet_id:
                    logger.debug(f"StremThru: Récupération des informations du magnet {magnet_id}")
                    magnet_data = self.get_magnet_info(magnet_info, ip)
                
                if not magnet_data:
                    logger.error(f"StremThru: Impossible de récupérer les informations du magnet")
                    return None
            
            if "files" not in magnet_data:
                logger.error(f"StremThru: Aucun fichier dans le magnet {magnet_data.get('id', 'inconnu')}")
                return None
                
            target_file = None
            
            # Pour les séries, chercher d'abord par nom
            if stream_type == "series" and season and episode:
                try:
                    numeric_season = int(season.replace("S", ""))
                    numeric_episode = int(episode.replace("E", ""))
                    
                    for file in magnet_data["files"]:
                        file_name = file.get("name", "").lower()
                        
                        if not any(ext in file_name for ext in [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"]):
                            continue
                            
                        if season_episode_in_filename(file_name, numeric_season, numeric_episode):
                            target_file = file
                            logger.info(f"StremThru: Fichier trouvé par NOM: {file_name} (index: {file.get('index')})")
                            break
                except Exception as e:
                    logger.warning(f"StremThru: Erreur lors de la recherche par nom: {str(e)}")
            
            # Si pas trouvé par nom et qu'un index est spécifié, essayer par index
            if not target_file and file_idx is not None:
                target_file = next((f for f in magnet_data["files"] if f.get("index") == file_idx), None)
                if target_file:
                    logger.info(f"StremThru: Fichier trouvé par INDEX {file_idx}: {target_file.get('name')}")
            
            # Si toujours pas trouvé, prendre le plus gros fichier vidéo
            if not target_file:
                logger.debug(f"StremThru: Aucun fichier trouvé par nom ou index, recherche du plus gros fichier")
                video_files = []
                
                for file in magnet_data["files"]:
                    file_name = file.get("name", "").lower()
                    
                    if any(ext in file_name for ext in [".nfo", ".txt", ".jpg", ".png", ".srt", ".sub"]):
                        logger.debug(f"StremThru: Ignoré le fichier non-vidéo: {file_name}")
                        continue
                    
                    if any(ext in file_name for ext in [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"]):
                        video_files.append(file)
                
                if video_files:
                    target_file = sorted(video_files, key=lambda x: x.get("size", 0), reverse=True)[0]
                    logger.info(f"StremThru: Sélection du plus gros fichier vidéo: {target_file.get('name')} (index: {target_file.get('index')})")
                else:
                    target_file = magnet_data["files"][0] if magnet_data["files"] else None
                    if target_file:
                        logger.warning(f"StremThru: Aucun fichier vidéo trouvé, utilisation du premier fichier: {target_file.get('name')} (index: {target_file.get('index')})")
                    else:
                        logger.error("StremThru: Aucun fichier trouvé dans le torrent")
                        return None
            
            if not target_file or "link" not in target_file:
                logger.error(f"StremThru: Fichier cible non trouvé ou sans lien")
                return None
                
            torrent_id = magnet_info.get("id", "")
            file_id = target_file.get("index", "")
            
            if stream_type == "series" and season and episode:
                logger.info(f"StremThru: Sélection finale de S{season}E{episode} dans le torrent {torrent_id}, fichier: {target_file.get('name')} (index: {file_id})")
            else:
                logger.info(f"StremThru: Sélection finale du fichier {target_file.get('name')} (index: {file_id}) dans le torrent {torrent_id}")
            
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/link/generate{client_ip_param}"
            
            logger.debug(f"StremThru: Génération du lien pour {target_file.get('name')}")
            
            json_data = {"link": target_file["link"]}
            
            try:
                response = self.session.post(url, json=json_data)
                
                if response.status_code in [200, 201]:
                    try:
                        json_data = response.json()
                        if json_data and "data" in json_data and "link" in json_data["data"]:
                            stream_link = json_data["data"]["link"]
                            logger.info(f"StremThru: Lien de streaming généré avec succès: {stream_link}")
                            return stream_link
                    except json.JSONDecodeError:
                        stream_link = response.text.strip()
                        if stream_link.startswith(('http://', 'https://')):
                            logger.info(f"StremThru: Lien de streaming reçu directement: {stream_link}")
                            return stream_link
                        else:
                            logger.error(f"StremThru: Réponse non-JSON invalide: {stream_link[:100]}...")
                    except Exception as e:
                        logger.error(f"StremThru: Erreur lors du traitement de la réponse: {str(e)}")
                else:
                    logger.error(f"StremThru: Échec de la génération du lien de streaming: {response.status_code} - {response.text[:100]}...")
            except Exception as e:
                logger.error(f"StremThru: Erreur lors de la génération du lien: {str(e)}")
            
            return None
        
        except Exception as e:
            logger.warning(f"Erreur lors de la génération du lien sur StremThru-{self.store_name}: {e}")
        
        return None
    
    def start_background_caching(self, magnet, query=None):
        """Démarre le téléchargement d'un magnet en arrière-plan."""
        logger.info(f"Démarrage du téléchargement en arrière-plan pour un magnet via StremThru-{self.store_name}")
        
        try:
            result = self.add_magnet(magnet)
            
            if not result:
                logger.error(f"Échec du démarrage du téléchargement en arrière-plan via StremThru-{self.store_name}")
                return False
                
            magnet_id = result.get("id")
            if not magnet_id:
                logger.error(f"Aucun ID de magnet retourné par StremThru-{self.store_name}")
                return False
                
            logger.info(f"Téléchargement en arrière-plan démarré avec succès via StremThru-{self.store_name}, ID: {magnet_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors du démarrage du téléchargement en arrière-plan via StremThru-{self.store_name}: {str(e)}")
            return False
