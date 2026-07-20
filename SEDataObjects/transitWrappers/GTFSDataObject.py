from dataclasses import dataclass
import hashlib
from typing import Callable
import typing
import fiona
import folium
import numpy as np
import pytz
import requests
import shapely
from SEDataObjects import SpatialDataObject
import datetime as dt
import pandas as pd
import geopandas as gpd
import pathlib

from SEDataObjects.transitWrappers.constants import CONFIG_CLUSTERING_ENABLED, MODE_COLOR_MAP, NO_MODE, Mode, ModeClassification
from SEDataObjects.transitWrappers import TransitNetwork
from SEDataObjects.transitWrappers.FeedWrapper import FeedWrapper
from SEDataObjects.utils import basic_circle_marker, download_file_with_playwright, download_file_with_requests, download_latest_feed_version_from_transitland, filter_two_corresponding_arrays, get_str_or_na, safe_is_na, transform_shapely_geometry, yes_no_to_bool
from SEDataObjects.constants import GEODESIC_CRS

GTFS_FEEDS_FIELDS_TO_STORE = [
    "name",
    "agency_url",
    "url",
    "raw_feed_path",
    "processed_file_path",
    "last_fetched",
    #"last_valid_date",
    "attribution_url",
    "attribution_text",
    "attribution_instructions",
    "attribution_must_attribute",
    "last_fetch_succeeded",
]


GTFS_STOPS_FIELDS_TO_DISPLAY_BASE = [
    "min_overlap_headway",
    "total_frequency",
    "transfer",
    "mode",
    "mode_classification",
]

GTFS_STOPS_FIELDS_TO_DISPLAY_CLUSTERING = [
    "clustering_id",
    *GTFS_STOPS_FIELDS_TO_DISPLAY_BASE,
]

GTFS_STOPS_FIELDS_TO_DISPLAY_NO_CLUSTERING = [
    "stop_id_unique",
    *GTFS_STOPS_FIELDS_TO_DISPLAY_BASE
]
GTFS_ALIASES = [
    "Stop ID", 
    "Minimum Headway (one direction)", 
    "Total Frequency (all directions)", 
    "Transfer?", 
    "Mode", 
    "Mode Classification"
]

@dataclass
class DownloadResponse:
    response_success: bool
    output_path: pathlib.Path | None
    sha1_hash: typing.Any

MAX_RESPONSES_PER_PAGE = 100
MAX_CHUNK_SIZE = 65536
MAX_CALLS = 100
MIN_TRIPS = 5

class GTFSDataObject(SpatialDataObject):
    df_feeds_metadata = None
    load_area = None
    _gdf = gpd.GeoDataFrame()
    name = "transitland_gtfs_stops"

    def __init__(
        self,
        local_crs: int,
        gtfs_cache_path: str | pathlib.Path,
        transitland_url: str,
        api_key_path: str,
        gtfs_cache_life: dt.timedelta = dt.timedelta(days=7),
        gtfs_override_feeds_path: None | str | pathlib.Path = None,
        download_transitland_first: bool = True,
        load_from_cache: bool = True,
        save_to_cache:bool = True,
        **network_config
    ) -> None:
        """
        A DataObject with points representing transit stops. Loads feeds from TransitLand and processes them with other library functions

        :param local_crs: An EPSG number representing a projected CRS that has units in meters and is valid for the area to be loaded
        :param gtfs_cache_path: A path to a folder for saving GTFS feeds and processed data
        :param transitland_url: An API to the Transitland V2 feeds api. 
            See https://www.transit.land/documentation/rest-api/feeds
        :param api_key_path: A path to a file containing a Transitland/Interline api key.
            See https://www.transit.land/documentation#signing-up-for-an-api-key
        :param gtfs_cache_life: A timedelta cotaining the length to save cached stops for, default is 7 days
        :param download_transitland_first: Whether to try using Transitland to download a feed over simulating a browser page with Playwright if the initial attempt to download a feed fails
            Defaults to True, which will speed loading and avoid any issues with working against protections against automated downloading, 
            but increases API calls and may lead to downloading outdated feeds in certain cases
        :param load_from_cache: Whether to attempt to load cached stops and metadata, defaults to True. Will not check if configuration matches the cached files
        :param save_to_cache: Whether to save stop locations, stop metadata, and feed metadata to the cache, defaults to True
        :param network_config: Any config to pass to TransitNetwork. See readme for a complete list
        """
        self.local_crs = local_crs
        self.gtfs_cache_path = pathlib.Path(gtfs_cache_path).resolve()
        if gtfs_override_feeds_path is not None:
            self.gtfs_override_feeds_path = pathlib.Path(gtfs_override_feeds_path).resolve()
        else:
            self.gtfs_override_feeds_path = None
        self.transitland_url = transitland_url
        self.gtfs_cache_life = gtfs_cache_life
        #TODO: add api calls to download gtfs files
        self.all_gtfs_paths = None
        self.transitland_last_queried = None
        with open(api_key_path) as f:
            self.api_key = f.read()
        self.download_transitland_first = download_transitland_first
        self.load_from_cache = load_from_cache
        self.save_to_cache = save_to_cache
        self.network_config = network_config
        self.clustering_enabled = True if (CONFIG_CLUSTERING_ENABLED not in network_config) else network_config[CONFIG_CLUSTERING_ENABLED]

    async def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        """Loads the data object for the area defined by load_area with the provided EPSG crs"""
        pd.set_option('future.no_silent_downcasting', True)
        load_area_transformed = transform_shapely_geometry(load_area_crs, GEODESIC_CRS, load_area)
        # Query Transitland
        transitland_feeds = self._recursively_make_transitland_call(
            MAX_RESPONSES_PER_PAGE,
            load_area_transformed.bounds,
            MAX_CALLS
        )
        df_feeds_metadata = pd.DataFrame
        if self.gtfs_override_feeds_path is not None:
            df_override_feeds = pd.read_csv(self.gtfs_override_feeds_path, index_col=0)
        else:
            df_override_feeds = pd.DataFrame(index=[])
        feeds_metadata_path = self.gtfs_cache_path / "feeds.csv"
        stops_geometry_path = self.gtfs_cache_path / "processed_stops.geojson"
        try:
            df_feeds_metadata = pd.read_csv(
                feeds_metadata_path,
                index_col=0
            )
        except FileNotFoundError:
            print("INFO: Did not load feeds metadata, generating a new file")
            df_feeds_metadata = pd.DataFrame(
                columns=GTFS_FEEDS_FIELDS_TO_STORE,
            )
        df_feeds_metadata["last_fetched"] = pd.to_datetime(df_feeds_metadata["last_fetched"])
        datetime_today = dt.datetime.now()

        # Check whether the cache is up to date
        get_from_cache = (
            self.load_from_cache
            and df_feeds_metadata.size != 0
            and self.gtfs_cache_life > (datetime_today - df_feeds_metadata["last_fetched"].min(skipna=True))
        )
        if get_from_cache:
            self._network = None
            gdf_stops_raw = gpd.read_file(stops_geometry_path)
            self._gdf = self._convert_from_geojson(gdf_stops_raw)
            self._set_is_loaded()
            return

        network = TransitNetwork(config=self.network_config, local_crs=self.local_crs)
        for feed_metadata in transitland_feeds:
            feed_id = feed_metadata["onestop_id"]
            print(f"INFO: Processing {feed_id}")
            # Get the most recent currently valid feed
            current_feed_version = feed_metadata["feed_state"]["feed_version"]
            if not current_feed_version:
                df_feeds_metadata.loc[feed_id, "last_fetch_succeeded"] = False
                continue 
            response = None
            cached_fetch_status = False
            # Check whether there is always a valid file downloaded
            if feed_id in df_feeds_metadata.index:
                cached_feed_metadata = df_feeds_metadata.loc[feed_id]
                cached_last_downloaded = cached_feed_metadata["last_fetched"]
                cached_fetch_status = cached_feed_metadata["last_fetch_succeeded"]
            if self.load_from_cache and cached_fetch_status and (datetime_today - cached_last_downloaded > self.gtfs_cache_life):
                response = (True, df_feeds_metadata.loc["raw_feed_path"], df_feeds_metadata.loc["sha1_hash"])
            else:
            # Download the feed and update the feed metadata
                feed_url = current_feed_version["url"]
                response = await self._download_feed(feed_id, feed_url, df_override_feeds)
                if not response.response_success:
                    print(f"WARN: Download for {feed_id} failed")
                    continue
            # Validate the hash, but do not fail (hashes will not match if transitland hasn't cached the feed recently)
            feed_output_path = response.output_path
            if (
                response.sha1_hash is not None 
                and response.sha1_hash.hexdigest() != current_feed_version["sha1"]
            ):
                print(
                    f"WARN: For {feed_metadata['onestop_id']}, the hash {response.sha1_hash.hexdigest()} does not match the provided hash from Transitland {current_feed_version['sha1']}"
                )
            
            feed_last_fetched = datetime_today
            #feed_end_of_life_dt = dt.datetime.fromisoformat(df_current_feed_version["latest_calendar_date"])
            feed_attribution_url = get_str_or_na(feed_metadata["license"]["url"])
            feed_attribution_text = get_str_or_na(feed_metadata["license"]["attribution_text"])
            feed_attribution_instructions = get_str_or_na(feed_metadata["license"]["attribution_instructions"])
            feed_must_attribute = yes_no_to_bool(feed_metadata["license"]["use_without_attribution"])
            

            # Load the feed object
            print(f"Loading {feed_id}")
            print(f"DEBUG: Attempting to open file path: {feed_output_path}")
            try:
                feed_object = FeedWrapper(feed_output_path, feed_id, load_area, load_area_crs, MIN_TRIPS)
                if not feed_object.get_feed_loaded_correctly():
                    continue
            except Exception as e:
                print(f"Skipping feed {feed_id} because it encountered an error: {e}")
                continue
            
            feed_name = feed_object.get_agency_name()
            print(f"FEED NAME: {feed_name}")
            feed_agency_url = feed_object.get_agency_url()
            #df_feeds_metadata.loc[feed_id] = {
                #"name": feed_name,
                #agency_url": feed_agency_url,
                #"url": feed_url,
                #"raw_feed_path": feed_output_path,
                #"last_fetched": feed_last_fetched,
                ##"last_valid_date": feed_end_of_life_dt,
                #"attribution_url": feed_attribution_url,
                #"attribution_text": feed_attribution_text,
                #"attribution_instructions": feed_attribution_instructions,
                #"attribution_must_attribute": feed_must_attribute,
                #"last_fetch_succeeded": True,
                #"sha1_hash": response.sha1_hash
            #}
            pass
            # Add the feed to the network
            network.add_feed(feed_object)
        if self.clustering_enabled:
            gdf_stop_locations = network.gdf_stops_clustered
        else:
            gdf_stop_locations = network.gdf_stops
        self._network = network
        #df_route_summary = network.get_summary_routes_df()
        gdf_stop_locations["min_overlap_headway"] = network.weighted_headways_by_stop_overlap.groupby(level=0).min()
        gdf_stop_locations["total_frequency"] = network.weighted_frequencies_by_stop_overlap.groupby(level=0).sum()
        gdf_stop_locations["transfer"] = network.transfer_status
        gdf_stop_locations["mode"] = network.mode_by_stop
        gdf_stop_locations["mode_classification"] = network.mode_classification_by_stop
        
        self.df_feeds_metadata = df_feeds_metadata
        # Save the stops, excluding any that do not have service associated with them
        #TODO: there should be a funciton to perform this filter in TransitNetwork
        self._gdf = gdf_stop_locations#.dropna(subset=["mode"])
        gdf_stop_locations_saveable = self.gdf
        # Save stops and feed metadata to file
        df_feeds_metadata.to_csv(feeds_metadata_path)
        gdf_stop_locations_saveable.to_file(stops_geometry_path)
        self._set_is_loaded()

    def get_scores(self) -> pd.Series:
        raise NotImplementedError

    def get_score_decay_function(self) -> Callable[[float], float]:
        raise NotImplementedError

    def get_folium_plot(self) -> folium.GeoJson:
        """Get a folium geojson object"""
        fields_to_display = GTFS_STOPS_FIELDS_TO_DISPLAY_CLUSTERING if self.clustering_enabled else GTFS_STOPS_FIELDS_TO_DISPLAY_NO_CLUSTERING
        gtfs_popup = folium.GeoJsonPopup(
            fields=fields_to_display,
            aliases=GTFS_ALIASES,
        )
        gtfs_geojson = folium.GeoJson(
            self.gdf.reset_index()[
                [*fields_to_display, self._gdf.geometry.name]
            ],
            popup=gtfs_popup,
            marker=basic_circle_marker("orange"),
            style_function=lambda x: {
                "fillColor": MODE_COLOR_MAP[Mode(x["properties"]["mode"])]
            },
        )
        return gtfs_geojson

    async def _download_feed(self, feed_id, feed_url, df_override_feeds):
        #TODO: implement df_override_feeds to load a cached feed if it is not available from TransitLand
        feed_output_path = self.gtfs_cache_path / f"gtfs_{feed_id}.zip"
        # Download the feed
        try:
            sha1_hash = download_file_with_requests(feed_url, feed_output_path, MAX_CHUNK_SIZE)
        except requests.HTTPError:
            sha1_hash = None
        except requests.exceptions.MissingSchema:
            print(f"WARN: URL {feed_url} is invalid. Skipping")
            return DownloadResponse(False, None, None)
        # If download fails and object is configured to download the cached feed from transitland first:
        if sha1_hash is None and self.download_transitland_first:
            # Download the cached feed from transitland
            sha1_hash = download_latest_feed_version_from_transitland(
                feed_id, feed_output_path, MAX_CHUNK_SIZE, self.api_key
            )
        # If download has still failed, try downloading the feed using Playwright
        if sha1_hash is None:
            print("INFO: Requests download failed. Will try Playwright")
            sha1_hash = await download_file_with_playwright(feed_url, 
                feed_output_path, feed_id, MAX_CHUNK_SIZE)
        # If download has still failed, try downloading the feed using Requests
        if sha1_hash is None and not self.download_transitland_first:
            sha1_hash = download_latest_feed_version_from_transitland(
                feed_id, feed_output_path, MAX_CHUNK_SIZE, self.api_key
            )
        # If the download has still failed, skip this feed
        if sha1_hash is None:
            print(f"WARN: Download for {feed_id} failed even with Playwright")
            return DownloadResponse(False, None, None)
        return DownloadResponse(True, feed_output_path, sha1_hash)

    def _recursively_make_transitland_call(self, max_responses, initial_load_area_bounds, max_calls):
        # Call transitland recursively with a smaller bounding box until it doesn't give an error
        def make_transitland_call(max_responses, load_area_bounds, after = None, max_calls=None):
            if max_calls is not None and max_calls <= 0:
                raise RecursionError("Max Transitland calls exceeded")
            stringified_bounds = ",".join(map(lambda x: str(x), load_area_bounds))
            transitland_url = f"{self.transitland_url}?bbox={stringified_bounds}&limit={max_responses}&license_create_derived_product=exclude_no&license_redistribution_allowed=exclude_no&apikey={self.api_key}"
            if after is not None:
                transitland_url += f"&after={after}"
            print(f"INFO: Transitland URL: {transitland_url}")
            transitland_response = requests.get(transitland_url)
            transitland_response.raise_for_status()
            transitland_json = transitland_response.json()
            new_max_calls = None if max_calls is None else max_calls - 1
            if "meta" in transitland_json:
                additional_feeds, returned_max_calls = make_transitland_call(
                    max_responses,
                    load_area_bounds,
                    after=transitland_json["meta"]["after"],
                    max_calls=new_max_calls,
                )
                return transitland_json["feeds"] + additional_feeds, returned_max_calls
            return transitland_response.json()["feeds"], new_max_calls

        def recursively_make_transitland_call_help(max_responses, initial_load_area_bounds, max_calls):
            if max_calls == 0:
                raise RecursionError("Max Transitland calls exceeded")
            try:
                returned_feeds, _ = make_transitland_call(max_responses, initial_load_area_bounds)
                return returned_feeds, max_calls - 1
            except requests.exceptions.HTTPError as e:
                if e.response.status_code != 500:
                    raise(e)
                bounds_center = (
                    (initial_load_area_bounds[0] + initial_load_area_bounds[2]) / 2,
                    (initial_load_area_bounds[1] + initial_load_area_bounds[3]) / 2,
                )
                quadrant_one = (
                    bounds_center[0],
                    bounds_center[1],
                    initial_load_area_bounds[2],
                    initial_load_area_bounds[3],
                )
                quadrant_two = (
                    initial_load_area_bounds[0],
                    bounds_center[1],
                    bounds_center[0],
                    initial_load_area_bounds[3],
                )
                quadrant_three = (
                    initial_load_area_bounds[0], 
                    initial_load_area_bounds[1],
                    bounds_center[0], 
                    bounds_center[1],
                )
                quadrant_four = (
                    bounds_center[0],
                    initial_load_area_bounds[1],
                    initial_load_area_bounds[2],
                    bounds_center[1],
                )
                feeds = []
                current_max_calls = max_calls - 1
                for quadrant in (quadrant_one, quadrant_two, quadrant_three, quadrant_four):
                    returned_feeds, returned_max_calls = recursively_make_transitland_call_help(max_responses, quadrant, current_max_calls)
                    feeds.append(returned_feeds)
                    current_max_calls = returned_max_calls
                all_feeds_with_duplicates = np.concatenate(feeds)
                unique_feed_indices = np.unique(
                    [feed["onestop_id"] for feed in all_feeds_with_duplicates],
                    return_index = True
                )[1]
                output = all_feeds_with_duplicates[unique_feed_indices]
                return output, current_max_calls
        output, _ = recursively_make_transitland_call_help(max_responses, initial_load_area_bounds, max_calls)
        return output
    
    @property
    def gdf_with_enums(self):
        return self._gdf.copy()

    @property
    def gdf(self):
        if not self.get_is_loaded:
            raise RuntimeError("Must load data object before the gdf can be obtained")
        gdf_copy = self._gdf.copy()
        gdf_copy[["mode", "mode_classification"]] = gdf_copy[
            ["mode", "mode_classification"]
        ].map(lambda x: NO_MODE if safe_is_na(x) else  x.value)
        gdf_copy["transfer"] = gdf_copy["transfer"].fillna("NA").map({
            True: "True",
            False: "False",
        })
        return gdf_copy
    
    @staticmethod
    def _convert_from_geojson(gdf):
        gdf_copy = gdf.copy()
        gdf_copy["mode"] = gdf_copy["mode"].map(
            lambda x: np.nan if x == NO_MODE or safe_is_na(x) else Mode(x)
        )
        gdf_copy["mode_classification"] = gdf_copy["mode_classification"].map(
            lambda x: np.nan if x == NO_MODE or safe_is_na(x) else ModeClassification(x)
        )
        gdf_copy["transfer"] = gdf_copy["transfer"].map({
            "True": True,
            "False": False,
            "NA": np.nan
        })
        return gdf_copy

