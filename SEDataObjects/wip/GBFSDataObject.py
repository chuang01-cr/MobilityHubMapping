# THIS IS NOT USED RIGHT NOW

import pathlib

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
import shapely
from shapely.geometry import MultiPolygon, Polygon
from SEDataObjects import SpatialDataObject, constants
from SEDataObjects.utils import safe_is_na, transform_shapely_geometry

COUNTRY_KEY = "COUNTRY",
SYSTEM_NAME_KEY = "SYSTEM_NAME"
LOCATION_KEY = "LOCATION"
FEED_URL_KEY = "FEED_URL"
VERSIONS_KEY = "VERSIONS"

COUNTRY_CODE_US = "US"

GBFS_SHEET_HEADERS = {
    COUNTRY_KEY: "Country Code",
    SYSTEM_NAME_KEY: "Name",
    LOCATION_KEY: "Location",
    FEED_URL_KEY: "Auto-Discovery URL",
    VERSIONS_KEY: "Supported Versions"
}

GBFS_SHEET_HEADERS_ORDER = [
    GBFS_SHEET_HEADERS[COUNTRY_KEY],
    GBFS_SHEET_HEADERS[SYSTEM_NAME_KEY],
    GBFS_SHEET_HEADERS[LOCATION_KEY],
    GBFS_SHEET_HEADERS[FEED_URL_KEY],
    GBFS_SHEET_HEADERS[VERSIONS_KEY]
]

class GBFSDataObject(SpatialDataObject):
    def __init__(
        self,
        feeds_csv_url: str,
        feeds_cache_path: pathlib.Path | str,
        states_geometry_path: pathlib.Path | str,
        state_code_header: str
    ) -> None:
        self.feeds_csv_url = feeds_csv_url
        self.feeds_cache_path = pathlib.Path(feeds_cache_path).resolve()
        self.states_geometry_path = pathlib.Path(states_geometry_path).resolve()
        self.state_code_header = state_code_header
    
    def load_data(self, load_area: MultiPolygon | Polygon | None, load_area_crs: int = 4326) -> None:
        # Get state codes in the load area
        gdf_states = gpd.read_file(self.states_geometry_path).to_crs(constants.GEOMETRIC_CRS)
        load_area_geometric =  transform_shapely_geometry(load_area_crs, constants.GEODESIC_CRS, load_area)
        state_codes_in_load_area = gdf_states.loc[gdf_states.contains(load_area_geometric), self.state_code_header]

        # Get feeds in the states of the load area
        df_feeds = pd.read_csv(self.feeds_csv_url)[GBFS_SHEET_HEADERS_ORDER]
        df_feeds_us = df_feeds.loc[df_feeds[GBFS_SHEET_HEADERS[COUNTRY_KEY]] == COUNTRY_CODE_US].copy()
        df_feeds_us["state"] = df_feeds_us[GBFS_SHEET_HEADERS[LOCATION_KEY]].str.split(",").map(
            lambda x: np.nan if safe_is_na(x) or len(x) < 2 else x[1]
        ).str.strip()
        
        df_feeds_in_load_area_state = df_feeds_us.loc[df_feeds_us["state"].isin(state_codes_in_load_area)].copy()
        df_feeds_in_load_area_state["gbfs.json"] = df_feeds_in_load_area_state[GBFS_SHEET_HEADERS[FEED_URL_KEY]].map(download_json_safely)
        
        
        