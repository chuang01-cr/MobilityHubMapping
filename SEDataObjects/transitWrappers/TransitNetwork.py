from dataclasses import dataclass
from typing import Iterable

import numpy as np
import datetime as dt
import geopandas as gpd
import pandas as pd
import shapely
from libpysal.graph import Graph
import networkx as nx

from SEDataObjects.transitWrappers.feed_config import DEFAULT_FEED_CONFIG
from SEDataObjects.transitWrappers.constants import ARBITRARY_DATE, CONFIG_CLUSTERING_DISTANCE_BUS_TO_BUS, CONFIG_CLUSTERING_DISTANCE_BUS_TO_HIGH_COMFORT, CONFIG_CLUSTERING_DISTANCE_HIGH_COMFORT_TO_HIGH_COMFORT, CONFIG_CLUSTERING_DISTANCE_SAME_ROUTE, CONFIG_CLUSTERING_ENABLED, CONFIG_EVENING_PEAK_END, CONFIG_EVENING_PEAK_START, CONFIG_HEADWAY_PERCENTILE, CONFIG_MIN_TRIPS, CONFIG_MORNING_PEAK_END, CONFIG_MORNING_PEAK_START, CONFIG_OFF_PEAK_END, CONFIG_OFF_PEAK_START, CONFIG_PEAK_WEIGHT, PERIOD_EVENING_PEAK_NAME, PERIOD_MORNING_PEAK_NAME, PERIOD_OFF_PEAK_NAME, ROUTE_PRIORITY_MAP
from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.transitWrappers import FeedWrapper
from SEDataObjects.transitWrappers.constants import MODE_CLASSIFICATION_MAP, ModeClassification
from SEDataObjects.transitWrappers.utils import concatenate_id_lists
from SEDataObjects.utils import safe_is_na, time_to_int

#TODO: describe this in the readme
#TODO write definitions for all of these in TransitNetworkSchema.md

@dataclass
class Period:
    name: str
    start: dt.time
    end: dt.time

class TransitNetwork:
    stops = np.array([])
    route_id_current_counts = {}

    feeds = {}
    _gdf_stops = gpd.GeoDataFrame()
    _df_routes = pd.DataFrame()
    _df_stop_times = pd.DataFrame()
    _df_service_patterns = pd.DataFrame()

    def __init__ (self, local_crs, feeds: Iterable[FeedWrapper]=[], config={}):
        """
        An object representing a Transit Network, containing one or more GTFS feeds

        :param local_crs: An EPSG number representing a projected CRS that is valid within the network's area
        :param feeds: An iterable of feeds to add to the network. Defaults to an empty list, in which case no feeds will be present
        :param config: Configuration values, see readme
        """
        config_to_use = {**DEFAULT_FEED_CONFIG, **config}
        self.config = config_to_use
        # Define periods from config
        self.morning_peak = Period(
            PERIOD_MORNING_PEAK_NAME, self.config[CONFIG_MORNING_PEAK_START], self.config[CONFIG_MORNING_PEAK_END]
        )
        self.evening_peak = Period(
            PERIOD_EVENING_PEAK_NAME, self.config[CONFIG_EVENING_PEAK_START], self.config[CONFIG_EVENING_PEAK_END]
        )
        self.off_peak = Period(
            PERIOD_OFF_PEAK_NAME, self.config[CONFIG_OFF_PEAK_START], self.config[CONFIG_OFF_PEAK_END]
        )
        self.periods = (self.morning_peak, self.evening_peak, self.off_peak)
        for feed in feeds:
            self.add_feed(feed)

        # TODO: surely there's a better way to do this
        self.local_crs = local_crs
        self._graph_generated = False
        self._clusters_generated = False
        self._overlap_groups_generated = False
        self._route_groups_generated = False
        self.min_trips = self.config[CONFIG_MIN_TRIPS]
        self.peak_weight = self.config[CONFIG_PEAK_WEIGHT]
        self.percentile = self.config[CONFIG_HEADWAY_PERCENTILE]
        self.clustering_enabled = self.config[CONFIG_CLUSTERING_ENABLED]
        self.clustering_distance_same_route = self.config[CONFIG_CLUSTERING_DISTANCE_SAME_ROUTE]
        self.clustering_distance_bus_to_bus = self.config[CONFIG_CLUSTERING_DISTANCE_BUS_TO_BUS]
        self.clustering_distance_high_comfort_to_high_comfort = self.config[CONFIG_CLUSTERING_DISTANCE_HIGH_COMFORT_TO_HIGH_COMFORT]
        self.clustering_distance_bus_to_high_comfort = self.config[CONFIG_CLUSTERING_DISTANCE_BUS_TO_HIGH_COMFORT]

    @property
    def gdf_stops(self):
        """A GeoDataFrame containing unclustered stops. See schema for details"""
        return self._gdf_stops.copy()
    
    @property
    def gdf_stops_clustered(self):
        """A GeoDataFrame containing clustered stops. See schema for details"""
        if not self.clustering_enabled:
            raise RuntimeError("Clustering is disabled for this network")
        self._create_stop_clusters_lazily()
        return self._gdf_stops_clustered.copy()

    @property
    def df_routes(self):
        """A DataFrame containing routes. See schema for details"""
        return self._df_routes.copy()
    
    @property
    def df_stop_times(self):
        """A DataFrame containing an entry for each stop/trip pair. See schema for details"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
            return self._df_stop_times_clustered.copy()
        return self._df_stop_times.copy()
    
    @property
    def df_service_patterns(self):
        """A DataFrame containing an entry for each service pattern. See schema for details"""
        return self._df_service_patterns.copy()

    @property
    def df_stop_graph(self):
        """A DataFrame representing each stop/service pattern combination. See schema for details"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
            return self._df_stop_graph_clustered
        self._create_route_graph_lazily()
        return self._df_stop_graph

    @property
    def df_overlapping_service_patterns(self):
        """A DataFrame representing each set of overlapping service patterns at a particular stop. See schema for details"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
            return self._df_overlapping_service_patterns_clustered
        self._create_route_graph_lazily()
        return self._df_overlapping_service_patterns

    def add_feed(self, feed: FeedWrapper):
        """Add the specified feed to the network. Currently quite slow"""
        assert feed.feed_loaded
        feed_id = feed.id

        # Build routes df
        routes = feed.df_routes.index.to_numpy()
        routes_transformed = self._transform_route_ids(feed_id, routes)
        routes_map = dict(zip(routes, routes_transformed))
        gdf_feed_stops = feed.gdf_stops
        feed_stop_ids_unique = pd.Series(
            self._transform_stop_ids(feed_id, gdf_feed_stops.index.values),
            index=gdf_feed_stops.index
        )
        stop_geometries = feed.gdf_stops.geometry.to_crs(GEODESIC_CRS)
        gdf_new_network_stops = gpd.GeoDataFrame(
            {
                "feed": feed_id,
                "geometry": stop_geometries,
                "stop_id_original": pd.Series(gdf_feed_stops.index, index=gdf_feed_stops.index),
                "stop_id_unique": feed_stop_ids_unique
            },
        )

        route_ids_direction_ids_combined = [
            feed.get_routes_serving_stop(stop_id) for stop_id in gdf_feed_stops.index.to_numpy()
        ]

        gdf_new_network_stops["route_ids_original"] = [x[0] for x in route_ids_direction_ids_combined]
        gdf_new_network_stops["direction_ids"] = [x[1] for x in route_ids_direction_ids_combined]
        gdf_new_network_stops["route_ids_unique"] = gdf_new_network_stops["route_ids_original"].map(
            lambda route_ids: tuple([routes_map[route_id] for route_id in route_ids])
        )
        df_routes = pd.DataFrame(
            {
                "feed": feed_id,
                "route_id_original": routes,
                "route_id_unique": routes_transformed
            }
        )
        service_pattern_ids_original = pd.Series(
            feed.df_service_patterns.index, index=feed.df_service_patterns.index
        )
        df_service_patterns = pd.DataFrame(
            {
                "feed": feed_id,
                "route_id_unique": self._transform_route_ids(feed_id, feed.df_service_patterns["route_id"]),
                "mode": feed.df_service_patterns["route_type"],
                "service_pattern_id_original": service_pattern_ids_original,
                "service_pattern_id_unique": self._transform_service_pattern_ids(
                    feed_id, feed.df_service_patterns["service_pattern_id"]
                ),
            },
        )
        stop_time_stop_ids_original = feed.df_stop_times["stop_id"]
        stop_time_trip_ids_original = feed.df_stop_times["trip_id"]
        stop_time_service_pattern_id_original = feed.df_stop_times[
            "service_pattern_id"
        ]
        df_stop_times = pd.DataFrame({
            "feed": feed_id,
            "stop_id_original": stop_time_stop_ids_original,
            "stop_id_unique": self._transform_stop_ids(feed_id, stop_time_stop_ids_original),
            "trip_id_original": stop_time_trip_ids_original,
            "trip_id_unique": self._transform_trip_ids(feed_id, stop_time_trip_ids_original),
            "service_pattern_id_original": stop_time_service_pattern_id_original,
            "service_pattern_id_unique": self._transform_service_pattern_ids(
                feed_id, stop_time_service_pattern_id_original
            ),
            "arrival_time": feed.df_stop_times["arrival_time"],
            "departure_time": feed.df_stop_times["departure_time"],
            "stop_sequence": feed.df_stop_times["stop_sequence"],
        }).dropna(subset=["service_pattern_id_unique"])
        df_stops = pd.concat(
            [self._gdf_stops.reset_index(drop=False), gdf_new_network_stops],
            ignore_index=True
        ).set_index("stop_id_unique").drop("index", axis=1, errors="ignore")
        self._gdf_stops = gpd.GeoDataFrame(df_stops, geometry="geometry")
        self._df_routes = pd.concat(
            [self._df_routes.reset_index(drop=False), df_routes],
            ignore_index=True
        ).set_index("route_id_unique").drop("index", axis=1, errors="ignore")
        self._df_service_patterns = pd.concat(
            [self._df_service_patterns.reset_index(drop=False), df_service_patterns],
            ignore_index=True
        ).set_index("service_pattern_id_unique").drop("index", axis=1, errors="ignore")
        self._df_stop_times = pd.concat(
            [self._df_stop_times.reset_index(drop=False), df_stop_times],
            ignore_index=True
        ).set_index(["stop_id_unique", "trip_id_unique"]).drop("index", axis=1, errors="ignore")
        self.feeds[feed_id] = feed
        self._reset_graph_status()
        print(f"Feed {feed_id} added")

    @property
    def weighted_headways_by_stop_overlap(self):
        """A DataFrame of weighted headways for each overlap of service patterns at each stop"""
        headway_group_function = lambda group: self._get_headways_for_group_helper(
            group, self.percentile
        )
        overlap_groups = self._get_overlap_groups_lazily()
        df_overlap_headways = self._get_values_from_groups(
            overlap_groups,
            [period.name for period in self.periods],
            headway_group_function
        )
        weighted_headways = self._get_weighted_value(
            df_overlap_headways, 
            [self.morning_peak.name, self.evening_peak.name],
            self.off_peak.name,
            self.peak_weight,
            max=False
        ).rename("weighted_headway")
        return weighted_headways

    @property
    def weighted_frequencies_by_stop_overlap(self):
        """A DataFrame of weighted frequencies for each overlap of service patterns at each stop"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        overlap_groups = self._get_overlap_groups_lazily()
        frequency_group_functions = [
            lambda group: self._get_frequencies_for_group_helper(group, self.morning_peak),
            lambda group: self._get_frequencies_for_group_helper(group, self.evening_peak),
            lambda group: self._get_frequencies_for_group_helper(group, self.off_peak),

        ]
        df_frequencies = self._get_values_from_groups(
            overlap_groups,
            [period.name for period in self.periods],
            frequency_group_functions
        )
        weighted_frequencies = self._get_weighted_value(
            df_frequencies,
            [self.morning_peak.name, self.evening_peak.name],
            self.off_peak.name, 
            self.peak_weight
        ).rename("weighted_frequency")
        return weighted_frequencies

    def _get_overlap_groups_lazily(self):
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        if not self._overlap_groups_generated:
            self.overlap_groups = [
                self._get_stop_times_grouped_by_service_overlaps(
                    period, self.min_trips
                ) for period in self.periods
            ]
            self._overlap_groups_generated = True
        return self.overlap_groups

    def get_summary_routes_df(self):
        """Get a DataFrame containing headways and frequencies at each stop"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        percentile = self.config[CONFIG_HEADWAY_PERCENTILE]
        peak_weight = self.config[CONFIG_PEAK_WEIGHT]
        periods = (self.morning_peak, self.evening_peak, self.off_peak)
        routes_groups = [
            self._get_stop_times_grouped_by_routes(period) for period in periods
        ]
        headway_group_function = lambda group: self._get_headways_for_group_helper(group, percentile)
        # TODO: use a list comprehension here
        frequency_group_functions = [
            lambda group: self._get_frequencies_for_group_helper(group, self.morning_peak),
            lambda group: self._get_frequencies_for_group_helper(group, self.evening_peak),
            lambda group: self._get_frequencies_for_group_helper(group, self.off_peak),
        ]
        df_route_headways = self._get_values_from_groups(
            routes_groups,
            [period.name for period in periods],
            headway_group_function
        )
        df_route_frequencies = self._get_values_from_groups(
            routes_groups,
            [period.name for period in periods],
            frequency_group_functions
        )
        df_route_summary = pd.concat([
            self._get_weighted_value(
                df_route_headways, [self.morning_peak.name, self.evening_peak.name], self.off_peak.name, peak_weight, max=False
            ).rename("weighted_headway"),
            self._get_weighted_value(
                df_route_frequencies, [self.morning_peak.name, self.evening_peak.name], self.off_peak.name, peak_weight
            ).rename("weighted_frequency"),
        ], axis=1)
        return df_route_summary

    def get_headways_by_route(self, period, percentile):
        """Get headways based on the provided period and percentile for each route at each stop"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        group_in_period = self._get_stop_times_grouped_by_routes(period)
        return self._get_headways_for_group_helper(group_in_period, percentile)

    def get_headways_by_overlap(self, period, percentile, min_trips=None):
        """Get headways based on the provided period and percentile for each set of overlapping service patterns at each stop"""
        min_trips = min_trips if min_trips is not None else self.min_trips
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        group_in_period = self._get_stop_times_grouped_by_service_overlaps(period, min_trips)
        return self._get_headways_for_group_helper(group_in_period, percentile)
    
    def get_frequencies_by_route(self, period):
        """Get frequencies based on the provided period for each route at each stop"""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        group_in_period = self._get_stop_times_grouped_by_routes(period)
        return self._get_frequencies_for_group_helper(group_in_period, period)
    
    def get_frequencies_by_overlap(self, period, min_trips=None): #TODO: this shouldn't be 1 by default
        """Get frequencies based on the provided period for each set of overlapping service patterns at each stop"""
        min_trips = min_trips if min_trips is not None else self.min_trips
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        group_in_period = self._get_stop_times_grouped_by_service_overlaps(period, min_trips)
        return self._get_frequencies_for_group_helper(group_in_period, period)

    @property
    def transfer_status(self):
        """A boolean Series indexed by stop id, with True values for transfer stops and False values for non-transfer stops."""
        if self.clustering_enabled:
            self._create_stop_clusters_lazily()
        else:
            self._create_route_graph_lazily()
        min_patterns_for_transfer = 3 if self.clustering_enabled else 2
        is_transfer = (
            (self.df_stop_graph.groupby("stop_id_unique")["next_stop"].nunique(dropna=True) >= min_patterns_for_transfer)
            | self.df_stop_graph.groupby("stop_id_unique")[["last_stop", "first_stop"]].any().any(axis=1)
        ).fillna(False)
        return is_transfer
    
    @property
    def mode_by_stop(self):
        """A series indexed by stop ids containing the mode for each stop"""
        stop_ids_with_service_patterns = self.df_stop_times.index.get_level_values(
            "stop_id_unique"
        ).drop_duplicates()
        service_patterns_by_stop_id = self.df_stop_times.droplevel(1).loc[
            stop_ids_with_service_patterns, "service_pattern_id_unique"
        ]
        modes_by_stop_id = self.df_service_patterns.loc[service_patterns_by_stop_id.values, "mode"]
        modes_by_stop_id.index = service_patterns_by_stop_id.index
        modes_by_stop_id_sorted = modes_by_stop_id.sort_values(
            key=(lambda mode_series: mode_series.map(ROUTE_PRIORITY_MAP))
        )
        primary_mode_by_stop = modes_by_stop_id_sorted.loc[~modes_by_stop_id_sorted.index.duplicated(keep="first")].copy()
        return primary_mode_by_stop

    @property
    def mode_classification_by_stop(self):
        """A series indexed by stop ids containing the mode classification for each stop (bus, high comfort, other)"""
        return self._get_mode_classification_by_stop()

    def _get_mode_classification_by_stop(self, cluster_if_enabled=True):
        df_stop_times = self.df_stop_times if cluster_if_enabled else self._df_stop_times
        stop_ids_with_service_patterns = df_stop_times.index.get_level_values(
            "stop_id_unique"
        ).drop_duplicates()
        service_patterns_by_stop_id = df_stop_times.droplevel(1).loc[
            stop_ids_with_service_patterns, "service_pattern_id_unique"
        ]
        modes_by_stop_id = self.df_service_patterns.loc[service_patterns_by_stop_id.values, "mode"]
        modes_by_stop_id.index = service_patterns_by_stop_id.index
        mode_classification_by_stop_id = modes_by_stop_id.map(MODE_CLASSIFICATION_MAP)
        primary_mode_classification_by_stop_id = mode_classification_by_stop_id.sort_values(
            key=lambda mode_series: mode_series.map({
                ModeClassification.HIGH_COMFORT: 0,
                ModeClassification.BUS: 1,
                ModeClassification.OTHER: 2
            }),
            ascending=True
        ).groupby(level=0).first()
        return primary_mode_classification_by_stop_id.reindex(
            self._gdf_stops.index if not (self.clustering_enabled and cluster_if_enabled) else self._gdf_stops_clustered.index
        ).copy() #TODO use gdf stop clustered if clustered
    
    def _cluster_stop_dataframes(self):
        cluster_response = self._cluster_stops()
        self._gdf_stops_clustered = cluster_response["gdf_stops"] #TODO: write schema
        # Convert cluster ids from int to str, to make them consistent with stop_id_unique
        self._gdf_stops_clustered.index = self._gdf_stops_clustered.index.astype(int).astype(str)
        cluster_id_map = cluster_response["id_map"].astype(int).astype(str)
        # Get df containing stop metadata by clustered stop id
        df_stop_cluster_metadata = self.gdf_stops.reset_index().drop(
            self.gdf_stops.geometry.name, axis=1
        ).explode(
            ["route_ids_unique", "route_ids_original", "direction_ids"]
        ).rename(
            columns={
                "route_ids_unique": "route_id_unique",
                "route_ids_original": "route_id_original",
                "direction_ids": "direction_id",
            }
        ).merge(
            cluster_id_map.reset_index(),
            on="stop_id_unique",
            how="left", #TODO: always do a left merge for legibility
            validate="many_to_one",
        ).set_index([
            "clustering_id", "stop_id_unique"
        ]).sort_index()
        self._df_stop_cluster_metadata = df_stop_cluster_metadata #TODO: write schema

        # Get df stop times with clustered instead of unique stop ids
        df_stop_times_with_cluster = self._df_stop_times.reset_index().merge(
            cluster_id_map.reset_index(),
            on="stop_id_unique",
            how="left",
            validate="many_to_one"
        ).drop("stop_id_unique", axis=1).rename(
            columns={"clustering_id": "stop_id_unique"}
        ).set_index(["stop_id_unique", "trip_id_unique"])
        stop_times_index_group = df_stop_times_with_cluster.dropna(
            subset=["arrival_time", "departure_time"]
        ).groupby(
            level=["stop_id_unique", "trip_id_unique"]
        )
        df_stop_times_with_cluster["arrival_time"] = stop_times_index_group["arrival_time"].min()
        df_stop_times_with_cluster["departure_time"] = stop_times_index_group["departure_time"].max()
        df_stop_times_with_cluster["stop_sequence"] = stop_times_index_group["stop_sequence"].min()

        df_stop_times_with_cluster_duplicates_removed = df_stop_times_with_cluster.loc[
            ~df_stop_times_with_cluster.index.duplicated(keep="first")
        ].drop(["feed", "stop_id_original"], axis=1)
        self._df_stop_times_clustered = df_stop_times_with_cluster_duplicates_removed

        # Update stop graph with cluster ids
        df_stop_graph_with_cluster = self._df_stop_graph.merge(
            cluster_id_map.reset_index(),
            on="stop_id_unique",
            how="left",
            validate="many_to_one"
        ).merge(
            cluster_id_map.rename("next_stop_clustering_id"),
            left_on="next_stop",
            right_index=True,
            how="left",
            validate="many_to_one"
        ).merge(
            cluster_id_map.rename("previous_stop_clustering_id"),
            left_on="previous_stop",
            right_index=True,
            how="left",
            validate="many_to_one"
        ).drop(
            ["next_stop", "previous_stop", "stop_id_unique"], axis=1
        ).rename(
            columns={
                "next_stop_clustering_id": "next_stop",
                "previous_stop_clustering_id": "previous_stop",
                "clustering_id": "stop_id_unique",    
            }
        )
        df_stop_graph_with_cluster_duplicates_removed = df_stop_graph_with_cluster.loc[
            ~(
                (df_stop_graph_with_cluster["previous_stop"] == df_stop_graph_with_cluster["stop_id_unique"])
                | (df_stop_graph_with_cluster["next_stop"] == df_stop_graph_with_cluster["stop_id_unique"])
            ),
        ]
        self._df_stop_graph_clustered = df_stop_graph_with_cluster_duplicates_removed

        # Update df_service_overlaps with clustered ids
        self._df_overlapping_service_patterns_clustered = self._get_overlaps_from_stop_graph(df_stop_graph_with_cluster_duplicates_removed)

        self._clusters_generated = True

    def _cluster_stops(self, output_id_name="clustering_id", high_comfort_name="high_comfort"):
        #TODO: needs to guarantee that stops won't be split by routes, probably need to add an extra step in stop clustering
        self._create_route_graph_lazily()
        # Build sequential stop graph (to avoid matching closely spaced stops with each other)

        # Get stops with mode classification, since different logic is used to cluster high comfort stops
        gdf_stops_copy = self._gdf_stops.to_crs(self.local_crs)
        gdf_stops_copy["mode_classification"] = self._get_mode_classification_by_stop(cluster_if_enabled = False)
        
        # Get df of stops and routes
        gdf_stops_with_route = gdf_stops_copy.reset_index().explode("route_ids_unique", "direction_ids").rename(
            columns={"route_ids_unique": "route_id_unique"}
        ).drop_duplicates( # Don't have duplicate entries where two different directions arrive at the same stop
            subset=["route_id_unique", "stop_id_unique"]
        ).set_index(
            "stop_id_unique"
        ).drop(["route_ids_original", "direction_ids"], axis=1)

        # Cluster same route stops
        same_stop_response = self._cluster_stops_same_route(gdf_stops_with_route) #TODO: need to have output df values as parameters, this is hard to read
        gdf_stops_same_route_clustered = same_stop_response["gdf_stops"]
        sequential_stop_graph_same_route_clustered = same_stop_response["sequential_stops"]
        same_route_id_by_original_id = same_stop_response["id_map"]
        
        # Get a df of high comfort stops
        gdf_stops_high_comfort = gdf_stops_same_route_clustered.loc[
            gdf_stops_same_route_clustered["mode_classification"] == ModeClassification.HIGH_COMFORT
        ].copy()
        if gdf_stops_high_comfort.index.size == 0:
            # We don't need to handle non-high comfort stops, so all stops bus-to-bus
            bus_to_bus_response = self._cluster_stops_min_distance_graph(
                gdf_stops_same_route_clustered,
                sequential_stop_graph_same_route_clustered,
                self.clustering_distance_bus_to_bus,
                original_id_name="same_route_clustering_id",
                new_id_name=output_id_name,
                new_geometry_name="clustered_geometry",
                existing_id_map=same_route_id_by_original_id
            )
            #TODO: duplicated code
            gdf_stops_bus_to_bus_clustered = bus_to_bus_response["gdf_stops"].set_index(output_id_name).to_crs(GEODESIC_CRS)
            gdf_stops_bus_to_bus_clustered[high_comfort_name] = False
            bus_to_bus_id_map = bus_to_bus_response["id_map"].rename(output_id_name)
            print("NO HIGH COMFORT STOPS")
            return {
                "id_map": bus_to_bus_id_map,
                "gdf_stops": gdf_stops_bus_to_bus_clustered
            }
        
        # Cluster high comfort stops with each other 
        inter_high_comfort_response = self._cluster_stops_min_distance_graph(
            gdf_stops_high_comfort,
            sequential_stop_graph_same_route_clustered,
            self.clustering_distance_high_comfort_to_high_comfort,
            original_id_name="same_route_clustering_id",
            new_id_name="inter_high_comfort_clustering_id",
            new_geometry_name="clustered_geometry",
            existing_id_map=same_route_id_by_original_id
        )
        inter_high_comfort_ids_by_original_route_id= inter_high_comfort_response["id_map"]
        gdf_high_comfort_stops_unique_points = inter_high_comfort_response["gdf_stops"]
        gdf_non_high_comfort = gdf_stops_same_route_clustered.loc[
            gdf_stops_same_route_clustered["mode_classification"] != ModeClassification.HIGH_COMFORT
        ]
        # Cluster bus stops to nearby high comfort stops
        high_comfort_to_bus_response = self._cluster_stops_nearest(
            gdf_high_comfort_stops_unique_points.set_index("inter_high_comfort_clustering_id"),
            gdf_non_high_comfort,
            self.clustering_distance_bus_to_high_comfort,
            new_id_name="nearest_high_comfort_id",
            new_geometry_name="clustered_geometry",
            existing_id_map=same_route_id_by_original_id
        )
        # Note we don't need the actual clustered geometries, since they're just a subset of the inter high comfort clustered points
        gdf_stops_no_high_comfort = high_comfort_to_bus_response["gdf_stops_not_joined"]
        joined_to_high_comfort_id_map = high_comfort_to_bus_response["id_map"]
        
        # Group stops that are not clustered with high comfort stops (bus-to-bus)
        bus_to_bus_response = self._cluster_stops_min_distance_graph(
            gdf_stops_no_high_comfort,
            sequential_stop_graph_same_route_clustered,
            self.clustering_distance_bus_to_bus,
            original_id_name="same_route_clustering_id",
            new_id_name="bus_to_bus_clustering_id",
            new_geometry_name="clustered_geometry",
            existing_id_map=same_route_id_by_original_id
        )
        gdf_stops_bus_to_bus_clustered = bus_to_bus_response["gdf_stops"]
        bus_to_bus_id_by_original_stop_id = bus_to_bus_response["id_map"]

        # Concatenate all id maps and update ids to all be on the same baseline
        bus_to_bus_id_start = inter_high_comfort_ids_by_original_route_id.max() + 1
        clustered_id_by_original_id = pd.concat([
            inter_high_comfort_ids_by_original_route_id,
            joined_to_high_comfort_id_map,
            bus_to_bus_id_by_original_stop_id + (bus_to_bus_id_start if not safe_is_na(bus_to_bus_id_start) else 1)
        ]).rename(output_id_name)
        gdf_high_comfort_stops_unique_points[output_id_name] = gdf_high_comfort_stops_unique_points["inter_high_comfort_clustering_id"]
        gdf_stops_bus_to_bus_clustered[output_id_name] = gdf_stops_bus_to_bus_clustered["bus_to_bus_clustering_id"] + bus_to_bus_id_start

        # Concatenate all stop gdfs
        gdf_high_comfort_stops_unique_points[high_comfort_name] = True
        gdf_stops_bus_to_bus_clustered[high_comfort_name] = False
        assert gdf_high_comfort_stops_unique_points.geometry.name == gdf_stops_bus_to_bus_clustered.geometry.name
        gdf_stops_clustered = gpd.GeoDataFrame(
            pd.concat([
                gdf_high_comfort_stops_unique_points, gdf_stops_bus_to_bus_clustered,
            ]),
            geometry=gdf_high_comfort_stops_unique_points.geometry.name
        )[
            [output_id_name, high_comfort_name, gdf_high_comfort_stops_unique_points.geometry.name]
        ].set_index(output_id_name).to_crs(GEODESIC_CRS)

        # Check that we have created a 1-1 mapping from original to clustered ids
        assert gdf_stops_with_route.index.isin(clustered_id_by_original_id.index).all()
        assert clustered_id_by_original_id.index.isin(gdf_stops_with_route.index).all()

        return {"id_map": clustered_id_by_original_id, "gdf_stops": gdf_stops_clustered}
    
    @staticmethod
    def _update_id_map(existing_id_map, new_id_map):
        if existing_id_map is None:
            return new_id_map
        
        indices_match = new_id_map.index.isin(existing_id_map.values)
        assert indices_match.all()

        return existing_id_map.map(new_id_map).dropna()

    @staticmethod
    def _cluster_stops_min_distance_graph(gdf_stops, stop_graph, clustering_distance, original_id_name="stop_id_unique", new_id_name="clustering_id", new_geometry_name="clustered_geometry", existing_id_map=None):        
        gdf_stops_copy = gdf_stops.copy()
        # Group high comfort stops to other nearby high comfort stops
        gdf_stops_copy[new_id_name] = TransitNetwork._get_clustered_stop_groups(
            gdf_stops_copy.geometry.buffer(clustering_distance),
            original_id_name,
            stop_graph,
            filter_sequential_stops_graph=True
        )
        gdf_stops_copy[new_geometry_name] = TransitNetwork._aggregate_clustered_geometry(
            gdf_stops_copy, new_id_name, ignore_index=False
        )
        # Get a map of high comfort ids by same route id. Necessary to avoid losing some in the next stop
        clustered_ids_by_original_id = TransitNetwork._update_id_map(
            existing_id_map, gdf_stops_copy[new_id_name].copy()
        )
        # Get points representing high comfort stops
        gdf_high_comfort_stops_unique_points = gdf_stops_copy.drop_duplicates(
            subset=[new_id_name], keep="first"
        ).set_geometry(
            new_geometry_name
        )[[new_id_name, new_geometry_name]].reset_index(drop=True)  
        new_stop_graph = TransitNetwork._update_stop_graph(stop_graph, clustered_ids_by_original_id)      
        return {
            "id_map": clustered_ids_by_original_id,
            "gdf_stops": gdf_high_comfort_stops_unique_points,
            "stop_graph": new_stop_graph,
        }

    def _cluster_stops_nearest(self, gdf_stops_fixed, gdf_stops_to_merge, clustering_distance, new_id_name="clustering_id", new_geometry_name="clustered_geometry", existing_id_map=None):
        #TODO: this hasn't been used/tested
        # Copy inputs and rename geometries to distinguish them
        FIXED_INDEX_NAME = "fixed_index"
        FIXED_GEOMETRY_NAME = "fixed_geometry"
        MERGE_INDEX_NAME = "merge_index"
        MERGE_GEOMETRY_NAME = "merge_geometry"
        gdf_stops_fixed_copy = gdf_stops_fixed.rename_geometry(FIXED_GEOMETRY_NAME).rename_axis(index=FIXED_INDEX_NAME)
        gdf_stops_to_merge_copy = gdf_stops_to_merge.rename_geometry(MERGE_GEOMETRY_NAME).rename_axis(index=MERGE_INDEX_NAME)
        # Assign to merge stops to a fixed stop, and get both geometries in one df
        gdf_stops_merged = gdf_stops_to_merge_copy.reset_index().sjoin_nearest(
            gdf_stops_fixed_copy.reset_index()[[FIXED_GEOMETRY_NAME, FIXED_INDEX_NAME]],
            how="left",
            max_distance=clustering_distance,
        ).merge( # We want the geometry from the high comfort stops, so we need to join, then merge
            gdf_stops_fixed_copy[[FIXED_GEOMETRY_NAME]],
            how="left",
            left_on=FIXED_INDEX_NAME,
            right_index=True,
            validate="many_to_one"
        ).drop("index_right", axis=1)
        assert ((gdf_stops_merged[MERGE_INDEX_NAME]) == (gdf_stops_to_merge.index)).all() # If this fails, it's likely because of an sjoin issue
        # Seperate stops that are not assigned a stop
        gdf_stops_not_joined = gdf_stops_to_merge.loc[
            gdf_stops_merged.set_index(MERGE_INDEX_NAME)[FIXED_INDEX_NAME].isna()
        ]
        gdf_stops_only_joined = gdf_stops_merged.rename( #TODO: test this, it isn't used in the main _cluster_stops_function
            columns={FIXED_INDEX_NAME: new_id_name, FIXED_GEOMETRY_NAME: new_geometry_name}
        ).loc[
            ~gdf_stops_merged[FIXED_INDEX_NAME].isna()
        ].drop(MERGE_GEOMETRY_NAME, axis=1).set_geometry(new_geometry_name)
        # Get a geoseries of clustered high comfort stops
        id_map = TransitNetwork._update_id_map(
            existing_id_map,
            gdf_stops_only_joined.set_index(MERGE_INDEX_NAME)[new_id_name]
        )
        # Get a gdf of unique stop locations
        gdf_stops_clustered = gdf_stops_only_joined.set_index(MERGE_INDEX_NAME)[[
            new_id_name, new_geometry_name
        ]].drop_duplicates(subset=[new_id_name], keep="first")
        return {
            "gdf_stops": gdf_stops_clustered,
            "gdf_stops_not_joined": gdf_stops_not_joined,
            "id_map": id_map
        }
    

    def _cluster_stops_same_route(self, gdf_stops_with_route):
        """
        Cluster stops where they share a route
        
        args:
        gdf_stops_with_route: A GeoDataFrame of stops that has been exploded so one row corresponds to a unique a stop/route/direction 
        
        returns:
        A dictionary with the following key-value pairs:
        "id_map": A series mapping original `stop_id_unique` values to the clustered stop ids
        "gdf_stops": A GeoDataFrame keyed by the new clustered stop ids
        "sequential_stops": A NX undirected graph with nodes representing clustered stops and edges representing routes between them
        """
        # Cluster stops with the same route id (to get stops on opposite sides of roads)
        df_stop_graph_no_termini = self._df_stop_graph.dropna(subset=["next_stop"]).groupby(
            ["service_pattern_id_unique", "stop_id_unique", "next_stop"]
        ).first().reset_index().merge(
            self.df_service_patterns["route_id_unique"],
            how="left",
            left_on="service_pattern_id_unique",
            right_index=True,
            validate="many_to_one"
        )
        sequential_stop_graph = nx.from_pandas_edgelist(
            df_stop_graph_no_termini.rename(columns={"next_stop": "target", "stop_id_unique": "source"}),
        )
        # Generate buffers
        gdf_stops_with_route_buffered = gdf_stops_with_route.copy()
        gdf_stops_with_route_buffered.geometry = gdf_stops_with_route.buffer(self.clustering_distance_bus_to_bus)
        # Workaround for https://github.com/geopandas/geopandas/issues/3059
        gdf_stops_with_route_buffered["geometry_as_object"] = gdf_stops_with_route_buffered.geometry.astype(object)
        # Generate clustered ids
        gdf_stops_with_route["same_route_clustering_id"] = gdf_stops_with_route_buffered.groupby(
            "route_id_unique"
        )["geometry_as_object"].transform(
            lambda stop_geometry: stop_geometry.name + "_" + self._get_clustered_stop_groups(
                stop_geometry,
                "stop_id_unique",
                sequential_stops_nx_graph=sequential_stop_graph,
                filter_sequential_stops_graph=True
            ).astype(str)
        )
        stops_grouped_by_unique_id = gdf_stops_with_route.groupby(level="stop_id_unique")
        # Reassign same-route ids so that they're all assigned the same stop
        gdf_stops_with_route["same_route_clustering_id"] = stops_grouped_by_unique_id["same_route_clustering_id"].first().reindex(gdf_stops_with_route.index)
        # Get map from `stop_id_unique` to `same_route_clustering_id`
        same_route_id_by_original_id = gdf_stops_with_route.reset_index().drop_duplicates(
            subset=["stop_id_unique"]
        ).set_index(
            ["stop_id_unique"]
        )["same_route_clustering_id"].dropna()
        # Cluster stop geometries by centroid
        gdf_stops_same_route_clustered = gpd.GeoDataFrame(
            gdf_stops_with_route.groupby("same_route_clustering_id")[["mode_classification"]].first(),
            geometry=self._aggregate_clustered_geometry(gdf_stops_with_route, "same_route_clustering_id"),
            crs=self.local_crs
        )

        # Update the stop graph with new route ids
        df_stop_graph_new_ids = df_stop_graph_no_termini.merge(
            same_route_id_by_original_id.rename("stop_id_same_route"),
            how="left",
            left_on="stop_id_unique",
            right_index=True,
            validate="many_to_one"
        ).merge(
            same_route_id_by_original_id.rename("next_stop_id_same_route"),
            how="left",
            left_on="next_stop",
            right_index=True,
            validate="many_to_one"
        )
        # Remove stop graph rows where consecutive stops have been clustered
        df_stop_graph_new_ids = df_stop_graph_new_ids.loc[
            df_stop_graph_new_ids["stop_id_same_route"] != df_stop_graph_new_ids["next_stop_id_same_route"]
        ] 
        # Remove duplicate edges from stop graph, since we don't care about routes anymore
        df_stop_graph_same_route_clustered = df_stop_graph_new_ids.groupby( 
            ["stop_id_same_route", "next_stop_id_same_route"]
        ).first().reset_index()
        # Convert stop graph df to networkx
        sequential_stop_graph_same_route_clustered = nx.from_pandas_edgelist(
            df_stop_graph_same_route_clustered.rename(columns={"stop_id_same_route": "source", "next_stop_id_same_route": "target"})
        )
        return {
            "id_map": same_route_id_by_original_id, 
            "gdf_stops": gdf_stops_same_route_clustered,
            "sequential_stops": sequential_stop_graph_same_route_clustered
        }

    @staticmethod
    def _update_stop_graph(original_graph, stop_id_map):    
        renamed_graph = nx.relabel_nodes(original_graph, dict(stop_id_map))
        renamed_graph.remove_edges_from(nx.selfloop_edges(renamed_graph))
        return renamed_graph

    @staticmethod
    def _aggregate_clustered_geometry(gdf, group_column, ignore_index=True):
        assert group_column in gdf.columns
        geometry_name = gdf.geometry.name
        geometry_crs = gdf.crs
        grouped_grometry = gdf.groupby(group_column)[geometry_name].agg(
            lambda geoms: shapely.unary_union(geoms).centroid
        )
        grouped_grometry.crs = geometry_crs
        if not ignore_index:
            assert not gdf.index.duplicated().any()
            gdf_merged = gpd.GeoDataFrame(
                gdf[[group_column]].merge(
                    grouped_grometry,
                    how="left",
                    left_on=group_column,
                    right_index=True,
                    validate="many_to_one"
                ),
                geometry=grouped_grometry.name,
            )
            return gdf_merged.geometry
        else:
            return grouped_grometry        

    @staticmethod
    def _get_clustered_stop_groups(
        stop_geoms_buffered, 
        stop_id_name, #TODO: not sure what this parameter does?
        sequential_stops_nx_graph,
        output_stop_id_name="clustered_stop_id",
        filter_sequential_stops_graph=False
    ):
        stop_geoms_no_duplicate_index = stop_geoms_buffered.loc[
            ~stop_geoms_buffered.index.duplicated(keep="first")
        ]
        # Get a graph of stops within the buffer radius, excluding stops that are adjacent in the stop graph
        nearby_stops_graph = Graph.build_fuzzy_contiguity(
            stop_geoms_no_duplicate_index.loc[
                np.intersect1d(stop_geoms_no_duplicate_index.index.values, sequential_stops_nx_graph.nodes)
            ]
        ).to_networkx()
        sequential_stops_nx_graph_copy = sequential_stops_nx_graph.copy()
        if filter_sequential_stops_graph:
            # Filter sequential stops to only those in the nearby stops graph (required to handle the same route case, otherwise redundant)
            # TODO: need to add that, if a connection between two sequential stops exists anywhere, it is removed
            sequential_stops_nx_graph_copy.remove_nodes_from(
                [node for node in sequential_stops_nx_graph.nodes if node not in nearby_stops_graph.nodes]
            )
        # Remove stops that are not in the adjacent stops graph, these are stops that do not receive a transit service
        nearby_stops_graph.remove_nodes_from(
            [node for node in nearby_stops_graph.nodes if node not in sequential_stops_nx_graph_copy.nodes]
        )
        nearby_stops_sequential_removed = nx.difference(nearby_stops_graph, sequential_stops_nx_graph_copy)
        # Get connected components as a series keyed by stop ids
        df_components = pd.Series(
            nx.connected_components(nearby_stops_sequential_removed)
        ).explode().reset_index(name=stop_id_name).rename(
            columns={"index": output_stop_id_name}
        ).set_index(stop_id_name).reindex(stop_geoms_buffered.index)
        component_series = pd.Series(df_components[output_stop_id_name])
        component_series_na = component_series.isna()
        na_start_value = np.max(component_series) + 1
        if np.isnan(na_start_value):
            component_series.loc[:] = np.arange(0, component_series.size)
        else:
            component_series.loc[component_series_na] = np.arange(
                na_start_value, na_start_value + component_series_na.sum()
            )
        assert stop_geoms_buffered.index.size == component_series.index.size
        return component_series

    @staticmethod
    def _get_weighted_value(df, peak_names, off_peak_name, peak_weight, max=True, na_value=None):
        peak_max = None
        if max:
            peak_max = df[peak_names].max(axis=1)
        else:
            peak_max = df[peak_names].min(axis=1)
        off_peak_value = df[off_peak_name]
        if na_value is not None:
            peak_max = peak_max.fillna(na_value)
            off_peak_value.fillna(na_value)
        weighted_values = (peak_max * peak_weight) + (off_peak_value * (1 - peak_weight))
        return weighted_values

    @staticmethod
    def _get_values_from_groups(groups, names, functions):
        result_series_list = []
        functions_iterable = None
        assert len(groups) == len(names)
        if callable(functions):
            functions_iterable = [functions for _ in names]
        else:
            functions_iterable = list(functions)
            assert len(functions_iterable) == len(groups)
        for group, name, function in zip(groups, names, functions_iterable):
            result_series_list.append(
                function(group).rename(name)
            )
        df_results = pd.concat(result_series_list, axis=1)
        return df_results
    
    def _create_route_graph(self):    
        df_stop_graph = self._df_stop_times.sort_values(
            ["trip_id_unique", "stop_sequence"], kind="stable"
        ).reset_index(
            drop=False
        ).drop_duplicates(
            subset=["stop_id_unique", "service_pattern_id_unique"]
        )[
            ["stop_id_unique", "service_pattern_id_unique", "stop_sequence"]
        ].copy()
        # Get info about the next and previous stop (so we now have a graph of the network)
        service_pattern_stop_groupby = df_stop_graph.groupby("service_pattern_id_unique")["stop_id_unique"]
        df_stop_graph["next_stop"] = service_pattern_stop_groupby.shift(periods=-1)
        df_stop_graph["previous_stop"] = service_pattern_stop_groupby.shift(periods=1)
        # Mark stops as first or last stop, needed to get transfers
        df_stop_graph["last_stop"] = df_stop_graph["next_stop"].isna()
        df_stop_graph["first_stop"] = df_stop_graph["previous_stop"].isna()

        # Get service patterns indexed by their next and previous stop
        df_stop_graph_no_endings = df_stop_graph.loc[~df_stop_graph["last_stop"]].copy()
        service_patterns_by_next_current_stop = df_stop_graph_no_endings.dropna(subset=["next_stop"]).set_index(
            ["stop_id_unique", "next_stop"]
        )["service_pattern_id_unique"]
        # Get a tuple of service patterns that share the same current and next stop, but that do not terminate
        get_as_tuple = lambda series_or_value: (series_or_value,) if type(series_or_value) is str else tuple(series_or_value.values)
        df_stop_graph["overlapping_service_patterns"] = df_stop_graph_no_endings[["stop_id_unique", "next_stop"]].apply(
            lambda row: (
                get_as_tuple(service_patterns_by_next_current_stop.loc[row["stop_id_unique"], row["next_stop"]]) 
            ),
            axis=1
        ).reindex_like(df_stop_graph)
        #merged_overlaps = df_merged_stops_service_pattern.reset_index().groupby( #TODO: couldn't this just use drop_duplicates()
        #    ["stop_id_unique", "overlapping_service_patterns"]
        #).first().reset_index(level=1)["overlapping_service_patterns"] # TODO: For any patterns that have some intersection, take the union of all of them so that all lists of service patterns are disjoint. also drop empty arrays
        self._df_overlapping_service_patterns = self._get_overlaps_from_stop_graph(df_stop_graph)
        self._df_stop_graph = df_stop_graph.copy()
        self._graph_generated = True

    @staticmethod
    def _get_overlaps_from_stop_graph(df_stop_graph):
        # Helper function
        def condense_overlaps(overlaps):
            overlaps_no_empty = [
                overlap for overlap in overlaps if (not safe_is_na(overlap)) and len(overlap) > 0
            ]
            skip_indices = []
            out = []
            for i, overlap_i in enumerate(overlaps_no_empty):
                out_array = overlap_i
                if i in skip_indices:
                    continue
                for j, overlap_j in enumerate(overlaps_no_empty):
                    if np.intersect1d(overlap_i, overlap_j).size != 0:
                        out_array = np.union1d(out_array, overlap_j)
                        skip_indices.append(j)
                out.append(tuple(out_array))
            return out

        merged_overlaps = df_stop_graph.reset_index(drop=False).drop_duplicates(
            subset=["stop_id_unique", "overlapping_service_patterns"]
        )#.set_index("stop_id_unique")["overlapping_service_patterns"]
        merged_overlaps_condensed = merged_overlaps.groupby("stop_id_unique")["overlapping_service_patterns"].apply(condense_overlaps)
        df_merged_overlaps_exploded = merged_overlaps_condensed.explode().reset_index(drop=False)
        df_merged_overlaps_exploded["overlap_id"] = df_merged_overlaps_exploded[
            "stop_id_unique"
        ] + "_" + df_merged_overlaps_exploded.groupby(
            "stop_id_unique"
        )["overlapping_service_patterns"].cumcount().astype(str)
        return df_merged_overlaps_exploded.set_index("overlap_id")

    def _create_route_graph_lazily(self):
        if not self._graph_generated:
            self._create_route_graph()

    def _create_stop_clusters_lazily(self):
        if not self._clusters_generated:
            self._cluster_stop_dataframes()
        
    @staticmethod  
    def _get_headway_combination_string(headways):
        raise NotImplementedError

    def _get_stop_times_for_time_period(self, start_time: dt.time, end_time: dt.time):
        start_time_seconds = time_to_int(start_time)
        end_time_seconds = time_to_int(end_time)
        return self.df_stop_times.loc[
            (self.df_stop_times["departure_time"] >= start_time_seconds) 
            & (self.df_stop_times["departure_time"] <= end_time_seconds)
        ]

    def _get_stop_times_grouped_by_routes(self, period):
        df_stop_times_in_period = self._get_stop_times_for_time_period(
            period.start, period.end
        )
        df_stop_times_with_routes = df_stop_times_in_period.merge(
            self.df_service_patterns["route_id"],
            how="left",
            left_on="service_pattern_id_unique",
            right_index=True,
            validate="many_to_one"
        )
        df_stop_times_with_routes["combined_time"] = df_stop_times_with_routes["departure_time"].fillna(
            df_stop_times_with_routes["arrival_time"] # Prefer departure time, but allow arrival time where only available
        )
        stop_times_grouped = df_stop_times_with_routes.sort_values("combined_time").groupby(
            ["stop_id_unique", "route_id"]
        )["combined_time"]
        return stop_times_grouped
    
    def _get_stop_times_grouped_by_service_overlaps(self, period, min_trips):
        df_stop_times_in_period = self._get_stop_times_for_time_period(
            period.start, period.end
        )
        df_overlap_ids = self.df_overlapping_service_patterns.explode(
            "overlapping_service_patterns"
        ).rename(columns={"overlapping_service_patterns": "service_pattern_id_unique"})
        df_stop_times_with_overlap_id = df_stop_times_in_period.merge(
            df_overlap_ids.drop_duplicates(subset=["stop_id_unique", "service_pattern_id_unique"]).reset_index(drop=False),
            how="left",
            on=["stop_id_unique", "service_pattern_id_unique"],
            validate="many_to_one",
        )
        # Get values that should be excluded 
        stop_counts = df_stop_times_in_period.index.get_level_values(0).value_counts()
        stops_to_keep = stop_counts.loc[stop_counts >= min_trips].index.values
        df_stop_times_with_overlap_id_filtered = df_stop_times_with_overlap_id.loc[
            df_stop_times_with_overlap_id["stop_id_unique"].isin(stops_to_keep)
        ].copy()
        df_stop_times_with_overlap_id_filtered["combined_time"] = df_stop_times_with_overlap_id_filtered["departure_time"].fillna(
            df_stop_times_with_overlap_id_filtered["arrival_time"].copy() # Prefer departure time, but allow arrival time where only available
        )
        stop_times_grouped = df_stop_times_with_overlap_id_filtered.sort_values("combined_time").groupby(
            ["stop_id_unique", "overlap_id"]
        )["combined_time"]
        return stop_times_grouped

    def _get_headways_for_group_helper(self, stop_times_grouped, percentile):
        headway_function = self.get_headway_function(percentile)
        headway_seconds = stop_times_grouped.apply(headway_function)
        headway_minutes = headway_seconds / 60.
        return headway_minutes
    
    def _get_frequencies_for_group_helper(self, stop_times_grouped, period):
        time_series_length = (
            dt.datetime.combine(ARBITRARY_DATE, period.end) - dt.datetime.combine(ARBITRARY_DATE, period.start)
        )
        frequency_function = self.get_frequency_function(time_series_length)
        frequencies = stop_times_grouped.apply(frequency_function)
        return frequencies

    def _transform_route_ids(self, feed_id, route_ids):
        """Rename the route ids to avoid the possibility of conflict between route ids from different feeds"""
        out = []
        for route_id in route_ids:
            route_id_count = 0
            if route_id in self.route_id_current_counts:
                self.route_id_current_counts[route_id] += 1
                route_id_count = self.route_id_current_counts[route_id]
            route_id_suffix = ""
            while route_id_count > 9:
                route_id_suffix += "9"
                route_id_count %= 10
            route_id_suffix += str(route_id_count)
                                                                     
            base_id = f"{feed_id}_{route_id}_{route_id_suffix}"
            out.append(base_id)
        return out

    def _reset_graph_status(self):
        self._graph_generated = False
        self._clusters_generated = False
        self._route_groups_generated = False
        self._overlap_groups_generated = False

    @staticmethod
    def _transform_service_pattern_ids(feed_id, service_pattern_ids):
        return concatenate_id_lists(feed_id, service_pattern_ids)

    @staticmethod
    def _transform_trip_ids(feed_id, trip_ids):
        return concatenate_id_lists(feed_id, trip_ids)

    @staticmethod
    def _transform_individual_stop_id(feed_id, stop_id):
        return f"{feed_id}_{stop_id}"

    @staticmethod
    def _transform_stop_ids(feed_id, stop_ids):
        return concatenate_id_lists(feed_id, stop_ids)

    @staticmethod    
    def get_headway_function(percentile):
        return lambda time_series: TransitNetwork.get_headway(time_series, percentile)

    @staticmethod
    def get_headway(time_series, percentile):
        headways = (time_series.shift(-1) - time_series).to_numpy()
        if headways.size == 0 or (headways.size == 1 and np.isnan(headways[0])):
            return np.nan
        return np.percentile(
            headways[:-1], percentile
        )

    @staticmethod
    def get_frequency_function(period_length):
        return lambda time_series: TransitNetwork.get_frequency(time_series, period_length)

    @staticmethod
    def get_frequency(time_series: pd.Series, period_length: dt.timedelta):
        return time_series.count() / (period_length.seconds / 3600.)