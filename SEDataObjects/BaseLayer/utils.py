import pandas as pd
import geopandas as gpd


def split_county_fips(county_fips: pd.Series) -> tuple[list[str]]:
    states = [i[0:2] for i in county_fips]
    counties = [i[2:6] for i in county_fips]
    return states, counties
