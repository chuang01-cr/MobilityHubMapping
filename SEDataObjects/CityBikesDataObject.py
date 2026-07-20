from typing import Callable
import folium
from folium.features import GeoJson
import numpy as np
import pandas as pd
import geopandas as gpd
import shapely
from SEDataObjects import SpatialDataObject, constants
from SEDataObjects.utils import basic_circle_marker, download_json_safely, filter_two_corresponding_arrays, transform_shapely_geometry

COUNTRY_CODE_US = "US"

CITYBIKES_FIELDS = ["system_name", "system", "station_name", 'capacity', "has_ebikes"]
CITYBIKES_ALIASES = ["System Name", "Operator", "Station Name", "Capacity", "Has Ebikes?"]

class CityBikesDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "citybikes_bikeshare_docks"

    def __init__(self, citybikes_url: str) -> None:
        self.citybikes_url = citybikes_url

    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        load_area_transformed = transform_shapely_geometry(load_area_crs, constants.GEODESIC_CRS, load_area)
        citybikes_feeds_json = download_json_safely(self.citybikes_url + "/v2/networks") #TODO: use urllib for this
        df_citybikes_feeds = pd.DataFrame.from_records(citybikes_feeds_json["networks"])
        df_citybikes_feeds["country"] = df_citybikes_feeds["location"].map(lambda x: x["country"])
        df_citybikes_feeds_us = df_citybikes_feeds.loc[df_citybikes_feeds["country"] == COUNTRY_CODE_US]
        df_citybikes_feeds_us["latitude"] = df_citybikes_feeds_us["location"].map(lambda x: x["latitude"]).copy()
        df_citybikes_feeds_us["longitude"] = df_citybikes_feeds_us["location"].map(lambda x: x["longitude"]).copy()
        gdf_citybikes_feeds_us = gpd.GeoDataFrame(
            df_citybikes_feeds_us,
            geometry=gpd.points_from_xy(df_citybikes_feeds_us["longitude"], df_citybikes_feeds_us["latitude"]),
            crs=constants.GEODESIC_CRS
        )
        #TODO: potentially switch to using a large buffer on the citybikes points, to avoid missing any feeds
        def download_feed(feed_href: str):
            feed_json = download_json_safely(self.citybikes_url + feed_href) #TODO: switch to using urllib for this
            try:
                return feed_json["network"]["stations"]
            except KeyError as e:
                print(f"{feed_href} pointed to an incorrectly formatted feed. Error below:")
                print(e)
                return np.nan
        gdf_citybikes_feeds_in_load_area = gdf_citybikes_feeds_us.loc[gdf_citybikes_feeds_us.within(load_area)]
        gdf_citybikes_feeds_in_load_area["stations"] = gdf_citybikes_feeds_in_load_area["href"].map(
            download_feed
        )
        df_citybikes_stations = gdf_citybikes_feeds_in_load_area.explode("stations", ignore_index=True)
        df_citybikes_stations = df_citybikes_stations[df_citybikes_stations["stations"].apply(lambda x: isinstance(x, dict))]
        df_citybikes_stations["longitude"] = df_citybikes_stations["stations"].map(lambda x: x["longitude"]).copy()
        df_citybikes_stations["latitude"] = df_citybikes_stations["stations"].map(lambda x: x["latitude"]).copy()
        df_citybikes_stations["station_name"] = df_citybikes_stations["stations"].map(lambda x: x["name"]).copy()
        df_citybikes_stations["free_bikes"] = df_citybikes_stations["stations"].map(lambda x: x["free_bikes"]).copy()
        df_citybikes_stations["empty_slots"] = df_citybikes_stations["stations"].map(lambda x: x["empty_slots"]).copy()
        df_citybikes_stations["has_ebikes"] = df_citybikes_stations["stations"].map(lambda x: "unknown" if "extra" not in x or "has_ebikes" not in x["extra"] else x["extra"]["has_ebikes"]
    ).copy()
        df_citybikes_stations["capacity"] = df_citybikes_stations["empty_slots"] + df_citybikes_stations["free_bikes"]
        gdf_citybikes_stations = gpd.GeoDataFrame(
            df_citybikes_stations,
            geometry=gpd.points_from_xy(df_citybikes_stations["longitude"], df_citybikes_stations["latitude"]),
            crs=constants.GEODESIC_CRS
        )
        gdf_citybikes_stations_to_save = gdf_citybikes_stations.rename(columns={"name": "system_name"}).loc[
            gdf_citybikes_stations.within(load_area_transformed)
        ]
        self._gdf = gpd.GeoDataFrame(
            gdf_citybikes_stations_to_save[CITYBIKES_FIELDS],
            geometry=gdf_citybikes_stations_to_save.geometry)
        self._set_is_loaded()

    def get_folium_plot(self) -> GeoJson:
        fields, aliases = filter_two_corresponding_arrays(self._gdf.columns, CITYBIKES_FIELDS, CITYBIKES_ALIASES)
        citybikes_popup = folium.GeoJsonPopup(
            fields=fields,
            aliases=aliases
        )
        return folium.GeoJson(
            self._gdf,
            popup=citybikes_popup,
            marker=basic_circle_marker("green")
        )