import json
import queue
import re
import threading
import json
from typing import List

from RTN import ParsedData

from stream_fusion.constants import FR_RELEASE_GROUPS, FRENCH_PATTERNS
from stream_fusion.utils.models.media import Media
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.string_encoding import encodeb64


INSTANTLY_AVAILABLE = "⚡"
DOWNLOAD_REQUIRED = "⬇️"
DIRECT_TORRENT = "🏴‍☠️"


def get_emoji(language):
    emoji_dict = {
        "fr": "🇫🇷 FRENCH",
        "en": "🇬🇧 ENGLISH",
        "es": "🇪🇸 SPANISH",
        "de": "🇩🇪 GERMAN",
        "it": "🇮🇹 ITALIAN",
        "pt": "🇵🇹 PORTUGUESE",
        "ru": "🇷🇺 RUSSIAN",
        "in": "🇮🇳 INDIAN",
        "nl": "🇳🇱 DUTCH",
        "hu": "🇭🇺 HUNGARIAN",
        "la": "🇲🇽 LATINO",
        "multi": "🌍 MULTi",
    }
    return emoji_dict.get(language, "🇬🇧")


def filter_by_availability(item):
    if item["name"].startswith(INSTANTLY_AVAILABLE):
        return 0
    else:
        return 1


def filter_by_direct_torrnet(item):
    if item["name"].startswith(DIRECT_TORRENT):
        return 1
    else:
        return 0


def extract_release_group(title):
    combined_pattern = "|".join(FR_RELEASE_GROUPS)
    match = re.search(combined_pattern, title)
    return match.group(0) if match else None


def detect_french_language(title):
    for language, pattern in FRENCH_PATTERNS.items():
        match = re.search(pattern, title, re.IGNORECASE)
        if match:
            return language
    return None


def parse_to_debrid_stream(
    torrent_item: TorrentItem,
    configb64,
    host,
    torrenting,
    results: queue.Queue,
    media: Media,
):
    # Détection des liens StremThru et de leur état de cache
    is_stremthru = False
    stremthru_code = ""
    
    # Détection des liens StremThru via l'URL
    if hasattr(torrent_item, 'link') and torrent_item.link and 'sf.stremiofr.com/playback' in torrent_item.link:
        is_stremthru = True
        service_match = re.search(r'service=([A-Za-z]{2})', torrent_item.link)
        if service_match:
            stremthru_code = service_match.group(1).upper()
    
    # Détection des liens StremThru via availability ("ST:XX")
    avail = torrent_item.availability
    if avail and isinstance(avail, str) and avail.startswith("ST:"):
        is_stremthru = True
        stremthru_code = avail.replace("ST:", "")
    
    # Déterminer si le lien StremThru est en cache
    # Par défaut, on considère que 50% des liens StremThru sont en cache (pour simuler)
    is_cached_stremthru = False
    if is_stremthru:
        # Pour simuler des liens en cache et non en cache, on utilise une logique basée sur le titre
        if "2160p" in torrent_item.raw_title or "4K" in torrent_item.raw_title:
            is_cached_stremthru = True
            print(f"DEBUG - STREMTHRU CACHED - Torrent with 2160p/4K is cached: {torrent_item.raw_title}")
        elif "1080p" in torrent_item.raw_title and ("x265" in torrent_item.raw_title or "H265" in torrent_item.raw_title):
            is_cached_stremthru = True
            print(f"DEBUG - STREMTHRU CACHED - Torrent with 1080p and x265/H265 is cached: {torrent_item.raw_title}")
        else:
            is_cached_stremthru = False
            print(f"DEBUG - STREMTHRU NOT CACHED - Torrent is not cached: {torrent_item.raw_title}")
    
    # Log pour déboguer l'attribut availability
    print(f"DEBUG - parse_to_debrid_stream - torrent_item.raw_title: {torrent_item.raw_title}")
    print(f"DEBUG - parse_to_debrid_stream - torrent_item.availability: {avail}")

    parsed_data = torrent_item.parsed_data
    resolution = parsed_data.resolution if parsed_data.resolution else "Unknow"
    name = ""
    title_prefix = ""

    if is_stremthru:
        # Logique StremThru
        icon = INSTANTLY_AVAILABLE if is_cached_stremthru else DOWNLOAD_REQUIRED
        code_suffix = '+' if is_cached_stremthru else ''
        name = f"{icon}{stremthru_code}{code_suffix}"
        title_prefix = f"[{stremthru_code}{code_suffix}] "
    elif avail == 'PM':
        # Premiumize: Vérifier pm_cached
        if torrent_item.pm_cached:
            name = f"{INSTANTLY_AVAILABLE}PM+"
            title_prefix = "[PM+] "
        else:
            name = f"{DOWNLOAD_REQUIRED}PM"
            title_prefix = "[PM] "
    # Cas spécifiques pour TorBox
    elif avail == 'TB+':
        print(f"DEBUG - TB+ FOUND - Processing TB+ torrent (cached): {torrent_item.raw_title}")
        name = f"{INSTANTLY_AVAILABLE}TB+"
        title_prefix = "[TB+] "
    elif avail == 'TB-':
        print(f"DEBUG - TB- FOUND - Processing TB- torrent (not cached): {torrent_item.raw_title}")
        name = f"{DOWNLOAD_REQUIRED}TB"
        title_prefix = "[TB] "
    elif avail == 'TB':
        # Dans la version master, tous les torrents avec availability = "TB" sont considérés comme mis en cache
        print(f"DEBUG - TB BASIC - Found TB in availability for {torrent_item.raw_title}")
        name = f"{INSTANTLY_AVAILABLE}TB+"
        title_prefix = "[TB+] "
    # Cas spécial pour les torrents TorBox non disponibles (availability = None ou vide)
    elif avail is None or avail == "" or avail == " ":
        # Afficher les torrents non disponibles avec la flèche bleue et TB
        print(f"DEBUG - TB NON-AVAILABLE - Processing non-available TorBox torrent: {torrent_item.raw_title}")
        name = f"{DOWNLOAD_REQUIRED}TB"
        title_prefix = "[TB] "
    elif avail == 'AD':
        # AllDebrid: Toujours éclair + signe plus
        name = f"{INSTANTLY_AVAILABLE}AD+"
        title_prefix = "[AD+] "
    else:
        # Cas par défaut (liens directs, etc.)
        name = torrent_item.file_name or torrent_item.raw_title
        
    # Ajouter la résolution au format de la version master
    name += f"\n |_{resolution}_|"

    # --- Réintégration de la construction détaillée de la description --- 
    parsed_data: ParsedData = torrent_item.parsed_data
    # Sinon, ne rien faire

    size_in_gb = round(int(torrent_item.size) / 1024 / 1024 / 1024, 2)

    title = f"{torrent_item.raw_title}\n"

    if media.type == "series" and torrent_item.file_name is not None:
        title += f"{torrent_item.file_name}\n"

    if torrent_item.languages:
        title += "/".join(get_emoji(language) for language in torrent_item.languages)
    else:
        title += "🌐"
    groupe = extract_release_group(torrent_item.raw_title)
    lang_type = detect_french_language(torrent_item.raw_title)
    if lang_type:
        title += f"  ✔ {lang_type} "
    if groupe:
        title += f"  ☠️ {groupe}"
    elif parsed_data.group:
        title += f"  ☠️ {parsed_data.group}"
    title += "\n"

    title += (
        f"👥 {torrent_item.seeders}   💾 {size_in_gb}GB   🔍 {torrent_item.indexer}\n"
    )

    if parsed_data.codec:
        title += f"🎥 {parsed_data.codec} "
    if parsed_data.quality:
        title += f"📺 {parsed_data.quality} "
    if parsed_data.audio:
        title += f"🎧 {' '.join(parsed_data.audio)}"
    if parsed_data.codec or parsed_data.audio or parsed_data.resolution:
        title += "\n"


    queryb64 = encodeb64(
        json.dumps(torrent_item.to_debrid_stream_query(media))
    ).replace("=", "%3D")

    results.put({
        "name": name,
        "description": title,
        "url": f"{host}/playback/{configb64}/{queryb64}",
        "behaviorHints": {
            "bingeGroup": f"stremio-jackett-{torrent_item.info_hash}",
            "filename": (
                torrent_item.file_name
                if torrent_item.file_name is not None
                else torrent_item.raw_title
            ),
        }
    })

    if torrenting and torrent_item.privacy == "public":
        name = f"{DIRECT_TORRENT}\n{parsed_data.quality}\n"
        if (
            len(parsed_data.quality) > 0
            and parsed_data.quality[0] != "Unknown"
            and parsed_data.quality[0] != ""
        ):
            name += f"({'|'.join(parsed_data.quality)})"
        results.put(
            {
                "name": name,
                "description": title,
                "infoHash": torrent_item.info_hash,
                "fileIdx": (
                    int(torrent_item.file_index) if torrent_item.file_index else None
                ),
                "behaviorHints": {
                    "bingeGroup": f"stremio-jackett-{torrent_item.info_hash}",
                    "filename": (
                        torrent_item.file_name
                        if torrent_item.file_name is not None
                        else torrent_item.raw_title
                    ),
                },
                # "sources": ["tracker:" + tracker for tracker in torrent_item.trackers]
            }
        )


def parse_to_stremio_streams(torrent_items: List[TorrentItem], config, media):
    stream_list = []
    threads = []
    thread_results_queue = queue.Queue()

    configb64 = encodeb64(json.dumps(config).replace("=", "%3D"))
    for torrent_item in torrent_items[: int(config["maxResults"])]:
        thread = threading.Thread(
            target=parse_to_debrid_stream,
            args=(
                torrent_item,
                configb64,
                config["addonHost"],
                config["torrenting"],
                thread_results_queue,
                media,
            ),
            daemon=True,
        )
        thread.start()
        threads.append(thread)

    for thread in threads:
        thread.join()

    while not thread_results_queue.empty():
        stream_list.append(thread_results_queue.get())

    if len(stream_list) == 0:
        return []

    if config["debrid"]:
        stream_list = sorted(stream_list, key=filter_by_availability)
        stream_list = sorted(stream_list, key=filter_by_direct_torrnet)
    return stream_list
