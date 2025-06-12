import requests
import time
from urllib.parse import quote

from stream_fusion.logging_config import logger
from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.settings import settings


class StremThru(BaseDebrid):
    def __init__(self, config):
        super().__init__(config)
        self.config = config
        self.stremthru_url = settings.stremthru_url or "https://stremthru.13377001.xyz"
        self.base_url = f"{self.stremthru_url}/v0/store"
        self.store_name = None
        self.token = None
        self.session = self._create_session()
        
        # Si aucun store n'est spécifié, tenter une détection automatique
        if not self.store_name:
            self.auto_detect_store()
        
    def _create_session(self):
        session = super()._create_session()
        return session
    
    def auto_detect_store(self):
        """Tente de détecter automatiquement le debrideur à utiliser en fonction des tokens disponibles"""
        # Priorité: RD > PM > TB > AD > DL > ED > OC > PP
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
            if token and len(token.strip()) > 5:  # Token valide (au moins 6 caractères)
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
        # Mapping des noms de stores vers les codes de debrid
        debrid_codes = {
            "realdebrid": "RD",
            "alldebrid": "AD",
            "torbox": "TB",
            "premiumize": "PM",
            "offcloud": "OC",  # Offcloud
            "debridlink": "DL",  # DebridLink
            "easydebrid": "ED",  # EasyDebrid
            "pikpak": "PK",      # PikPak
        }
        
        # Retourner le code correspondant ou None si non identifié
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
        
        # Regrouper les hashes en lots de 50 maximum (comme dans Comet)
        chunk_size = 50
        for i in range(0, len(hashes_or_magnets), chunk_size):
            chunk = hashes_or_magnets[i:i + chunk_size]
            magnets = []
            
            # Convertir tous les hashes en magnets
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
                # Vérifier tous les magnets en une seule requête (comme dans Comet)
                url = f"{self.base_url}/magnets/check?magnet={','.join([quote(m) for m in magnets])}"
                if ip:
                    url += f"&client_ip={ip}"
                
                # Utiliser le nom du store pour le logging
                logger.debug(f"Vérification de {len(magnets)} magnets sur StremThru-{self.store_name}")
                
                response = self.session.get(url)
                
                if response.status_code == 200:
                    try:
                        json_data = response.json()
                        if json_data and "data" in json_data and "items" in json_data["data"]:
                            for item in json_data["data"]["items"]:
                                # Vérifier uniquement le statut 'cached' comme dans Comet
                                if item.get("status") == "cached":
                                    hash_value = item["hash"].lower()
                                    # Ajouter le hash aux résultats
                                    results.append({
                                        "hash": hash_value,
                                        "status": "cached",
                                        "files": item.get("files", []),
                                        # Ajouter le store_name pour que le container puisse identifier le service
                                        "store_name": self.store_name,
                                        "debrid": StremThru.get_underlying_debrid_code(self.store_name)
                                    })
                                    # Log pour débogage
                                    logger.debug(f"Magnet caché trouvé sur StremThru-{self.store_name}: {hash_value}")
                    except Exception as json_e:
                        logger.warning(f"Erreur lors du parsing JSON: {json_e}")
            except Exception as e:
                logger.warning(f"Erreur lors de la vérification des magnets sur StremThru-{self.store_name}: {e}")
                
            # Note: Nous n'ajoutons pas automatiquement les magnets ici
            # L'ajout se fera uniquement lorsque l'utilisateur sélectionnera un fichier à lire
            # via la méthode get_stream_link
                
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
                # Convertir le hash en magnet complet
                magnet = f"magnet:?xt=urn:btih:{magnet}"
                
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/magnets{client_ip_param}"
            
            logger.debug(f"Ajout du magnet sur StremThru-{self.store_name}: {magnet[:60]}...")
            
            # Utiliser directement la session avec json=data au lieu de data=data
            response = self.session.post(url, json={"magnet": magnet})
            
            # Accepter à la fois 200 (OK) et 201 (Created) comme codes de succès
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
        # Si magnet_info est déjà un dictionnaire contenant les informations nécessaires, le retourner directement
        if isinstance(magnet_info, dict):
            if "files" in magnet_info and "id" in magnet_info:
                logger.debug(f"Utilisation des informations de magnet déjà disponibles pour {magnet_info.get('id')}")
                return magnet_info
            
            # Si le dictionnaire ne contient pas les informations nécessaires mais a un ID, utiliser cet ID
            magnet_id = magnet_info.get("id")
            if not magnet_id:
                logger.error("Aucun ID de magnet trouvé dans les informations fournies")
                return None
        else:
            # Si magnet_info est un ID (string), l'utiliser directement
            magnet_id = magnet_info
        
        try:
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/magnets/{magnet_id}{client_ip_param}"
            
            logger.debug(f"Récupération des informations du magnet {magnet_id} sur StremThru-{self.store_name}")
            
            # Utiliser directement la session
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
                
                # Si nous avons un dictionnaire avec des fichiers, utilisons-le directement
                if isinstance(magnet_info, dict) and "files" in magnet_info:
                    logger.debug("Utilisation des informations de fichiers déjà disponibles dans le magnet")
                    return magnet_info
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération du magnet {magnet_id}: {e}")
            
            # En cas d'erreur, si nous avons un dictionnaire avec des fichiers, utilisons-le directement
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
            
            # Si aucun store n'est défini, tenter la détection automatique
            if not self.store_name:
                self.auto_detect_store()
                
                # Si toujours aucun store après détection, impossible de continuer
                if not self.store_name:
                    logger.error("StremThru: Aucun debrideur configuré pour StremThru")
                    return None
            
            # Extraire les informations nécessaires de la requête
            # Extraire le type de stream (film ou série)
            stream_type = query.get('type')
            if not stream_type:
                logger.error("StremThru: Le type de média n'est pas défini dans la requête")
                return None
                
            # Extraire la saison et l'épisode pour les séries
            season = query.get("season")
            episode = query.get("episode")
            
            # Vérifier si la requête contient un magnet ou un infoHash
            magnet_url = query.get("magnet")
            info_hash = query.get("infoHash")
            file_idx = query.get("file_index", query.get("fileIdx", -1))
            
            # Extraire le hash du magnet si présent
            if magnet_url and not info_hash:
                # Format: magnet:?xt=urn:btih:HASH&...
                import re
                hash_match = re.search(r'btih:([a-fA-F0-9]+)', magnet_url)
                if hash_match:
                    info_hash = hash_match.group(1).lower()
                    logger.debug(f"StremThru: Hash extrait du magnet: {info_hash}")
            
            if not info_hash:
                logger.error("StremThru: Aucun hash trouvé dans la requête")
                return None
            
            # Utiliser le service spécifié dans la requête ou celui par défaut
            service = query.get("service")
            if service and service != "ST":
                logger.debug(f"StremThru: Utilisation du service {service} spécifié dans la requête")
                
            # Ajouter directement le magnet à StremThru sans vérifier s'il est disponible
            magnet = magnet_url or f"magnet:?xt=urn:btih:{info_hash}"
            logger.debug(f"StremThru: Ajout direct du magnet {magnet} via le store {self.store_name}")
            magnet_info = self.add_magnet(magnet, ip)
            
            if not magnet_info:
                logger.error(f"StremThru: Impossible d'ajouter le magnet {info_hash}")
                return None
                
            # Utiliser directement les informations du magnet
            logger.debug(f"StremThru: Utilisation des informations du magnet")
            magnet_data = magnet_info
            
            # Vérifier si le magnet contient des fichiers
            if not magnet_data or "files" not in magnet_data:
                # Si les fichiers ne sont pas disponibles, essayer de récupérer les informations du magnet
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
                
            # Logique identique aux autres debrideurs : utiliser SEULEMENT le file_index pré-calculé
            target_file = None
            
            # Chercher le fichier correspondant à l'index pré-calculé par TorrentSmartContainer
            if file_idx != -1:
                logger.debug(f"StremThru: Recherche du fichier avec index {file_idx}")
                for file in magnet_data["files"]:
                    if str(file.get("index")) == str(file_idx):
                        target_file = file
                        logger.debug(f"StremThru: Fichier trouvé avec index {file_idx}: {file.get('name')}")
                        break
                
                if not target_file:
                    logger.error(f"StremThru: Fichier avec index {file_idx} non trouvé - échec de la sélection")
            else:
                logger.error(f"StremThru: Aucun file_index fourni - TorrentSmartContainer n'a pas pu sélectionner de fichier")
            
            if not target_file or "link" not in target_file:
                logger.error(f"StremThru: Fichier cible non trouvé ou sans lien")
                return None
                
            # Générer un identifiant unique pour ce torrent, fichier et épisode
            torrent_id = magnet_info.get("id", "")
            file_id = target_file.get("index", "")
            
            # Log détaillé pour le débogage
            if stream_type == "series" and season and episode:
                logger.info(f"StremThru: Sélection de S{season}E{episode} dans le torrent {torrent_id}, fichier index {file_id}")
            else:
                logger.info(f"StremThru: Sélection du fichier {file_id} dans le torrent {torrent_id}")
            
            # Générer le lien de streaming
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/link/generate{client_ip_param}"
            
            logger.debug(f"StremThru: Génération du lien pour {target_file.get('name')}")
            
            # Logique standard comme les autres debrideurs
            json_data = {"link": target_file["link"]}
            
            # Utiliser directement la session avec json=data au lieu de data=data
            response = self.session.post(url, json=json_data)
            
            if response.status_code in [200, 201]:
                try:
                    json_data = response.json()
                    if json_data and "data" in json_data and "link" in json_data["data"]:
                        stream_link = json_data["data"]["link"]
                        logger.info(f"StremThru: Lien de streaming généré avec succès: {stream_link}")
                        return stream_link
                    else:
                        logger.error(f"StremThru: Réponse invalide: {json_data}")
                except Exception as json_e:
                    logger.warning(f"Erreur lors du parsing JSON: {json_e}")
            else:
                logger.error(f"StremThru: Échec de la génération du lien de streaming: {response.status_code} - {response.text}")
            
        except Exception as e:
            logger.warning(f"Erreur lors de la génération du lien sur StremThru-{self.store_name}: {e}")
        
        return None
    
    def get_magnet_info(self, magnet_id, ip=None):
        """Récupère les informations d'un magnet"""
        try:
            client_ip_param = f"?client_ip={ip}" if ip else ""
            url = f"{self.base_url}/magnets/{magnet_id}{client_ip_param}"
            
            response = self.json_response(url)
            if response and "data" in response:
                return response["data"]
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération des informations du magnet sur StremThru-{self.store_name}: {e}")
        
        return None
        
    def start_background_caching(self, magnet, query=None):
        """Démarre le téléchargement d'un magnet en arrière-plan."""
        logger.info(f"Démarrage du téléchargement en arrière-plan pour un magnet via StremThru-{self.store_name}")
        
        try:
            # Ajouter le magnet sans attendre la fin du téléchargement
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
