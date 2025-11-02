import json

from stream_fusion.utils.string_encoding import decodeb64
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings

def parse_config(b64config):
    config = json.loads(decodeb64(b64config))

    if "languages" not in config:
        config["languages"] = [config["language"]]
    if "jackett" not in config:
        config["jackett"] = False
    if isinstance(config.get("RDToken"), str):
        try:
            config["RDToken"] = json.loads(config["RDToken"])
        except json.JSONDecodeError:
            pass
    if "anonymizeMagnets" not in config:
        config["anonymizeMagnets"] = False
    if "addonHost" not in config:
        logger.warning("addonHost not found in config, using default")
        config["addonHost"] = "http://127.0.0.1:8000"

    # Forcer zilean=false si désactivé au niveau système
    if not settings.zilean_enabled:
        config["zilean"] = False
        logger.debug("Zilean forced to False: disabled at system level")

    return config
