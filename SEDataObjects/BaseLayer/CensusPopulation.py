import pandas as pd
from .constants import CENSUS_POPULATION_NAME, CENSUS_TOTAL_POPULATION_VARIABLES
from .entities import BaseLayerCensus


class CensusPopulation(BaseLayerCensus):
    variable_dict = CENSUS_TOTAL_POPULATION_VARIABLES
    def get_data_for_ids(self, ids: pd.Series) -> pd.Series:
        return self.df.loc[ids, self.variable_dict["TOTAL"]].rename(CENSUS_POPULATION_NAME)