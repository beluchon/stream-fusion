import requests

from stream_fusion.utils.metdata.metadata_provider_base import MetadataProvider
from stream_fusion.utils.models.movie import Movie
from stream_fusion.utils.models.series import Series
from stream_fusion.settings import settings
from stream_fusion.logging_config import logger

class TMDB(MetadataProvider):
    def get_metadata(self, id, type):
        self.logger.info("Getting metadata for " + type + " with id " + id)

        full_id = id.split(":")

        result = None

        for lang in self.config['languages']:
            url = f"https://api.themoviedb.org/3/find/{full_id[0]}?api_key={settings.tmdb_api_key}&external_source=imdb_id&language={lang}"
            response = requests.get(url)
            data = response.json()
            logger.trace(data)

            if lang == self.config['languages'][0]:
                if type == "movie":
                    if data.get("movie_results") and data["movie_results"]:
                        result = Movie(
                            id=id,
                            tmdb_id=data["movie_results"][0]["id"],
                            titles=[self.replace_weird_characters(data["movie_results"][0]["title"])],
                            year=data["movie_results"][0]["release_date"][:4],
                            languages=self.config['languages']
                        )
                    else:
                        logger.warning(f"No movie results found on TMDB for {id}")
                        return None
                else:
                    if data.get("tv_results") and data["tv_results"]:
                        # Vérifier si la saison et l'épisode sont spécifiés dans l'ID
                        season = "S01"  # Valeur par défaut
                        episode = "E01"  # Valeur par défaut
                        
                        # Si le format est id:saison:episode
                        if len(full_id) > 1:
                            try:
                                season = "S{:02d}".format(int(full_id[1]))
                                if len(full_id) > 2:
                                    episode = "E{:02d}".format(int(full_id[2]))
                            except (ValueError, IndexError) as e:
                                logger.warning(f"Erreur lors du traitement de la saison/épisode pour {id}: {e}")
                        
                        result = Series(
                            id=id,
                            tmdb_id = data["tv_results"][0]["id"],
                            titles=[self.replace_weird_characters(data["tv_results"][0]["name"])],
                            season=season,
                            episode=episode,
                            languages=self.config['languages']
                        )
                    else:
                        logger.warning(f"No TV results found on TMDB for {id}")
                        return None
            else:
                if type == "movie":
                    if data.get("movie_results") and data["movie_results"]:
                        result.titles.append(self.replace_weird_characters(data["movie_results"][0]["title"]))
                else:
                    if data.get("tv_results") and data["tv_results"]:
                        result.titles.append(self.replace_weird_characters(data["tv_results"][0]["name"]))

        self.logger.info("Got metadata for " + type + " with id " + id)
        return result
