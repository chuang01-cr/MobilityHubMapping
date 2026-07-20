import pathlib
from typing import Callable, Iterable
import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import pygris
from pygris.utils import erase_water
import shapely
from scipy.spatial import KDTree
import time

from SEDataObjects import SpatialDataObject
from SEDataObjects.constants import GEODESIC_CRS
from .ColorMaps import ColorMaps
from .constants import ACS_YEAR, BUFFER_SIZE, EJSCREEN_NAME, GEOID_COLUMN, GEOID_NAME, TIGER_CRS
from .entities import BaseLayerMetric
from SEDataObjects.utils import call_pygris_with_error_handling, raise_tiger_http_error, transform_shapely_geometry

class BaseLayer(SpatialDataObject):
    name = "base_layer"
    _gdf = gpd.GeoDataFrame
    def __init__(self, metrics: Iterable[BaseLayerMetric], local_crs: int, color_map: ColorMaps, smooth: bool, remove_water=True):
        self.metrics = metrics
        self.local_crs = local_crs
        # Get the color map function
        self.color_map_function = color_map.value
        self.smooth = smooth
        self.remove_water = remove_water
    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ):
        # Load a gdf of counties
        gdf_counties_national = call_pygris_with_error_handling(
            pygris.counties, cache=True, year=2023
        ).to_crs(load_area_crs)
        # Shrink the load area slightly to avoid getting bordering geometries
        load_area_shrunk = transform_shapely_geometry(
            self.local_crs, load_area_crs, shapely.buffer(
                transform_shapely_geometry(
                    load_area_crs, self.local_crs, load_area
                ), BUFFER_SIZE
            )
        )
        gdf_counties = gdf_counties_national.loc[gdf_counties_national.intersects(load_area_shrunk)].copy()
        # Get the TIGER block group data
        gdf_tiger = pd.concat(
            gdf_counties[GEOID_COLUMN].map(
                lambda counties_fips: call_pygris_with_error_handling(
                    pygris.block_groups,
                    state=counties_fips[:2],
                    county=counties_fips[2:5],
                    year=ACS_YEAR,
                    cache=True
                )
            ).values
        )
        if self.remove_water:
            gdf_tiger = call_pygris_with_error_handling(erase_water, gdf_tiger.loc[gdf_tiger.intersects(load_area)], cache=True)
        gdf_tiger = gdf_tiger.set_index("GEOID")
        block_group_centroids = gdf_tiger.to_crs(self.local_crs).centroid
        metric_names = []
        for metric in self.metrics:
            # Load each metric
            if metric.should_send_block_group_gdf():
                metric.send_block_group_gdf(gdf_tiger)
            if not metric.get_is_loaded():
                metric.load_data(gdf_counties[GEOID_COLUMN].values)
            metric_series = metric.get_data_for_ids(gdf_tiger.index)
            print(f"Loaded {metric_series.name}")
            if self.smooth:
                gdf_tiger[metric_series.name] = kde_smoothing(metric_series, block_group_centroids)
            else:
                gdf_tiger[metric_series.name] = metric_series
            metric_names.append(metric_series.name)
        self.metric_names = list(metric_names)
        self._gdf = gdf_tiger
        self._set_is_loaded()

    def get_folium_plot(self):
        gdf_to_render = self.gdf
        gdf_to_render["color"] = self.color_map_function(
            gdf_to_render[self.metric_names]
        )
        popup = folium.GeoJsonPopup(
            fields=self.metric_names + [GEOID_NAME]
        )
        return folium.GeoJson(
            gdf_to_render.reset_index(names=GEOID_NAME),
            style_function=lambda x: {
                "fillColor": x["properties"]["color"],
                "weight": 0.5,
                "color": "grey"
            },
            popup=popup
        ) 
    
    @property
    def gdf(self):
        if not self.get_is_loaded:
            raise RuntimeError("Must load data object before the gdf can be obtained")
        return gpd.GeoDataFrame(
            self._gdf[self.metric_names],
            geometry=self._gdf.geometry
        )

def kde_smoothing(data: pd.Series, points_dropped: gpd.GeoSeries, k=5, bandwidth=0.005, distances_factor = 1/100000):
    """
    Run a kde smoothing algorithm

    :param data: a Pandas Series containing data associated with each element of geom
    :param points: a Geopandas GeoSeries of points associated with data. Must be identically shaped with data
    :param kd_tree: a Kernel Density tree containing each entry of geom. #TODO: allow this to be generated if None is specified
    :param k: the number of geometries to query for. higher = more smoothing, defaults to 5
    :param bandwidth: the bandwidth parameter for the Gaussian smopthing algorithm. Higher bandwith = further points have more weight, defaults to 0.1
    :param distances_factor: the amount to multiply distances by, defaults to 1/100000 to keep values of d^2 reasonably sized and avoid floating point error
    """
    # Build kd tree
    assert data.index.size == points_dropped.index.size and (data.index == points_dropped.index).all()
    # Handle each column, running columns without na values in bulk and running columns with na values together
    #count_na_values = {column: data[column].isna().sum() for column in data.columns}
    data_dropped = data.dropna()
    points_dropped = points_dropped.reindex_like(data_dropped)
    points_dropped_array = np.array([[point.x, point.y] for point in points_dropped.to_numpy()])
    kd_tree = KDTree(points_dropped_array)
    value_is_dropped = ~data.index.isin(data_dropped.index)
    smoothed_values = np.zeros_like(data)
    data_array = data.to_numpy()
    for i, point in enumerate(points_dropped_array):
        if value_is_dropped[i]:
            continue
        distances, indices = kd_tree.query(point, k=k)
        weights = np.exp(-(distances * distances_factor) ** 2 / (2 * bandwidth ** 2))
        smoothed_values[i] = np.sum(data_array[indices] * weights) / np.sum(weights)
    return pd.Series(
        smoothed_values,
        index=data.index
    ).loc[~value_is_dropped].copy().reindex_like(data)
