import pathlib
from typing import Callable
import folium
from folium.features import GeoJson
import numpy as np
import osmnx as ox
import pandas as pd
import geopandas as gpd
from shapely import MultiPolygon, Polygon
import shapely
from SEDataObjects import SpatialDataObject
from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.utils import transform_shapely_geometry

PAINT_BIKE_LANE_TYPES = ["lane", "share_busway"]
PROTECTED_BIKE_LANE_TYPES = ["track"]
BIKE_EXCLUDE = ["no", "discouraged"]
BIKE_INCLUDE = ["yes", "designated", "permissive"]
PATH_BIKE_NOT_ALLOWED_USUALLY = ["footway", "bridleway", "ramp"]
PATH_BIKE_ALLOWED_USUALLY = ["cycleway", "path"]
CYCLEWAY_TAG_TYPES = ["cycleway", "cycleway:left", "cycleway:right", "cycleway:both"]
#TODO: handle bicycle_road, cyclestreet, bike routes, not sure how to do this though

BIKE_FIELDS = ["cycleway", "highway", "paint_only"]
BIKE_ALIASES = ["Cycleway Type", "Road Type", "Paint Only?"]

BIKE_SEARCH_TAGS = {
    "cycleway": PAINT_BIKE_LANE_TYPES + PROTECTED_BIKE_LANE_TYPES,
    "highway": PATH_BIKE_ALLOWED_USUALLY + PATH_BIKE_NOT_ALLOWED_USUALLY,
    "cycleway:left": PAINT_BIKE_LANE_TYPES + PROTECTED_BIKE_LANE_TYPES,
    "cycleway:right": PAINT_BIKE_LANE_TYPES + PROTECTED_BIKE_LANE_TYPES,
    "cycleway:both": PAINT_BIKE_LANE_TYPES + PROTECTED_BIKE_LANE_TYPES,
    "ramp: bicycle": True,
}
BIKE_COLORS = {
    "paint_only": "#1cff03",
    "not_paint_only": "#21c90e"
}
DEFAULT_REFERENCE_DISTANCE = 4829 # 3 miles in meters

class OSMBikeStreetsDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "osm_bike_infrastructure"

    def __init__(
            self,
            cache_path: (str | pathlib.Path),
            max_distance_from_reference: int | None = DEFAULT_REFERENCE_DISTANCE,
            reference: SpatialDataObject | None = None,
            local_crs: int | None = int
        ):
        self.cache_path = cache_path
        self.max_distance_from_reference = max_distance_from_reference
        self.reference = reference
        self.local_crs = local_crs
        if reference is not None and (max_distance_from_reference is None or local_crs is None):
            raise RuntimeError("Reference provided but CRS and/or max_distance_from_reference not provided. All three parameters must be provided together if reference is provided")
    
    def load_data(self, load_area: MultiPolygon | Polygon, load_area_crs: int) -> None:
        if self.reference is not None:
            assert self.reference.get_is_loaded()
        old_cache_path = ox.settings.cache_folder
        ox.settings.cache_folder = self.cache_path
        if self.reference is not None:
            reference_geom = self.reference._gdf.geometry.to_crs(self.local_crs).buffer(self.max_distance_from_reference).unary_union
            load_area_geom = transform_shapely_geometry(
                self.local_crs,
                GEODESIC_CRS,
                shapely.intersection(
                    transform_shapely_geometry(load_area_crs, self.local_crs, load_area),
                    reference_geom
                )
            )
        else:
            load_area_geom = transform_shapely_geometry(load_area_crs, GEODESIC_CRS, load_area)
        gdf_osm_result = ox.features_from_polygon(
            load_area_geom,
            BIKE_SEARCH_TAGS,
        )
        gdf_osm_result["geometry"] = gdf_osm_result.geometry
        osm_crs = gdf_osm_result.crs
        for tag in BIKE_SEARCH_TAGS.keys():
            if tag not in gdf_osm_result:
                gdf_osm_result[tag] = np.nan
        df_osm_processed_separated = pd.concat([
            gdf_osm_result.loc[ # Paths that usually include bikes and are not excluded
                gdf_osm_result["highway"].isin(PATH_BIKE_ALLOWED_USUALLY) 
                & ~gdf_osm_result["bicycle"].isin(BIKE_EXCLUDE)
            ],
            gdf_osm_result.loc[ # Paths that do not usually include bikes, but where bikes are included
                gdf_osm_result["highway"].isin(PATH_BIKE_NOT_ALLOWED_USUALLY)
                & gdf_osm_result["bicycle"].isin(BIKE_INCLUDE)
            ],
            *[gdf_osm_result.loc[ # Protected bike lanes (includes flexposts)
                gdf_osm_result[cycleway_type].isin(PROTECTED_BIKE_LANE_TYPES)
            ] for cycleway_type in CYCLEWAY_TAG_TYPES],
            gdf_osm_result.loc[gdf_osm_result["ramp: bicycle"] == "yes"]
        ])
        df_osm_processed_separated = df_osm_processed_separated[
            ~df_osm_processed_separated.index.duplicated(keep="first")
        ]
        df_osm_processed_separated["paint_only"] = False
        df_osm_processed_not_separated = pd.concat([
            gdf_osm_result.loc[ # Paint-only bike lanes
                gdf_osm_result[cycleway_type].isin(PAINT_BIKE_LANE_TYPES)
            ] for cycleway_type in CYCLEWAY_TAG_TYPES
        ])
        df_osm_processed_not_separated["paint_only"] = True
        gdf_osm_processed = pd.concat(
            [df_osm_processed_not_separated, df_osm_processed_separated]
        ).sort_values(["cycleway", "paint_only"], ascending=False, kind="stable")
        gdf_osm_processed = gdf_osm_processed.loc[
            ~gdf_osm_processed.index.duplicated(keep="first")
        ]
        gdf_osm_processed = gpd.GeoDataFrame(gdf_osm_processed.reset_index(), geometry="geometry", crs=osm_crs)
        self._gdf = gpd.GeoDataFrame(gdf_osm_processed[BIKE_FIELDS], geometry=gdf_osm_processed.geometry)
        ox.settings.cache_folder = old_cache_path
        self._set_is_loaded()
    
    def get_folium_plot(self) -> GeoJson:
        bike_lane_popup = folium.GeoJsonPopup(
            fields=BIKE_FIELDS,
            aliases=BIKE_ALIASES
        )
        return folium.GeoJson(
            data=self._gdf,
            popup=bike_lane_popup,
            style_function=lambda x: {
                "color": BIKE_COLORS["paint_only"] if x["properties"]["paint_only"] else BIKE_COLORS["not_paint_only"],
                "weight": 4,
            }
        ) 