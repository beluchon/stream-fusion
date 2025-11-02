import requests
from typing import List, Optional, Tuple, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from stream_fusion.settings import settings
from stream_fusion.logging_config import logger
import time

class DMMQueryRequest(BaseModel):
    queryText: Optional[str] = None

class DMMImdbFile(BaseModel):
    imdbId: Optional[str] = None
    category: Optional[str] = None
    title: Optional[str] = None
    adult: Optional[bool] = None
    year: Optional[int] = None

class DMMImdbSearchResult(BaseModel):
    title: Optional[str] = None
    imdbId: Optional[str] = None
    year: int = 0
    score: float = 0.0
    category: Optional[str] = None

class DMMTorrentInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    info_hash: str
    raw_title: str
    size: str
    parsed_title: Optional[str] = None
    normalized_title: Optional[str] = None
    trash: Optional[bool] = None
    year: Optional[int] = None
    resolution: Optional[str] = None
    seasons: Tuple[int, ...] = Field(default_factory=tuple)
    episodes: Tuple[int, ...] = Field(default_factory=tuple)
    complete: Optional[bool] = None
    volumes: Tuple[int, ...] = Field(default_factory=tuple)
    languages: Tuple[str, ...] = Field(default_factory=tuple)
    quality: Optional[str] = None
    hdr: Tuple[str, ...] = Field(default_factory=tuple)
    codec: Optional[str] = None
    audio: Tuple[str, ...] = Field(default_factory=tuple)
    channels: Tuple[str, ...] = Field(default_factory=tuple)
    dubbed: Optional[bool] = None
    subbed: Optional[bool] = None
    date: Optional[str] = None
    group: Optional[str] = None
    edition: Optional[str] = None
    bit_depth: Optional[str] = None
    bitrate: Optional[str] = None
    network: Optional[str] = None
    extended: Optional[bool] = None
    converted: Optional[bool] = None
    hardcoded: Optional[bool] = None
    region: Optional[str] = None
    ppv: Optional[bool] = None
    three_d: Optional[bool] = Field(None, alias='_3d')
    site: Optional[str] = None
    proper: Optional[bool] = None
    repack: Optional[bool] = None
    retail: Optional[bool] = None
    upscaled: Optional[bool] = None
    remastered: Optional[bool] = None
    unrated: Optional[bool] = None
    documentary: Optional[bool] = None
    episode_code: Optional[str] = None
    country: Optional[str] = None
    container: Optional[str] = None
    extension: Optional[str] = None
    torrent: Optional[bool] = None
    category: Optional[str] = None
    imdb_id: Optional[str] = None
    imdb: Optional[DMMImdbFile] = None

class ZileanAPI:
    def __init__(
        self,
        pool_connections: int = settings.zilean_pool_connections,
        pool_maxsize: int = settings.zilean_api_pool_maxsize,
        max_retries: int = settings.zilean_max_retry,
    ):
        # Vérifier si Zilean est activé au niveau système
        if not settings.zilean_enabled:
            logger.info("Zilean is disabled via ZILEAN_ENABLED environment variable")
            self.disabled = True
            return

        self.disabled = False
        self.base_url = settings.zilean_url
        if not self.base_url:
            logger.error("Zilean API URL is not set in the environment variables.")
            raise ValueError("Zilean API URL is not set in the environment variables.")

        self.session = requests.Session()
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=0.1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"],
        )
        adapter = HTTPAdapter(
            pool_connections=pool_connections,
            pool_maxsize=pool_maxsize,
            max_retries=retry_strategy,
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Cache pour les résultats de recherche
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 900  # 15 minutes en secondes

    def _get_cache_key(self, method: str, endpoint: str, **kwargs) -> str:
        """Génère une clé de cache unique basée sur la méthode, l'endpoint et les paramètres."""
        params_str = "&".join(f"{k}={v}" for k, v in sorted(kwargs.items()) if v is not None)
        return f"{method}:{endpoint}:{params_str}"

    def _get_from_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Récupère les données du cache si elles existent et sont valides."""
        if cache_key in self._cache:
            cached_data = self._cache[cache_key]
            if time.time() - cached_data["timestamp"] < self._cache_ttl:
                return cached_data["data"]
            # Nettoyer les entrées expirées
            del self._cache[cache_key]
        return None

    def _add_to_cache(self, cache_key: str, data: Any) -> None:
        """Ajoute des données au cache avec un timestamp."""
        self._cache[cache_key] = {
            "data": data,
            "timestamp": time.time()
        }
        
        # Nettoyer le cache si trop volumineux (garder max 100 entrées)
        if len(self._cache) > 100:
            # Supprimer la plus ancienne entrée
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k]["timestamp"])
            del self._cache[oldest_key]

    def _request(self, method: str, endpoint: str, cache: bool = True, **kwargs):
        """
        Effectue une requête HTTP avec gestion de cache.
        
        :param method: Méthode HTTP (GET, POST, etc.)
        :param endpoint: Point d'accès API
        :param cache: Activer/désactiver le cache pour cette requête
        :param kwargs: Arguments supplémentaires pour la requête
        :return: Réponse HTTP
        """
        url = f"{self.base_url}{endpoint}"
        headers = kwargs.pop("headers", {})
        headers.update(
            {"accept": "application/json", "Content-Type": "application/json"}
        )
        
        # Vérifier le cache pour les requêtes GET si activé
        if cache and method.upper() == "GET":
            cache_key = self._get_cache_key(method, endpoint, **kwargs)
            cached_data = self._get_from_cache(cache_key)
            if cached_data:
                return type('CachedResponse', (), {'json': lambda: cached_data, 'text': str(cached_data)})
        
        try:
            response = self.session.request(method, url, headers=headers, **kwargs)
            response.raise_for_status()
            
            # Mettre en cache les résultats pour les requêtes GET si activé
            if cache and method.upper() == "GET":
                try:
                    cache_key = self._get_cache_key(method, endpoint, **kwargs)
                    self._add_to_cache(cache_key, response.json())
                except Exception as e:
                    logger.warning(f"Impossible de mettre en cache la réponse: {e}")
                    
            return response
        except requests.exceptions.RequestException as e:
            logger.error(f"Erreur lors de la requête API : {e}")
            raise

    def _convert_to_dmm_torrent_info(self, entry: dict) -> DMMTorrentInfo:
        for key in ['seasons', 'episodes', 'volumes', 'languages', 'hdr', 'audio', 'channels']:
            if key in entry and isinstance(entry[key], list):
                entry[key] = tuple(entry[key])
        if 'imdb' in entry and entry['imdb']:
            entry['imdb'] = DMMImdbFile(**entry['imdb'])
        return DMMTorrentInfo(**entry)

    def dmm_search(self, query: DMMQueryRequest) -> List[DMMTorrentInfo]:
        response = self._request("POST", "/dmm/search", json=query.dict())
        return [self._convert_to_dmm_torrent_info(entry) for entry in response.json()]

    def dmm_filtered(
        self,
        query: Optional[str] = None,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        year: Optional[int] = None,
        language: Optional[str] = None,
        resolution: Optional[str] = None,
        imdb_id: Optional[str] = None,
    ) -> List[DMMTorrentInfo]:
        params = {
            "Query": query,
            "Season": season,
            "Episode": episode,
            "Year": year,
            "Language": language,
            "Resolution": resolution,
            "ImdbId": imdb_id,
        }
        params = {k: v for k, v in params.items() if v is not None}
        response = self._request("GET", "/dmm/filtered", params=params)
        return [self._convert_to_dmm_torrent_info(entry) for entry in response.json()]

    def dmm_on_demand_scrape(self) -> None:
        self._request("GET", "/dmm/on-demand-scrape", cache=False)

    def healthchecks_ping(self) -> str:
        response = self._request("GET", "/healthchecks/ping", cache=False)
        return response.text

    def imdb_search(
        self, query: Optional[str] = None, year: Optional[int] = None, category: Optional[str] = None
    ) -> List[DMMImdbSearchResult]:
        params = {"Query": query, "Year": year, "Category": category}
        params = {k: v for k, v in params.items() if v is not None}
        response = self._request("POST", "/imdb/search", params=params)
        return [DMMImdbSearchResult(**file) for file in response.json()]

    def __del__(self):
        self.session.close()
