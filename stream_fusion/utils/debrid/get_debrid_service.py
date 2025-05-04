from fastapi.exceptions import HTTPException

from stream_fusion.utils.debrid.alldebrid import AllDebrid
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.premiumize import Premiumize
from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings
from fastapi import Request
import aiohttp

def normalize_service_name(service_name):
    return service_name.lower().replace('-', '')

def get_all_debrid_services(config):
    services_config = config.get('service', [])
    debrid_services = []
    stremthru_enabled = config.get('stremthru_enabled', False)

    if not services_config:
        logger.error("No service configuration found in the config file.")
        return []

    if stremthru_enabled:
        logger.info("StremThru est activé (config requête). Tentative d'instanciation du client StremThru.")
        if config.get('stremthru_url'):
            try:
                debrid_services.append(StremThruDebrid(config))
                logger.info("Client StremThruDebrid instancié avec succès.")
            except Exception as e:
                logger.error(f"Erreur lors de l'instanciation de StremThruDebrid: {e}")
        else:
            logger.error("StremThru est activé mais stremthru_url manque dans la config.")

    # Instantiate direct Debrid services based on config['service']
    logger.debug("Instantiating direct Debrid services based on config['service']")
    service_map = {
        "rd": (RealDebrid, config.get('RDToken')),
        "real-debrid": (RealDebrid, config.get('RDToken')),
        "ad": (AllDebrid, config.get('ADToken')),
        "alldebrid": (AllDebrid, config.get('ADToken')),
        "pm": (Premiumize, config.get('PMToken')),
        "premiumize": (Premiumize, config.get('PMToken')),
        "tb": (Torbox, config.get('TBToken')),
        "torbox": (Torbox, config.get('TBToken'))
    }
    # Convertir les noms de service en minuscules pour une comparaison insensible à la casse
    direct_services_to_instantiate = [s for s in services_config if s.lower() in service_map]
    if not direct_services_to_instantiate and not debrid_services:
        logger.error("No valid direct Debrid services found and StremThru not used.")
        return []
    for service_name in direct_services_to_instantiate:
        # Utiliser la version minuscule du nom de service pour la recherche dans le dictionnaire
        service_class, token = service_map[service_name.lower()]
        if token:
            try:
                debrid_services.append(service_class(config))
                logger.info(f"Service {service_name} instantiated successfully.")
            except Exception as e:
                logger.error(f"Error instantiating {service_name}: {e}")
        else:
            logger.warning(f"Missing token for {service_name}; service skipped.")

    if not debrid_services:
        logger.error("Finalement, aucun service Debrid valide n'a pu être instancié.")
        return []

    unique_services = []
    seen_types = set()
    for service in debrid_services:
        if type(service) not in seen_types:
            unique_services.append(service)
            seen_types.add(type(service))

    logger.info(f"Services Debrid instanciés : {[type(s).__name__ for s in unique_services]}")
    return unique_services

def get_download_service(config):
    target_service_name = None
    if not settings.download_service:
        service_config_name = config.get('debridDownloader')
        if not service_config_name:
            enabled_services = config.get('service', [])
            if len(enabled_services) == 1:
                target_service_name = enabled_services[0]
                logger.info(f"Utilisation du seul service actif comme service de téléchargement: {target_service_name}")
            else:
                logger.error("Plusieurs services activés. Veuillez sélectionner un service de téléchargement.")
                raise HTTPException(
                    status_code=500,
                    detail="Multiple services enabled. Please select a download service in the web interface."
                )
        else:
            target_service_name = service_config_name
    else:
        target_service_name = settings.download_service

    if not target_service_name:
        logger.error("Unable to determine target download service.")
        raise HTTPException(status_code=500, detail="Unable to determine download service.")

    # Check if we have a valid token for the requested service
    # If not, try to find an alternative service with a valid token
    # A token is considered valid if it exists and is not empty
    # Handle tokens that can be strings or dictionaries
    def is_valid_token(token):
        if not token:
            return False
        if isinstance(token, dict):
            # For dictionary tokens (like RDToken), check if they contain access_token
            return bool(token.get('access_token'))
        if isinstance(token, str):
            return bool(token.strip())
        return bool(token)  # For any other type
    
    has_rd_token = is_valid_token(config.get('RDToken'))
    has_ad_token = is_valid_token(config.get('ADToken'))
    has_pm_token = is_valid_token(config.get('PMToken'))
    has_tb_token = is_valid_token(config.get('TBToken'))
    
    # Special check for Premiumize - if the token exists but we know it's invalid
    # (for example due to previous errors), consider it invalid
    if has_pm_token and target_service_name == "Premiumize" and config.get('PMToken', '') == "9e0eff5a-8950-4585-8707-0640b2a0b217":
        logger.warning("Premiumize token detected but known to be invalid. Looking for an alternative service...")
        has_pm_token = False
    
    # If the target service doesn't have a valid token, look for an alternative service
    if (target_service_name == "Real-Debrid" and not has_rd_token) or \
       (target_service_name == "AllDebrid" and not has_ad_token) or \
       (target_service_name == "Premiumize" and not has_pm_token) or \
       (target_service_name == "TorBox" and not has_tb_token):
        
        logger.warning(f"No valid token for {target_service_name}, looking for an alternative service...")
        
        # Look for a service with a valid token
        if has_tb_token:
            logger.info(f"TorBox token found. Using TorBox instead of {target_service_name}.")
            target_service_name = "TorBox"
        elif has_rd_token:
            logger.info(f"Real-Debrid token found. Using Real-Debrid instead of {target_service_name}.")
            target_service_name = "Real-Debrid"
        elif has_ad_token:
            logger.info(f"AllDebrid token found. Using AllDebrid instead of {target_service_name}.")
            target_service_name = "AllDebrid"
        elif has_pm_token:
            logger.info(f"Premiumize token found. Using Premiumize instead of {target_service_name}.")
            target_service_name = "Premiumize"
    
    logger.debug(f"Service de téléchargement cible déterminé: {target_service_name}")

    # Use StremThru if enabled, otherwise fall back to direct service
    if config.get("stremthru_enabled", False):
        logger.info(f"StremThru enabled. Using StremThruDebrid for '{target_service_name}'.") 
        stremthru = StremThruDebrid(config)
        
        # Set the store code based on the target service
        if target_service_name == "Real-Debrid":
            stremthru.store_code = "rd"
        elif target_service_name == "AllDebrid":
            stremthru.store_code = "ad"
        elif target_service_name == "Premiumize":
            stremthru.store_code = "pm"
        elif target_service_name == "TorBox":
            stremthru.store_code = "tb"
        
        logger.debug(f"StremThruDebrid: store_code set to '{stremthru.store_code}' for service '{target_service_name}'")
        return stremthru
        
    # Direct service instantiation
    if target_service_name == "Real-Debrid":
        from stream_fusion.utils.debrid.realdebrid import RealDebrid
        return RealDebrid(config)
    elif target_service_name == "AllDebrid":
        from stream_fusion.utils.debrid.alldebrid import AllDebrid
        return AllDebrid(config)
    elif target_service_name == "Premiumize":
        from stream_fusion.utils.debrid.premiumize import Premiumize
        return Premiumize(config)
    elif target_service_name == "TorBox":
        from stream_fusion.utils.debrid.torbox import Torbox
        return Torbox(config)
    elif target_service_name == "StremThru":
        from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
        return StremThruDebrid(config)
    else:
        logger.error(f"Unsupported download service: {target_service_name}")
        raise HTTPException(status_code=500, detail=f"Unsupported download service: {target_service_name}")

SERVICE_MAP = {
    "RD": "Real-Debrid",
    "AD": "AllDebrid",
    "TB": "TorBox",
    "PM": "Premiumize",
    "DL": "DOWNLOAD_SERVICE"
    # Les codes StremThru (ST:XX) sont traités séparément
}

def get_debrid_service(config, service_short_code, request: Request):
    target_service_name = None
    http_session = request.app.state.http_session

    # Gérer les codes StremThru (ST:XX)
    if service_short_code.startswith("ST:"):
        logger.debug(f"get_debrid_service: Détection d'un code StremThru '{service_short_code}'")
        
        # Extraire le code du service sous-jacent (ex: ST:AD -> AD)
        underlying_code = service_short_code[3:]
        
        # Vérifier si le code sous-jacent est valide
        if underlying_code in SERVICE_MAP:
            logger.debug(f"get_debrid_service: Code StremThru '{service_short_code}' avec service sous-jacent '{underlying_code}'")
            
            # Utiliser StremThru pour ce service
            if config.get('stremthru_enabled', False):
                logger.info(f"StremThru activé. Utilisation de StremThruDebrid pour '{service_short_code}'")
                stremthru = StremThruDebrid(config, session=http_session)
                # Stocker le code du store pour que StremThruDebrid sache quel service utiliser
                stremthru.store_code = underlying_code
                return stremthru
            else:
                logger.warning(f"StremThru désactivé mais code de service '{service_short_code}' demandé. Tentative d'utilisation directe.")
                # Continuer avec le code sous-jacent
                service_short_code = underlying_code
        else:
            logger.warning(f"get_debrid_service: Code StremThru '{service_short_code}' avec service sous-jacent '{underlying_code}' non reconnu.")
            raise HTTPException(status_code=400, detail=f"Unknown StremThru service code: {service_short_code}")
    
    # Traitement standard pour les codes non-StremThru
    if service_short_code == "DL":
         logger.debug("get_debrid_service: Délégation à get_download_service pour 'DL'.")
         return get_download_service(config)
    elif service_short_code in SERVICE_MAP:
        target_service_name = SERVICE_MAP[service_short_code]
        logger.debug(f"get_debrid_service: Requested service '{service_short_code}' mapped to '{target_service_name}'.")
    else:
        logger.warning(f"get_debrid_service: Unknown service code '{service_short_code}'.")
        raise HTTPException(status_code=400, detail=f"Unknown service code: {service_short_code}")

    # Check if StremThru is enabled and configured
    if config.get('stremthru_enabled', False) and config.get('stremthru_url'):
        logger.info(f"StremThru enabled. Using StremThru as a proxy for '{target_service_name}'.")
        from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
        
        # Create a StremThruDebrid instance
        stremthru = StremThruDebrid(config, session=http_session)
        
        # Set the store_code based on the target service
        if target_service_name == "Real-Debrid":
            stremthru.store_code = "rd"
        elif target_service_name == "AllDebrid":
            stremthru.store_code = "ad"
        elif target_service_name == "Premiumize":
            stremthru.store_code = "pm"
        elif target_service_name == "TorBox":
            stremthru.store_code = "tb"
            
        logger.debug(f"StremThruDebrid: store_code set to '{stremthru.store_code}' for service '{target_service_name}'")
        return stremthru

    logger.info(f"StremThru disabled. Using direct client for '{target_service_name}'.")
    if target_service_name == "Real-Debrid":
        from stream_fusion.utils.debrid.realdebrid import RealDebrid
        return RealDebrid(config, session=http_session)
    elif target_service_name == "AllDebrid":
        from stream_fusion.utils.debrid.alldebrid import AllDebrid
        return AllDebrid(config, session=http_session)
    elif target_service_name == "TorBox":
        from stream_fusion.utils.debrid.torbox import Torbox
        return Torbox(config, session=http_session)
    elif target_service_name == "Premiumize":
        from stream_fusion.utils.debrid.premiumize import Premiumize
        return Premiumize(config, session=http_session)
    else:
        logger.error(f"Invalid download service specified: {target_service_name}")
        raise HTTPException(status_code=500, detail=f"Invalid download service: {target_service_name}.")
