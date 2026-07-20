from typing import Iterable

import pandas as pd

from .constants import EJSCREEN_COLUMN, EJSCREEN_NAME
from .entities import BaseLayerMetric
import geopandas as gpd

class BaseLayerEjscreen(BaseLayerMetric):
    def __init__(self, ejscreen_path):
        self.path = ejscreen_path
        
    def load_data(self, county_fips: Iterable[str]) -> None:
        gdf_ejscreen = gpd.read_file(self.path, mask=self.gdf_bgs).set_index("ID")
        self.gdf = gdf_ejscreen.loc[gdf_ejscreen.index.str.slice(0,5).isin(county_fips), EJSCREEN_COLUMN]
        self._set_is_loaded()
    def get_data_for_ids(self, ids: pd.Series) -> pd.DataFrame | pd.Series:
        return self.gdf.loc[ids].rename(EJSCREEN_NAME)
    def should_send_block_group_gdf(self) -> bool:
        return True
    def send_block_group_gdf(self, gdf: gpd.GeoDataFrame) -> None:
        self.gdf_bgs = gdf