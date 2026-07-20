import pandas as pd
from .constants import CENSUS_MODESHARE_NAME, CENSUS_MODESHARE_VARIABLES
from .entities import BaseLayerCensus


class CensusModeshare(BaseLayerCensus):
    variable_dict = CENSUS_MODESHARE_VARIABLES
    def get_data_for_ids(self, ids: pd.Series) -> pd.Series:
        return (self.df.loc[
            ids, 
            [self.variable_dict["TRANSIT"], self.variable_dict["BIKE"], self.variable_dict["WALK"]]
        ].sum(axis=1) / (
            self.df.loc[ids, self.variable_dict["MODESHARE_TOTAL"]] - self.df.loc[ids, self.variable_dict["WFH"]]
        )).rename(CENSUS_MODESHARE_NAME)