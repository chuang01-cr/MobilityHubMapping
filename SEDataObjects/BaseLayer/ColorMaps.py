from enum import Enum, member
import math
import pandas as pd
import numpy as np
import branca.colormap as cm
from SEDataObjects.BaseLayer.constants import EJSCREEN_NAME, SMART_LOCATION_NATIONAL_WALKABILITY_INDEX_NAME


class ColorMaps(Enum):
    @member
    def BASIC_EJSCREEN_COLORMAP(df: pd.DataFrame) -> pd.Series:
        color_map = {
                0: "green",
                1: "green",
                2: "green",
                3: "green",
                4: "green",
                5: "green",
                6: "yellow",
                7: "yellow",
                9: "yellow",
                8: "red",
                9: "purple",
                10: "purple"
            }
        p_ptraf_to_color_map = lambda p_ptraf_value: (
            "black" if (
                type(p_ptraf_value) is float and np.isnan(p_ptraf_value)
            ) else color_map[math.floor(p_ptraf_value * 0.1)]
        )
        return df[EJSCREEN_NAME].map(p_ptraf_to_color_map)

    @member
    def NATIONAL_WALKABILITY_INDEX_COLORMAP(df: pd.DataFrame) -> pd.Series:
        colormap = cm.StepColormap(
            ["purple", "red", "yellow", "green"],
            [1, 5.76, 10.51, 15.26],
            vmin=1,
            vmax=20
        )
        return df[SMART_LOCATION_NATIONAL_WALKABILITY_INDEX_NAME].map(colormap)

