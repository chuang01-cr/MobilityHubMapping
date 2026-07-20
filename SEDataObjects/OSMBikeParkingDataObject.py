import pathlib
from typing import Callable
import geopandas as gpd
import numpy as np
import osmnx as ox
from pandas.core.api import Series as Series
import shapely
import folium

from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.utils import basic_circle_marker, filter_two_corresponding_arrays, small_geodesic_polygons_to_points, transform_shapely_geometry

from .SpatialDataObject import SpatialDataObject

BIKE_PARKING_FIELDS = ["bicycle_parking", "capacity", "covered"]
BIKE_PARKING_ALIASES = ["Facility Type", "Capacity", "Covered?"]

class OSMBikeParkingDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "osm_bike_parking"

    def __init__(self, cache_path: (str | pathlib.Path), tags, max_point_size: int = 100): # TODO: not sure of type for tags so using any
        self.cache_path = cache_path
        self.tags = tags
        self.max_point_size = max_point_size

    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        old_cache_path = ox.settings.cache_folder
        ox.settings.cache_folder = self.cache_path
        gdf_osm_result = ox.features_from_polygon(transform_shapely_geometry(load_area_crs, GEODESIC_CRS, load_area), self.tags)
        ox.settings.cache_folder = old_cache_path
        gdf_osm_result.geometry = gdf_osm_result.geometry.map(
            lambda geom: small_geodesic_polygons_to_points(geom, self.max_point_size)
        )
        self._gdf = gpd.GeoDataFrame(
            gdf_osm_result[np.intersect1d(BIKE_PARKING_FIELDS, gdf_osm_result.columns)],
            geometry=gdf_osm_result.geometry
        )
        self._set_is_loaded()

    def get_folium_plot(self):
        fields, aliases = filter_two_corresponding_arrays(
            self._gdf.columns,
            BIKE_PARKING_FIELDS,
            BIKE_PARKING_ALIASES,
        )
        osm_popup = folium.GeoJsonPopup(
            fields=fields,
            aliases=aliases,
            localize=True,
            labels=True,
        )
        osm_geojson = folium.GeoJson(
            self._gdf,
            marker=basic_circle_marker("red"),
            style_function=lambda _: {
                "fillColor": "red",
                "color": "red",
            },
            popup=osm_popup,
        )
        return osm_geojson

