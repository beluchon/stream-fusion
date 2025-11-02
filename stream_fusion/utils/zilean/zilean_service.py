import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Union, Dict, Tuple, Optional
from functools import lru_cache
import time

from stream_fusion.logging_config import logger
from stream_fusion.utils.models.movie import Movie
from stream_fusion.utils.models.series import Series
from stream_fusion.settings import settings
from stream_fusion.utils.zilean.zilean_api import ZileanAPI, DMMQueryRequest, DMMTorrentInfo

class ZileanService:
    def __init__(self, config):
        self.zilean_api = ZileanAPI()
        self.logger = logger
        self.max_workers = settings.zilean_max_workers
        self._search_cache: Dict[str, Tuple[List[DMMTorrentInfo], float]] = {}
        self._cache_ttl = 3600  # 1 heure en secondes

    def search(self, media: Union[Movie, Series]) -> List[DMMTorrentInfo]:
        # Vérification : si Zilean désactivé au niveau système, retourner liste vide
        if not settings.zilean_enabled:
            logger.debug("Zilean search skipped: disabled at system level")
            return []

        # Vérification : si l'instance est marquée comme disabled
        if hasattr(self.zilean_api, 'disabled') and self.zilean_api.disabled:
            logger.debug("Zilean search skipped: API instance is disabled")
            return []

        # Vérifier si nous avons déjà des résultats en cache pour ce média
        cache_key = self._get_cache_key(media)
        cached_results = self._get_from_cache(cache_key)
        if cached_results:
            return cached_results

        # Sinon, effectuer la recherche
        if isinstance(media, Movie):
            results = self.__search_movie(media)
        elif isinstance(media, Series):
            results = self.__search_series(media)
        else:
            raise TypeError("Only Movie and Series are allowed as media!")

        # Stocker les résultats dans le cache
        self._add_to_cache(cache_key, results)
        return results

    def _get_cache_key(self, media: Union[Movie, Series]) -> str:
        """Génère une clé de cache unique pour un média."""
        if isinstance(media, Movie):
            return f"movie:{media.id}:{media.titles[0] if media.titles else ''}"
        elif isinstance(media, Series):
            season = getattr(media, 'season', '')
            episode = getattr(media, 'episode', '')
            return f"series:{media.id}:{media.titles[0] if media.titles else ''}:{season}:{episode}"

    def _get_from_cache(self, cache_key: str) -> Optional[List[DMMTorrentInfo]]:
        """Récupère les résultats du cache s'ils existent et sont valides."""
        if cache_key in self._search_cache:
            results, timestamp = self._search_cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                return results
            # Nettoyer les entrées expirées
            del self._search_cache[cache_key]
        return None

    def _add_to_cache(self, cache_key: str, results: List[DMMTorrentInfo]) -> None:
        """Ajoute des résultats au cache avec un timestamp."""
        self._search_cache[cache_key] = (results, time.time())
        
        # Nettoyer le cache si trop volumineux (garder max 50 entrées)
        if len(self._search_cache) > 50:
            # Supprimer la plus ancienne entrée
            oldest_key = min(self._search_cache.keys(), key=lambda k: self._search_cache[k][1])
            del self._search_cache[oldest_key]

    def __deduplicate_api_results(self, api_results: List[DMMTorrentInfo]) -> List[DMMTorrentInfo]:
        unique_results = set()
        deduplicated_results = []
        for result in api_results:
            result_tuple = (
                result.raw_title,
                result.info_hash,
                result.size,
            )
            if result_tuple not in unique_results:
                unique_results.add(result_tuple)
                deduplicated_results.append(result)
        return deduplicated_results

    def __remove_duplicate_titles(self, titles: List[str]) -> List[str]:
        seen = set()
        return [title for title in titles if not (title.lower() in seen or seen.add(title.lower()))]

    def __search_movie(self, movie: Movie) -> List[DMMTorrentInfo]:
        unique_titles = self.__remove_duplicate_titles(movie.titles)
        
        # Recherche par IMDb ID d'abord (souvent plus précise)
        imdb_results = self.__search_by_imdb_id(movie.id)
        
        # Si nous avons suffisamment de résultats IMDb, nous pouvons éviter des recherches supplémentaires
        if len(imdb_results) >= 10:
            return imdb_results
            
        # Sinon, compléter avec des recherches par titre
        keyword_results = self.__threaded_search_movie(unique_titles)
        
        # Combiner et dédupliquer les résultats
        all_results = imdb_results + keyword_results
        return self.__deduplicate_api_results(all_results)

    def __search_series(self, series: Series) -> List[DMMTorrentInfo]:
        unique_titles = self.__remove_duplicate_titles(series.titles)
        
        # Recherche par IMDb ID d'abord (souvent plus précise)
        imdb_results = self.__search_by_imdb_id(series.id)
        
        # Si nous avons suffisamment de résultats IMDb, nous pouvons éviter des recherches supplémentaires
        if len(imdb_results) >= 10:
            return imdb_results
            
        # Sinon, compléter avec des recherches par titre
        keyword_results = self.__threaded_search_series(unique_titles, series)
        
        # Combiner et dédupliquer les résultats
        all_results = imdb_results + keyword_results
        return self.__deduplicate_api_results(all_results)

    def __threaded_search_movie(self, search_texts: List[str]) -> List[DMMTorrentInfo]:
        results = []
        if not search_texts:
            return results
            
        # Limite le nombre de titres à rechercher pour éviter trop de requêtes
        search_texts = search_texts[:3]  
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_text = {executor.submit(self.__make_movie_request, text): text for text in search_texts}
            for future in as_completed(future_to_text):
                try:
                    results.extend(future.result())
                except Exception as e:
                    self.logger.exception(f"Error in threaded movie search: {str(e)}")
        return results

    def __threaded_search_series(self, search_texts: List[str], series: Series) -> List[DMMTorrentInfo]:
        results = []
        if not search_texts:
            return results
            
        # Limite le nombre de titres à rechercher pour éviter trop de requêtes
        search_texts = search_texts[:3]
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_text = {executor.submit(self.__make_series_request, text, series): text for text in search_texts}
            for future in as_completed(future_to_text):
                try:
                    results.extend(future.result())
                except Exception as e:
                    self.logger.exception(f"Error in threaded series search: {str(e)}")
        return results

    def __make_movie_request(self, query_text: str) -> List[DMMTorrentInfo]:
        try:
            return self.zilean_api.dmm_search(DMMQueryRequest(queryText=query_text))
        except Exception as e:
            self.logger.exception(f"An exception occurred while searching for movie '{query_text}' on Zilean: {str(e)}")
            return []

    def __make_series_request(self, query_text: str, series: Series) -> List[DMMTorrentInfo]:
        try:
            season = getattr(series, 'season', None)
            episode = getattr(series, 'episode', None)
            
            if season is not None:
                season = season.lstrip('S') if isinstance(season, str) else season
            if episode is not None:
                episode = episode.lstrip('E') if isinstance(episode, str) else episode
            
            return self.zilean_api.dmm_filtered(
                query=query_text,
                season=season,
                episode=episode
            )
        except Exception as e:
            self.logger.exception(f"An exception occurred while searching for series '{query_text}' on Zilean: {str(e)}")
            return []

    def __search_by_imdb_id(self, imdb_id: str) -> List[DMMTorrentInfo]:
        try:
            return self.zilean_api.dmm_filtered(imdb_id=imdb_id)
        except Exception as e:
            self.logger.exception(f"An exception occurred while searching for IMDb ID '{imdb_id}' on Zilean: {str(e)}")
            return []
