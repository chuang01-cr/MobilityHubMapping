import time
from typing import Iterable
import geopandas as gpd
import numpy as np
import pandas as pd
from SEDataObjects.BaseLayer.constants import SMART_LOCATION_COLUMNS, SMART_LOCATION_JOB_ACCESSIBILITY_COLUMN
from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.utils import overlap_and_weight_values
from .utils import split_county_fips


class SmartLocationWrapper:
    loaded_county_fips = []
    def __init__(self, smartlocation_path: str, local_crs: int) -> None:
        self.smartlocation_path = smartlocation_path
        self.local_crs = local_crs
    
    def load_data(
        self,
        gdf_bgs_current: gpd.GeoDataFrame,
        county_fips: Iterable[str]
    ):
        # Check that a block group dataframe for the region has been passed
        if gdf_bgs_current is None:
            raise RuntimeError("Need to pass block group GDF")
        state_fips, county_only_fips = split_county_fips(county_fips)
        
        # Load the block groups that overlap with the given county fips codes
        gdf_bgs_current_filtered = gdf_bgs_current.loc[
            (gdf_bgs_current["STATEFP"].isin(state_fips)) & (gdf_bgs_current["COUNTYFP"].isin(county_only_fips))
        ].to_crs(
            self.local_crs
        )
        # Get the smart location gdf. Note that the Smart Location geometry is 2018 Block Groups
        gdf_smartlocation = gpd.read_file(self.smartlocation_path, mask=gdf_bgs_current_filtered).to_crs(self.local_crs)
        gdf_smartlocation = gdf_smartlocation.loc[
            gdf_smartlocation.intersects(gdf_bgs_current_filtered.unary_union)
        ]
        gdf_inferred_values_geographic = overlap_and_weight_values(
            gdf_keep_geometry=gdf_bgs_current_filtered,
            gdf_keep_data=gdf_smartlocation,
            keep_columns=SMART_LOCATION_COLUMNS,
            local_crs=self.local_crs
        )
        gdf_inferred_values_geographic[SMART_LOCATION_COLUMNS] = gdf_inferred_values_geographic[SMART_LOCATION_COLUMNS].map(
            lambda x: np.nan if x < 0 else x
        )
        # Save the results
        self.gdf = gdf_inferred_values_geographic.to_crs(GEODESIC_CRS).copy()
        self._set_is_loaded(county_fips)

    def get_is_loaded(self, county_fips: Iterable[str]) -> bool:
        for i in county_fips:
            if i not in self.loaded_county_fips:
                return False
            
        return True
    
    def _set_is_loaded(self, county_fips: Iterable[str]) -> None:
        self.loaded_county_fips = county_fips