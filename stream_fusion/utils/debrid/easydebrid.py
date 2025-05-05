from stream_fusion.utils.debrid.stremthru import StremThru
from stream_fusion.logging_config import logger


class EasyDebrid(StremThru):
    def __init__(self, config):
        super().__init__(config)
        self.name = "EasyDebrid"
        self.extension = "ED"
        
        # Récupérer la clé API d'EasyDebrid
        self.api_key = config.get("easydebrid_api_key", "")
        
        # Configurer StremThru pour utiliser EasyDebrid
        self.set_store_credentials("easydebrid", self.api_key)
        
    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        """Vérifie la disponibilité des torrents en masse via StremThru"""
        results = super().get_availability_bulk(hashes_or_magnets, ip)
        logger.debug(f"EasyDebrid (via StremThru): {len(results)} torrents en cache trouvés")
        return results
    
    def add_magnet(self, magnet, ip=None):
        """Ajoute un magnet à EasyDebrid via StremThru"""
        result = super().add_magnet(magnet, ip)
        logger.debug(f"EasyDebrid (via StremThru): Magnet ajouté avec succès: {result is not None}")
        return result
    
    def get_stream_link(self, query, ip=None):
        """Génère un lien de streaming via StremThru"""
        link = super().get_stream_link(query, ip)
        logger.debug(f"EasyDebrid (via StremThru): Lien de streaming généré: {link is not None}")
        return link
