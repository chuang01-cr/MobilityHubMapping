import pandas as pd
from .constants import CENSUS_CARLESSNESS_NAME, CENSUS_CARLESSNESS_VARIABLES
from .entities import BaseLayerCensus


class CensusCarlessness(BaseLayerCensus):
    variable_dict = CENSUS_CARLESSNESS_VARIABLES
    def get_data_for_ids(self, ids: pd.Series) -> pd.Series:
        return (self.df.loc[
            ids, [self.variable_dict["NO_VEHICLE_RENTER"], self.variable_dict["NO_VEHICLE_OWNER"]]
        ].sum(axis=1) / self.df.loc[
            ids, self.variable_dict["TOTAL"]
        ]).rename(CENSUS_CARLESSNESS_NAME)