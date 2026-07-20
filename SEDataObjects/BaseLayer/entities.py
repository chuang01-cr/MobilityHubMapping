from abc import ABC, abstractmethod
from typing import Iterable

import pandas as pd
import geopandas as gpd
from pygris.data import get_census

from SEDataObjects.BaseLayer import SmartLocationWrapper

from .utils import split_county_fips
from .constants import GEOID_COLUMN


class BaseLayerMetric(ABC):
    df = pd.DataFrame()
    census_id_column = ""
    metric_column = ""
    name = ""
    _loaded = False

    def get_is_loaded(self):
        return self._loaded
    def _set_is_loaded(self):
        self._loaded = True

    @abstractmethod
    def load_data(
        self,
        county_fips: Iterable[str]
    ) -> None:
        pass

    def get_data_for_ids(self, ids: pd.Series) -> pd.DataFrame | pd.Series:
        return self.df.loc[ids].rename(self.name)
    
    def should_send_block_group_gdf(self) -> bool:
        return False
    def send_block_group_gdf(self, gdf: gpd.GeoDataFrame) -> None:
        raise NotImplementedError("This layer does not have a block group gdf configured")
    

class BaseLayerCensus(BaseLayerMetric, ABC):
    @property
    @abstractmethod
    def variable_dict(cls) -> dict[str, str]:
        pass

    def __init__(self):
        self.census_id_column = GEOID_COLUMN
    
    def load_data(self, county_id: Iterable[str]):
        state_id, county_id = split_county_fips(county_id)
        state = state_id[0]
        data = get_census(
            dataset="2022/acs/acs5",
            variables=list(self.variable_dict.values()),
            params={
                "for": f"block group: *",
                "in": f"state: {state} county: {','.join(county_id)}"
            },
            return_geoid = True,
            guess_dtypes = True,
        ).set_index(self.census_id_column)
        self.df = data
        self._set_is_loaded()
    
    @abstractmethod
    def get_data_for_ids(self, ids: pd.Series) -> pd.Series:
        pass


class BaseLayerSmartLocation(BaseLayerMetric, ABC):
    gdf_block_groups = None

    @property
    @abstractmethod
    def metric_field_id(cls) -> str | Iterable[str]:
        pass

    @property
    @abstractmethod
    def metric_alias(cls) -> str:
        pass

    def __init__(self, smartLocationWrapper: SmartLocationWrapper) -> None:
        self.smartlocation_wrapper = smartLocationWrapper

    def load_data(self, county_fips: Iterable[str]):
        if not self.smartlocation_wrapper.get_is_loaded(county_fips):
            self.smartlocation_wrapper.load_data(self.gdf_block_groups, county_fips)
        self._set_is_loaded()
        
    def get_data_for_ids(self, ids):
        out = self.smartlocation_wrapper.gdf.loc[ids, self.metric_field_id]
        if type(self.metric_field_id) is not str:
            out = out.sum(axis=1)
        return out.rename(self.metric_alias, copy=True)

    
    def should_send_block_group_gdf(self) -> bool:
        return True
    def send_block_group_gdf(self, gdf: gpd.GeoDataFrame) -> None:
        self.gdf_block_groups = gdf