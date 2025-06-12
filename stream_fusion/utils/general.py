from datetime import datetime, timezone
from typing import Optional, List, Dict
from RTN import parse
import re

from stream_fusion.logging_config import logger

video_formats = {".mkv", ".mp4", ".avi", ".mov", ".flv", ".wmv", ".webm", ".mpg", ".mpeg", ".m4v", ".3gp", ".3g2",
                 ".ogv",
                 ".ogg", ".drc", ".gif", ".gifv", ".mng", ".avi", ".mov", ".qt", ".wmv", ".yuv", ".rm", ".rmvb", ".asf",
                 ".amv", ".m4p", ".m4v", ".mpg", ".mp2", ".mpeg", ".mpe", ".mpv", ".mpg", ".mpeg", ".m2v", ".m4v",
                 ".svi", ".3gp", ".3g2", ".mxf", ".roq", ".nsv", ".flv", ".f4v", ".f4p", ".f4a", ".f4b"}


def season_episode_in_filename(filename, season, episode):
    if not is_video_file(filename):
        return False

    parsed_name = parse(filename)

    return season in parsed_name.seasons and episode in parsed_name.episodes


def smart_episode_fallback(files: List[Dict], season: int, episode: int) -> Optional[Dict]:
    """
    Fallback intelligent pour trouver l'épisode correct quand RTN échoue.
    Essaie plusieurs stratégies avant de prendre le plus gros fichier.
    """
    if not files:
        return None
    
    video_files = [f for f in files if is_video_file(f.get("name", ""))]
    if not video_files:
        return None
    
    logger.debug(f"Smart fallback: Recherche S{season:02d}E{episode:02d} parmi {len(video_files)} fichiers")
    
    episode_patterns = [
        rf"[Ss]{season:02d}[Ee]{episode:02d}",  # S01E01
        rf"[Ss]{season}[Ee]{episode:02d}",      # S1E01  
        rf"{season:02d}x{episode:02d}",         # 01x01
        rf"{season}x{episode:02d}",             # 1x01
        rf"[Ee]{episode:02d}",                  # E01 (si saison unique)
        rf"[Ee]pisode.{episode:02d}",           # Episode 01
        rf"\.{episode:02d}\.",                  # .01.
    ]
    
    for pattern in episode_patterns:
        for file in video_files:
            filename = file.get("name", "")
            if re.search(pattern, filename, re.IGNORECASE):
                logger.debug(f"Smart fallback: Match trouvé avec pattern '{pattern}': {filename}")
                return file
    
    sorted_files = sorted(video_files, key=lambda f: f.get("name", "").lower())
    if episode <= len(sorted_files):
        selected_file = sorted_files[episode - 1]  # Index 0-based
        logger.debug(f"Smart fallback: Sélection par ordre alphabétique (épisode #{episode}): {selected_file.get('name')}")
        return selected_file
    
    largest_file = max(video_files, key=lambda f: f.get("size", 0))
    logger.warning(f"Smart fallback: Aucune stratégie n'a fonctionné, sélection du plus gros fichier: {largest_file.get('name')}")
    return largest_file


def get_info_hash_from_magnet(magnet: str):
    exact_topic_index = magnet.find("xt=")
    if exact_topic_index == -1:
        logger.debug(f"No exact topic in magnet {magnet}")
        return None

    exact_topic_substring = magnet[exact_topic_index:]
    end_of_exact_topic = exact_topic_substring.find("&")
    if end_of_exact_topic != -1:
        exact_topic_substring = exact_topic_substring[:end_of_exact_topic]

    info_hash = exact_topic_substring[exact_topic_substring.rfind(":") + 1:]

    return info_hash.lower()


def is_video_file(filename):
    extension_idx = filename.rfind(".")
    if extension_idx == -1:
        return False

    return filename[extension_idx:] in video_formats


# Utility functions for timestamp conversion
def datetime_to_timestamp(dt: Optional[datetime]) -> Optional[int]:
    return int(dt.timestamp()) if dt is not None else None

def timestamp_to_datetime(ts: Optional[int]) -> Optional[datetime]:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None
