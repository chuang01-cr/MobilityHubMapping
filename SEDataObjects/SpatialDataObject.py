from abc import ABC, abstractmethod

import folium
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from typing import Callable


class SpatialDataObject(ABC):
    _loaded = False
    _gdf = gpd.GeoDataFrame

    @property
    def gdf(self):
        if not self.get_is_loaded:
            raise RuntimeError("Must load data object before the gdf can be obtained")
        return self._gdf.copy()

    @property
    @abstractmethod
    def name(self):
        return self._name

    @abstractmethod
    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        pass

    @abstractmethod
    def get_folium_plot(self) -> folium.GeoJson:
        pass
        
    def get_is_loaded(self):
        return self._loaded
    def _set_is_loaded(self):
        self._loaded = True
