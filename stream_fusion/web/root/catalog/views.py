import asyncio
import pickle

from redis import Redis
from tmdbv3api import TMDb, Movie, TV, Season, Discover, Find
from fastapi_simple_rate_limiter import rate_limiter
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi_simple_rate_limiter.database import create_redis_session

from stream_fusion.services.postgresql.dao.apikey_dao import APIKeyDAO
from stream_fusion.settings import settings
from stream_fusion.utils.parse_config import parse_config
from stream_fusion.utils.security.security_api_key import check_api_key
from stream_fusion.utils.yggfilx.yggflix_api import YggflixAPI
from stream_fusion.web.root.catalog.schemas import (
    ErrorResponse,
    MetaItem,
    Metas,
    Meta,
    Video,
)
from stream_fusion.services.redis.redis_config import get_redis
from stream_fusion.logging_config import logger

router = APIRouter()

redis_session = create_redis_session(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db)

tmdb = TMDb()
tmdb.api_key = settings.tmdb_api_key
tmdb.language = "fr-FR"
movie = Movie()
tv = TV()
season = Season()
discover = Discover()
find = Find()


async def get_movie_details(tmdb_id):
    return await asyncio.to_thread(movie.details, tmdb_id)


async def get_tv_details(tmdb_id):
    return await asyncio.to_thread(tv.details, tmdb_id)


async def get_tv_season_details(tmdb_id, season_number):
    return await asyncio.to_thread(season.details, tmdb_id, season_number)


async def validate_config_and_api_key(config: str, apikey_dao: APIKeyDAO):
    config_data = parse_config(config)
    api_key = config_data.get("apiKey")
    if api_key:  
        await check_api_key(api_key, apikey_dao)
    return api_key


async def get_cached_item(redis_client: Redis, cache_key: str):
    cached_item = await asyncio.to_thread(redis_client.get, cache_key)
    if cached_item:
        return pickle.loads(cached_item)
    return None


async def cache_item(
    redis_client: Redis, cache_key: str, item, duration: int = 7 * 24 * 60 * 60
):
    await asyncio.to_thread(
        redis_client.set, cache_key, pickle.dumps(item), ex=duration
    )


def extract_year(date_string):
    if date_string and len(date_string) >= 4:
        return date_string[:4]
    return None


async def create_meta_object(details, item_type: str, imdb_id: str):
    meta = Meta(
        id=imdb_id,
        title=details.title if hasattr(details, "title") else details.name,
        type=item_type,
        poster=(
            f"https://image.tmdb.org/t/p/w500{details.poster_path}"
            if details.poster_path
            else None
        ),
        background=(
            f"https://image.tmdb.org/t/p/original{details.backdrop_path}"
            if details.backdrop_path
            else None
        ),
        country=(
            details.production_countries[0].name
            if details.production_countries
            else None
        ),
        tv_language=details.original_language,
        logo=None,
        genres=[genre.name for genre in details.genres] if details.genres else None,
        description=details.overview,
        runtime=(
            f"{str(details.runtime)} minutes" if item_type == "movie" and details.runtime else None
        ),
        website=details.homepage,
        imdb_rating=str(details.vote_average) if details.vote_average else None,
        year=extract_year(
            details.release_date if item_type == "movie" else details.first_air_date
        ),
    )

    if item_type == "movie":
        meta.stream = {
            "id": imdb_id
        }
    elif item_type == "series" and hasattr(details, "seasons"):
        meta.videos = []
        for season in details.seasons:
            season_details = await get_tv_season_details(
                details.id, season.season_number
            )
            for episode in season_details.episodes:
                meta.videos.append(
                    Video(
                        id=f"{imdb_id}:{season.season_number}:{episode.episode_number}",
                        title=episode.name,
                        released=str(episode.air_date),
                        season=season.season_number,
                        episode=episode.episode_number,
                    )
                )

    return meta


async def get_tmdb_id_from_imdb(imdb_id: str) -> str:
    results = await asyncio.to_thread(find.find_by_imdb_id, imdb_id)
    if results.movie_results:
        return results.movie_results[0]["id"]
    elif results.tv_results:
        return results.tv_results[0]["id"]
    return None


@router.get(
    "/{config}/catalog/{type}/{id}.json", responses={500: {"model": ErrorResponse}}
)
@router.get(
    "/{config}/catalog/{type}/{id}/skip={skip}.json", responses={500: {"model": ErrorResponse}}
)
@rate_limiter(limit=20, seconds=60, redis=redis_session)
async def get_catalog(
    config: str,
    type: str,
    id: str,
    request: Request,
    skip: int = 0,
    redis_client: Redis = Depends(get_redis),
    apikey_dao: APIKeyDAO = Depends()
):
    try:
        config_data = parse_config(config)
        api_key = config_data.get("apiKey")
        if api_key:  
            await check_api_key(api_key, apikey_dao)

        logger.debug(
            f"Received catalog request from api_key: {api_key}, type: {type}, id: {id}, config: {config_data}"
        )

        if type not in {"movie", "series"} or id not in {
            "latest_movies",
            "recently_added_movies",
            "popular_movies",
            "top_rated_movies",
            "latest_tv_shows",
            "recently_added_tv_shows",
            "popular_tv_shows",
            "top_rated_tv_shows",
        }:
            raise HTTPException(status_code=400, detail="Invalid type or catalog id")

        cache_key = f"catalog:{type}:{id}"
        cached_catalog = await get_cached_item(redis_client, cache_key)
        if cached_catalog:
            logger.info(f"Catalog found in cache for key: {cache_key}")
            full_catalog = Metas.model_validate(cached_catalog)
            return Metas(metas=full_catalog.metas[skip:])

        logger.info(f"Catalog not found in cache for key: {cache_key}. Generating...")

        item_ids = []
        use_yggflix = config_data.get("yggflix", False)
        yggflix_ids = {
            "latest_movies": "latest_movies",
            "recently_added_movies": "recently_added_movies",
            "latest_tv_shows": "latest_tv_shows",
            "recently_added_tv_shows": "recently_added_tv_shows",
        }

        if use_yggflix and id in yggflix_ids:
            try:
                logger.info(f"Attempting to fetch catalog from Yggflix for id: {yggflix_ids[id]}")
                yggflix = YggflixAPI()
                home_data = await asyncio.to_thread(yggflix.get_home)
                item_ids = [item["id"] for item in home_data.get(yggflix_ids[id], []) if "id" in item]
                logger.info(f"Fetched {len(item_ids)} IDs from Yggflix for {yggflix_ids[id]}")
            except Exception as ygg_error:
                logger.warning(f"Failed to fetch catalog from Yggflix for id {yggflix_ids[id]}: {ygg_error}. Falling back to TMDb.")
                item_ids = []

        if not item_ids:
            logger.info(f"Fetching catalog from TMDb for type: {type}, id: {id}")
            try:
                tmdb_params = {"page": 1}
                if type == "movie":
                    if id == "popular_movies": discover_func = discover.discover_movies
                    elif id == "top_rated_movies": discover_func = discover.discover_movies; tmdb_params['sort_by'] = 'vote_average.desc'
                    else: discover_func = discover.discover_movies

                    results = await asyncio.to_thread(discover_func, tmdb_params)
                else:
                    if id == "popular_tv_shows": discover_func = discover.discover_tv_shows
                    elif id == "top_rated_tv_shows": discover_func = discover.discover_tv_shows; tmdb_params['sort_by'] = 'vote_average.desc'
                    else: discover_func = discover.discover_tv_shows

                    results = await asyncio.to_thread(discover_func, tmdb_params)

                item_ids = [item.id for item in results if hasattr(item, 'id')]
                logger.info(f"Fetched {len(item_ids)} IDs from TMDb for {type}/{id}")

            except Exception as tmdb_error:
                 logger.error(f"Failed to fetch catalog from TMDb for {type}/{id}: {tmdb_error}", exc_info=True)
                 item_ids = []

        metas = []
        pipeline = redis_client.pipeline()

        process_limit = 50
        for tmdb_id in item_ids[:process_limit]:
            item_cache_key_tmdb = f"tmdbid_item:{tmdb_id}"
            cached_item = await get_cached_item(redis_client, item_cache_key_tmdb)

            if cached_item:
                try:
                    metas.append(Meta.model_validate(cached_item))
                except Exception as validation_error:
                     logger.warning(f"Failed to validate cached meta for TMDB ID {tmdb_id}: {validation_error}")
                continue

            try:
                if type == "movie":
                    details = await get_movie_details(tmdb_id)
                    item_type = "movie"
                    imdb_id = getattr(details, 'imdb_id', None)
                else:
                    details = await get_tv_details(tmdb_id)
                    item_type = "series"
                    external_ids = await asyncio.to_thread(tv.external_ids, tmdb_id)
                    imdb_id = external_ids.get("imdb_id")

                if not imdb_id:
                    logger.warning(f"No IMDb ID found for TMDB ID: {tmdb_id}")
                    continue

                meta = await create_meta_object(details, item_type, imdb_id)
                metas.append(meta)

                item_cache_key_imdb = f"imdbid_item:{imdb_id}"
                try:
                    pipeline.set(
                        item_cache_key_tmdb, pickle.dumps(meta), ex=7 * 24 * 60 * 60
                    )
                    pipeline.set(
                        item_cache_key_imdb, pickle.dumps(meta), ex=7 * 24 * 60 * 60
                    )
                    pipeline.set(
                        f"tmdbid_to_imdbid:{tmdb_id}", imdb_id, ex=7 * 24 * 60 * 60
                    )
                except Exception as cache_err:
                    logger.error(f"Error adding item TMDB:{tmdb_id}/IMDB:{imdb_id} to cache pipeline: {cache_err}")

            except Exception as e:
                logger.error(f"Error processing item with TMDB ID {tmdb_id}: {str(e)}")
                continue

        try:
            await asyncio.to_thread(pipeline.execute)
        except Exception as pipe_exec_err:
             logger.error(f"Error executing cache pipeline: {pipe_exec_err}")

        catalog = Metas(metas=metas)
        try:
            await cache_item(redis_client, cache_key, catalog, 1800)
        except Exception as cat_cache_err:
             logger.error(f"Error caching final catalog {cache_key}: {cat_cache_err}")

        logger.info(f"Catalog generated and cached for key: {cache_key} with {len(metas)} items.")
        return Metas(metas=catalog.metas[skip:])

    except Exception as e:
        logger.error(f"Catalog error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while processing the request."
        )


@router.get(
    "/{config}/meta/{type}/{id}.json", responses={500: {"model": ErrorResponse}}
)
@rate_limiter(limit=20, seconds=60, redis=redis_session)
async def get_meta(
    config: str,
    type: str,
    id: str,
    request: Request,
    redis_client: Redis = Depends(get_redis),
    apikey_dao: APIKeyDAO = Depends()
):
    try:
        config_data = parse_config(config)
        api_key = config_data.get("apiKey")
        if api_key:  
            await check_api_key(api_key, apikey_dao)

        logger.debug(
            f"Received meta request from api_key: {api_key}, type: {type}, id: {id}"
        )

        if type not in {"movie", "series"}:
            raise HTTPException(status_code=400, detail="Invalid type")

        cache_key = f"imdbid_item:{id}"
        cached_meta = await get_cached_item(redis_client, cache_key)
        if cached_meta:
            logger.info(f"Meta found in cache for IMDB ID: {id}")
            meta = Meta.model_validate(cached_meta)
            return MetaItem(meta=meta)

        logger.info(f"Meta not found in cache for IMDB ID: {id}, fetching from TMDB")

        tmdb_id = await get_tmdb_id_from_imdb(id)
        if not tmdb_id:
            logger.warning(f"No TMDB ID found for IMDB ID: {id}")
            raise HTTPException(status_code=404, detail="Item not found")

        if type == "movie":
            details = await get_movie_details(tmdb_id)
            item_type = "movie"
        else:
            details = await get_tv_details(tmdb_id)
            item_type = "series"

        logger.debug(f"Creating Meta object for {item_type} with IMDB ID: {id}")
        meta = await create_meta_object(details, item_type, id)

        await cache_item(redis_client, cache_key, meta)
        await cache_item(redis_client, f"tmdbid_item:{tmdb_id}", meta)
        await cache_item(redis_client, f"tmdbid_to_imdbid:{tmdb_id}", id)

        logger.info(f"Meta generated and cached for IMDB ID: {id}, TMDB ID: {tmdb_id}")
        return MetaItem(meta=meta)

    except Exception as e:
        logger.error(f"Meta error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred while processing the request."
        )
