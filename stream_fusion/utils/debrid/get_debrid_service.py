from fastapi.exceptions import HTTPException

from stream_fusion.utils.debrid.alldebrid import AllDebrid
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.premiumize import Premiumize
from stream_fusion.utils.debrid.debridlink import DebridLink
from stream_fusion.utils.debrid.easydebrid import EasyDebrid
from stream_fusion.utils.debrid.offcloud import Offcloud
from stream_fusion.utils.debrid.pikpak import PikPak
from stream_fusion.utils.debrid.stremthru import StremThru
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings


def get_all_debrid_services(config):
    services = config['service']
    debrid_service = []
    if not services:
        logger.error("No service configuration found in the config file.")
        return []
    
    # Vérifier si StremThru est activé
    use_stremthru = config.get('stremthru', False)
    
    for service in services:
        if service == "Real-Debrid":
            if use_stremthru:
                # Utiliser StremThru pour Real-Debrid
                st = StremThru(config)
                st.set_store_credentials("realdebrid", config.get("RDToken", ""))
                st.extension = "ST:RD"  # Préfixe ST pour indiquer StremThru
                debrid_service.append(st)
                logger.debug("Real-Debrid (via StremThru): service added to be use")
            else:
                debrid_service.append(RealDebrid(config))
                logger.debug("Real-Debrid: service added to be use")
                
        if service == "AllDebrid":
            if use_stremthru:
                # Utiliser StremThru pour AllDebrid
                st = StremThru(config)
                st.set_store_credentials("alldebrid", config.get("ADToken", ""))
                st.extension = "ST:AD"
                debrid_service.append(st)
                logger.debug("AllDebrid (via StremThru): service added to be use")
            else:
                debrid_service.append(AllDebrid(config))
                logger.debug("AllDebrid: service added to be use")
                
        if service == "TorBox":
            if use_stremthru:
                # Utiliser StremThru pour TorBox
                st = StremThru(config)
                st.set_store_credentials("torbox", config.get("TBToken", ""))
                st.extension = "ST:TB"
                debrid_service.append(st)
                logger.debug("TorBox (via StremThru): service added to be use")
            else:
                debrid_service.append(Torbox(config))
                logger.debug("TorBox: service added to be use")
                
        if service == "Premiumize":
            if use_stremthru:
                # Utiliser StremThru pour Premiumize
                st = StremThru(config)
                st.set_store_credentials("premiumize", config.get("PMToken", ""))
                st.extension = "ST:PM"
                debrid_service.append(st)
                logger.debug("Premiumize (via StremThru): service added to be use")
            else:
                debrid_service.append(Premiumize(config))
                logger.debug("Premiumize: service added to be use")
                
        if service == "Debrid-Link":
            debrid_service.append(DebridLink(config))
            logger.debug("Debrid-Link: service added to be use")
            
        if service == "EasyDebrid":
            debrid_service.append(EasyDebrid(config))
            logger.debug("EasyDebrid: service added to be use")
            
        if service == "Offcloud":
            debrid_service.append(Offcloud(config))
            logger.debug("Offcloud: service added to be use")
            
        if service == "PikPak":
            debrid_service.append(PikPak(config))
            logger.debug("PikPak: service added to be use")
            
    if not debrid_service:
        raise HTTPException(status_code=500, detail="Invalid service configuration.")
    
    return debrid_service

def get_download_service(config):
    if not settings.download_service:
        service = config.get('debridDownloader')
        if not service:
            # Si aucun service n'est spécifié, utiliser le service activé
            services = config.get('service', [])
            if len(services) == 1:
                service = services[0]
                logger.info(f"Using active service as download service: {service}")
            else:
                logger.error("Multiple services enabled. Please select a download service in the web interface.")
                raise HTTPException(
                    status_code=500,
                    detail="Multiple services enabled. Please select a download service in the web interface."
                )
    else:
        service = settings.download_service
    
    # Vérifier si StremThru est activé
    use_stremthru = config.get('stremthru', False)
        
    if service == "Real-Debrid":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("realdebrid", config.get("RDToken", ""))
            return st
        return RealDebrid(config)
    elif service == "AllDebrid":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("alldebrid", config.get("ADToken", ""))
            return st
        return AllDebrid(config)
    elif service == "TorBox":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("torbox", config.get("TBToken", ""))
            return st
        return Torbox(config)
    elif service == "Premiumize":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("premiumize", config.get("PMToken", ""))
            return st
        return Premiumize(config)
    elif service == "Debrid-Link":
        return DebridLink(config)
    elif service == "EasyDebrid":
        return EasyDebrid(config)
    elif service == "Offcloud":
        return Offcloud(config)
    elif service == "PikPak":
        return PikPak(config)
    else:
        logger.error(f"Invalid download service: {service}")
        raise HTTPException(
            status_code=500,
            detail=f"Invalid download service: {service}. Please select a valid download service in the web interface."
        )


def get_debrid_service(config, service):
    if not service:
        service = settings.download_service
    
    # Vérifier si StremThru est activé
    use_stremthru = config.get('stremthru', False)
    
    if service == "RD":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("realdebrid", config.get("RDToken", ""))
            return st
        return RealDebrid(config)
    elif service == "AD":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("alldebrid", config.get("ADToken", ""))
            return st
        return AllDebrid(config)
    elif service == "TB":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("torbox", config.get("TBToken", ""))
            return st
        return Torbox(config)
    elif service == "PM":
        if use_stremthru:
            st = StremThru(config)
            st.set_store_credentials("premiumize", config.get("PMToken", ""))
            return st
        return Premiumize(config)
    elif service == "DL":
        return DebridLink(config)
    elif service == "ED":
        return EasyDebrid(config)
    elif service == "OC":
        return Offcloud(config)
    elif service == "PP":
        return PikPak(config)
    elif service == "ST":
        return get_download_service(config)
    else:
        logger.error("Invalid service configuration return by stremio in the query.")
        raise HTTPException(status_code=500, detail="Invalid service configuration return by stremio.")
