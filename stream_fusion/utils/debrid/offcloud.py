from stream_fusion.utils.debrid.stremthru import StremThru
from stream_fusion.logging_config import logger


class Offcloud(StremThru):
    def __init__(self, config):
        super().__init__(config)
        self.name = "Offcloud"
        self.extension = "OC"
        
        # Récupérer les identifiants Offcloud (email:password)
        self.credentials = config.get("offcloud_credentials", "")
        
        # Configurer StremThru pour utiliser Offcloud
        self.set_store_credentials("offcloud", self.credentials)
        
    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        """Vérifie la disponibilité des torrents en masse via StremThru"""
        results = super().get_availability_bulk(hashes_or_magnets, ip)
        logger.debug(f"Offcloud (via StremThru): {len(results)} torrents en cache trouvés")
        # Note: Pour Offcloud, la liste des fichiers est toujours vide selon la documentation StremThru
        return results
    
    def add_magnet(self, magnet, ip=None):
        """Ajoute un magnet à Offcloud via StremThru"""
        result = super().add_magnet(magnet, ip)
        logger.debug(f"Offcloud (via StremThru): Magnet ajouté avec succès: {result is not None}")
        return result
    
    def get_stream_link(self, query, ip=None):
        """Génère un lien de streaming via StremThru"""
        link = super().get_stream_link(query, ip)
        logger.debug(f"Offcloud (via StremThru): Lien de streaming généré: {link is not None}")
        return link
