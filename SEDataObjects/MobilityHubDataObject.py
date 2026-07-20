from typing import Iterable

import folium
import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
from SEDataObjects import BaseLayer, SpatialDataObject
from SEDataObjects.BaseLayer.constants import *
from scipy.stats import percentileofscore

from SEDataObjects.constants import GEODESIC_CRS, LocalDestinationClassification, TrunkBranchClassification
from SEDataObjects.transitWrappers.constants import HIGH_COMFORT_MODES, ModeClassification
from SEDataObjects.utils import basic_circle_marker, get_quantile_ranking_series, transform_shapely_geometry

CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_CUTOFF_BUS = "mh_bus_abs"
CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_QUANTILE_BUS = "mh_bus_percentile"
CONFIG_OVERLAP_HEADWAY_TRUNK_CUTOFF_BUS = "trunk_bus_abs"
CONFIG_OVERLAP_HEADWAY_TRUNK_QUANTILE_BUS = "trunk_bus_percentile"
CONFIG_OVERLAP_HEADWAY_TRUNK_RAIL_CUTOFF = "trunk_rail_abs"
CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_MOBILITY_HUB_BUS = "mh_bus_transfer"
CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_TRUNK_BUS = "trunk_bus_transfer"
CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_RAIL = "trunk_rail_transfer"

DEFAULT_CONFIG_CLASSIFICATION = {
    CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_CUTOFF_BUS: 15,
    CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_QUANTILE_BUS: 0.15,
    CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_MOBILITY_HUB_BUS: 3,
    CONFIG_OVERLAP_HEADWAY_TRUNK_CUTOFF_BUS: 8,
    CONFIG_OVERLAP_HEADWAY_TRUNK_QUANTILE_BUS: 0.05,
    CONFIG_OVERLAP_HEADWAY_TRUNK_RAIL_CUTOFF: 25,
    CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_TRUNK_BUS: 6,
    CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_RAIL: 2,
}

USED_BASE_LAYER_METRICS = [
    SMART_LOCATION_JOB_DENSITY_NAME,
    SMART_LOCATION_POPULATION_DENSITY_NAME,
    SMART_LOCATION_RETAIL_ENTERTAINMENT_JOB_DENSITY_NAME,
    SMART_LOCATION_RAW_JOBS_NAME,
]

MIN_JOB_DENSITY = 1
POINT_BUFFER_RADIUS = 500

OUTPUT_COLUMNS = [
    "od_type", 
    "od_score", 
    "trunk_branch_type",
    "mode", 
    "adjusted_headway",
    "total_frequency", 
    "transfer",
]
OUTPUT_NAMES = [
    "Origin / Local?",
    "Origin / Local Score",
    "Trunk / Branch?",
    "Mode", 
    "Minimum Headway (one direction)", 
    "Total Frequency (all directions)", 
    "Transfer"
]

class MobilityHubDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "mobility_hubs"
    def __init__(self, transit_stop_data_object, base_layer: BaseLayer, local_crs, **classifier_config):
        self.transit_stop_data_object = transit_stop_data_object
        self.base_layer = base_layer
        self.local_crs = local_crs
        self.classifier_config = {
            **DEFAULT_CONFIG_CLASSIFICATION,
            **classifier_config,
        }
    
    def load_data(self, load_area, load_area_crs):
        assert self.transit_stop_data_object.get_is_loaded()
        assert np.all([metric in self.base_layer.metric_names for metric in USED_BASE_LAYER_METRICS])
        gdf_transit_stops = self.transit_stop_data_object.gdf_with_enums.loc[
            self.transit_stop_data_object.gdf_with_enums.within(transform_shapely_geometry(load_area_crs, GEODESIC_CRS, load_area))
        ]
        gdf_merged_transit_stops = assign_base_layer_vars_to_points(
            self.base_layer._gdf, gdf_transit_stops, USED_BASE_LAYER_METRICS, POINT_BUFFER_RADIUS, self.local_crs
        )
        # Correct for cases where headways are unrealistically high (happens when service is very bunched)
        min_reasonable_headway = 60 / gdf_merged_transit_stops["total_frequency"]
        gdf_merged_transit_stops["adjusted_headway"] = gdf_merged_transit_stops["min_overlap_headway"].where(
            gdf_merged_transit_stops["min_overlap_headway"] > min_reasonable_headway, min_reasonable_headway
        )

        gdf_merged_transit_stops["od_score"] = self._generate_od_score(gdf_merged_transit_stops)
        gdf_merged_transit_stops["od_type"] = self._classify_od(gdf_merged_transit_stops["od_score"])
        gdf_merged_transit_stops["trunk_branch_type"] = self._classify_trunk_branch(gdf_merged_transit_stops)
        gdf_merged_transit_stops["investment_score"] = np.nan
        # For convenience, make sure the name of gdf_merged_points.geometry is "geometry"
        gdf_merged_transit_stops["geometry"] = gdf_merged_transit_stops.geometry
        gdf_merged_transit_stops.geometry = gdf_merged_transit_stops["geometry"]
        self._gdf = gdf_merged_transit_stops[
            [*OUTPUT_COLUMNS, gdf_merged_transit_stops.geometry.name]
        ].to_crs(GEODESIC_CRS).dropna(subset=["mode"])
        self._set_is_loaded()
    
    def get_folium_plot(self):
        popup = folium.GeoJsonPopup(
            fields=OUTPUT_COLUMNS, aliases=OUTPUT_NAMES
        )

        def get_color(od_type, trunk_branch_type):
            if trunk_branch_type == TrunkBranchClassification.NOT_MOBILITY_HUB.value:
                return "#c2c2c2"
            if od_type == LocalDestinationClassification.DESTINATION.value and trunk_branch_type == TrunkBranchClassification.TRUNK.value:
                return "#0000ff"
            if od_type == LocalDestinationClassification.DESTINATION.value and trunk_branch_type == TrunkBranchClassification.BRANCH.value:
                return "#00bfff"
            if od_type == LocalDestinationClassification.LOCAL.value and trunk_branch_type == TrunkBranchClassification.TRUNK.value:
                return "#ff00ee"
            if od_type == LocalDestinationClassification.LOCAL.value and trunk_branch_type == TrunkBranchClassification.BRANCH.value:
                return "#ffb0fa"
        
        return folium.GeoJson(
            self.gdf,
            marker=basic_circle_marker("black"),
            style_function=lambda x: {
                "fillColor": get_color(x["properties"]["od_type"], x["properties"]["trunk_branch_type"])
            },
            popup=popup
        )

    @property
    def gdf(self):
        if not self.get_is_loaded:
            raise RuntimeError("Must load data object before the gdf can be obtained")
        output_gdf = self._gdf.copy()
        output_gdf[["mode", "od_type", "trunk_branch_type"]] = output_gdf[["mode", "od_type", "trunk_branch_type"]].map(lambda x: x.value)
        return output_gdf

    @staticmethod
    def _classify_od(od_scores):
        return (get_quantile_ranking_series(od_scores) > 0.8).map({
            True: LocalDestinationClassification.DESTINATION,
            False: LocalDestinationClassification.LOCAL
        })

    @staticmethod
    def _generate_od_score(gdf_merged_points):
        gdf_points = gpd.GeoDataFrame(
            gdf_merged_points[USED_BASE_LAYER_METRICS], geometry=gdf_merged_points.geometry
        )
        gdf_points["jobs_housing_ratio"] = (
            gdf_points[SMART_LOCATION_JOB_DENSITY_NAME] / gdf_points[SMART_LOCATION_POPULATION_DENSITY_NAME]
        )
        gdf_points["jobs_housing_ratio_quantile"] = get_quantile_ranking_series(
            gdf_points["jobs_housing_ratio"]
        )
        gdf_points["job_density_quantile"] = get_quantile_ranking_series(
            gdf_points[SMART_LOCATION_JOB_DENSITY_NAME]
        )
        gdf_points["retail_job_density_quantile"] = get_quantile_ranking_series(
            gdf_points[SMART_LOCATION_RETAIL_ENTERTAINMENT_JOB_DENSITY_NAME]
        )
        gdf_points["od_score"] = (
            (
                gdf_points["jobs_housing_ratio_quantile"] * 10 
                + gdf_points["job_density_quantile"] * 3 
                + gdf_points["retail_job_density_quantile"] * 3
            ) / 16
        )
        gdf_points["od_score"] = gdf_points["od_score"].where(
            (
                (gdf_points[SMART_LOCATION_JOB_DENSITY_NAME] >= MIN_JOB_DENSITY )
                | (gdf_points[SMART_LOCATION_RETAIL_ENTERTAINMENT_JOB_DENSITY_NAME] >=  MIN_JOB_DENSITY)
            ), 0
        ) #TODO: may need to add an additional factor for small bgs or bgs with a school?
        return gdf_points["od_score"]

    def _classify_trunk_branch(self, gdf_stops):
        gdf_stops_copy = gdf_stops.copy()
        #gdf_stops_copy["headway_quantile"] = get_quantile_ranking_series(gdf_stops["min_overlap_headway"])
        headway_array = gdf_stops_copy["adjusted_headway"].dropna().to_numpy()
        mh_headway_quantile_value = np.quantile(
            headway_array, self.classifier_config[CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_QUANTILE_BUS]
        )
        trunk_headway_quantile_value = np.quantile(
            headway_array, self.classifier_config[CONFIG_OVERLAP_HEADWAY_TRUNK_QUANTILE_BUS]
        )
        gdf_stops_copy["classification"] = np.nan
        gdf_stops_copy["mh_from_mode"] = gdf_stops_copy["mode_classification"] == ModeClassification.HIGH_COMFORT
        gdf_stops_copy["mh_from_absolute_headway"] = (
            (gdf_stops_copy["adjusted_headway"] <= self.classifier_config[CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_CUTOFF_BUS])
            & (gdf_stops_copy["total_frequency"] >= (60 / self.classifier_config[CONFIG_OVERLAP_HEADWAY_MOBILITY_HUB_CUTOFF_BUS]))
        )
        gdf_stops_copy["mh_from_headway_quantile"] = (
            (gdf_stops_copy["adjusted_headway"] <= mh_headway_quantile_value)
            & (gdf_stops_copy["total_frequency"] >= (60 / mh_headway_quantile_value)) # TODO: use quantile instead
        )
        gdf_stops_copy["mh_from_transfer"] = (
            (gdf_stops_copy["total_frequency"] >= self.classifier_config[CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_MOBILITY_HUB_BUS])
            & gdf_stops_copy["transfer"]
        )
        gdf_stops_copy["is_mh"] = (
            gdf_stops_copy["mh_from_mode"] 
            | gdf_stops_copy["mh_from_absolute_headway"]
            | gdf_stops_copy["mh_from_headway_quantile"]
            | gdf_stops_copy["mh_from_transfer"]
        )
        gdf_stops_copy["classification"] = gdf_stops_copy["is_mh"].replace(
            to_replace=[True, False],
            value=[np.nan, TrunkBranchClassification.NOT_MOBILITY_HUB]
        )
        gdf_stops_mobility_hub_only_high_comfort = gdf_stops_copy.loc[
            gdf_stops_copy["mh_from_mode"] & gdf_stops_copy["is_mh"], []
        ]
        gdf_stops_mobility_hub_only_other = gdf_stops_copy.loc[
            ~gdf_stops_copy["mh_from_mode"] & gdf_stops_copy["is_mh"], []
        ]
        gdf_stops_mobility_hub_only_high_comfort["trunk_from_headway"] = (
            gdf_stops_copy.loc[
                gdf_stops_mobility_hub_only_high_comfort.index, "adjusted_headway"
            ] <= self.classifier_config[CONFIG_OVERLAP_HEADWAY_TRUNK_RAIL_CUTOFF]
        )
        gdf_stops_mobility_hub_only_high_comfort["trunk_from_transfer"] = (
            (gdf_stops_copy.loc[
                gdf_stops_mobility_hub_only_high_comfort.index, "total_frequency"
            ] >= self.classifier_config[CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_RAIL])
            & gdf_stops_copy.loc[gdf_stops_mobility_hub_only_high_comfort.index, "transfer"]
        )
        gdf_stops_mobility_hub_only_other["trunk_from_headway"] = (
            (gdf_stops_copy.loc[
                gdf_stops_mobility_hub_only_other.index, "adjusted_headway"
            ] <= self.classifier_config[CONFIG_OVERLAP_HEADWAY_TRUNK_CUTOFF_BUS])
            & (gdf_stops_copy["total_frequency"] >= (60 / self.classifier_config[CONFIG_OVERLAP_HEADWAY_TRUNK_CUTOFF_BUS]))
        )
        gdf_stops_mobility_hub_only_other["trunk_from_transfer"] = (
            (gdf_stops_copy.loc[
                gdf_stops_mobility_hub_only_other.index, "total_frequency"
            ] >= self.classifier_config[CONFIG_TOTAL_FREQUENCY_DIVERGING_ROUTES_TRUNK_BUS])
            & gdf_stops_copy["transfer"].loc[gdf_stops_mobility_hub_only_other.index]
        )
        gdf_stops_mobility_hub_only_other["trunk_from_headway_quantile"] = (
            (gdf_stops_copy.loc[
                gdf_stops_mobility_hub_only_other.index, "adjusted_headway"
            ] <= trunk_headway_quantile_value)
            & (gdf_stops_copy["total_frequency"] >= (60 / trunk_headway_quantile_value)) # TODO: change this to be based on the frequency quantile
            
        )
        gdf_stops_mobility_hub = pd.concat(
            [gdf_stops_mobility_hub_only_high_comfort, gdf_stops_mobility_hub_only_other]
        )
        gdf_stops_mobility_hub["is_trunk"] = gdf_stops_mobility_hub[
            ["trunk_from_headway", "trunk_from_transfer", "trunk_from_headway_quantile"]
        ].fillna(False).any(axis=1)
        gdf_stops_with_classification = gdf_stops_copy.merge(
            gdf_stops_mobility_hub[["is_trunk", "trunk_from_headway", "trunk_from_transfer", "trunk_from_headway_quantile"]],
            how="left", left_index=True, right_index=True, validate="one_to_one"
        )
        gdf_stops_with_classification["classification"] = gdf_stops_with_classification["classification"].fillna(
            gdf_stops_mobility_hub["is_trunk"].replace(
                to_replace=[True, False],
                value=[TrunkBranchClassification.TRUNK, TrunkBranchClassification.BRANCH]
            )
        )
        return gdf_stops_with_classification["classification"].copy()


def assign_base_layer_vars_to_points(gdf_base, gdf_points, base_vars, radius, projected_crs):
    assert np.intersect1d(base_vars, gdf_points.columns).size == 0
    # TODO: this is naive approach
    gdf_points_to_merge = gdf_points.copy().reset_index(drop=True).to_crs(projected_crs)
    gdf_points_to_merge["unique_id"] = 1
    gdf_points_to_merge["unique_id"] = gdf_points_to_merge["unique_id"].cumsum()
    gdf_points_buffered = gdf_points_to_merge.copy()
    gdf_points_buffered.geometry = gdf_points_to_merge.buffer(radius)
    buffer_area = np.pi * (radius ** 2) #TODO: check this is right and there isn't a unit error
    gdf_overlayed = gdf_points_buffered.overlay(
        gdf_base[[*base_vars, gdf_base.geometry.name]].to_crs(projected_crs).copy(), 
        how="intersection"
    )
    proportion = gdf_overlayed.area.div(buffer_area)
    gdf_overlayed[base_vars] = gdf_overlayed[base_vars].mul(proportion, axis=0)
    gdf_base_values_on_points = gdf_overlayed.groupby("unique_id")[base_vars].sum()
    assert gdf_points.index.size == gdf_base_values_on_points.index.size
    gdf_merged = gdf_points_to_merge.merge(
        gdf_base_values_on_points, on="unique_id", how="left", validate="one_to_one"
    ).set_index(gdf_points.index, drop=True)
    return gdf_merged
