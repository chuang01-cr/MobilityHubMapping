from typing import Callable
import pandas as pd
from SEDataObjects.utils import basic_circle_marker, filter_two_corresponding_arrays, transform_shapely_geometry
from .SpatialDataObject import SpatialDataObject
import geopandas as gpd
import shapely
import folium

from .constants import METERS_TO_MILES_FACTOR

AFDC_FIELDS = ["station_name", "street_address", "ev_network", "ev_network_web"]
AFDC_ALIASES = ["Name", "Address", "Network", "Website"]

class AFDCApiDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "afdc_ev_chargers"
    def __init__(self, source, api_key_path, local_projected_crs):
        # TODO: ping api url to make sure it works
        self.source = source
        self.api_key_path = api_key_path
        self.local_projected_crs = local_projected_crs

    def _call_afdc_api_ev_chargers(self, latitude: int, longitude: int, radius: float, limit: (int | None) = None):
        limit_field = limit if limit is not None else "all"
        with open(self.api_key_path, "r") as f:
            api_key = f.readline()
        url = f"{self.source}?api_key={api_key}&latitude={latitude}&longitude={longitude}&radius={radius}&fuel_type=ELEC&limit={limit_field}"
        return gpd.read_file(url)

    def get_folium_plot(self) -> folium.GeoJson:
        fields, aliases = filter_two_corresponding_arrays(self._gdf.columns, AFDC_FIELDS, AFDC_ALIASES)
        afdc_popup = folium.GeoJsonPopup(
            fields=fields,
            aliases=aliases,
            localize=True,
            labels=True,
        )
        afdc_geojson = folium.GeoJson(
            self._gdf[["station_name", "street_address", "ev_network", "ev_network_web", "geometry"]],
            marker=basic_circle_marker("blue"),
            popup=afdc_popup,
        )
        return afdc_geojson

    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        load_area_transformed = transform_shapely_geometry(load_area_crs, self.local_projected_crs, load_area)
        load_area_centroid_lat_lon = shapely.centroid(load_area)
        load_area_centroid = shapely.centroid(load_area_transformed)
        def get_max_distance_from_centroid(geom: shapely.Polygon) -> float:
            return max(
                [
                    shapely.geometry.LineString([load_area_centroid, v]).length
                    for v in geom.exterior.coords
                ]
            )
        load_area_max_distance = -1
        if type(load_area_transformed) is shapely.Polygon:
            load_area_max_distance = get_max_distance_from_centroid(load_area_transformed)
        else:
            load_area_max_distance = max(map(get_max_distance_from_centroid, load_area_transformed.geoms))
        gdf_afdc_response = self._call_afdc_api_ev_chargers(
            load_area_centroid_lat_lon.y,
            load_area_centroid_lat_lon.x,
            load_area_max_distance * METERS_TO_MILES_FACTOR,
        )
        gdf_afdc_response_to_save = gdf_afdc_response.loc[gdf_afdc_response.within(load_area)]
        self._gdf = gpd.GeoDataFrame(
            gdf_afdc_response_to_save[AFDC_FIELDS],
            geometry=gdf_afdc_response_to_save.geometry
        )
        self._set_is_loaded()

