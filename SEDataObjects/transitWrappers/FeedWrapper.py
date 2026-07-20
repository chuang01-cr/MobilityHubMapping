import pathlib
import partridge as ptg
import datetime as dt
import numpy as np
import pandas as pd
import geopandas as gpd
import traceback

from SEDataObjects.transitWrappers.constants import GTFS_ROUTE_TYPE_TO_ID_MAP
from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.utils import transform_shapely_geometry


class FeedWrapper:
    # Public
    feed_loaded = False
    service_loaded = False
    gdf_stops = None,
    
    def __init__(
            self,
            feed_path: str | pathlib.Path,
            feed_id: str,
            filter_area,
            filter_area_crs,
            min_trips: int
        ):
        """
        A wrapper for a GTFS feed that loads feeds with partridge and extracts service patterns.
        Currently quite slow to load, and does some really messy stuff with tuples in dataframes

        :param feed_path: A path to GTFS feed, as a zip
        :param feed_id: An arbitrary id to assign to a feed
        :param filter_area: The area in which to filter the feed
        :param filter_area_crs: The EPSG code for the CRS of filter_area. Does not need to be a proejcted CRS
        :param min_trips: The minimum number of trips required to save a service pattern
        """
        self.path = pathlib.Path(feed_path).resolve()
        self.id = feed_id
        # Helper functions for printing exceptions 
        def print_partridge_warning(e):
            print(f"WARN: Partridge failed to read a GTFS feed. This is likely because the feed was improperly formatted. Traceback follows:")
            print(traceback.print_exception(e))
        def print_bad_gtfs_warning(e):
            print(f"WARN: GTFS feed could not be read, possibly because it was missing a required field. Exception is below:")
            print(e)
        # Get the service ids associated with the busiest service day in the feed (standard in Partridge example code)
        try:
            _, service_ids = ptg.read_busiest_date(str(self.path))
        except Exception as e:
            print_partridge_warning(e)
            return
        # Create a Partridge feed object for the busiest service day
        view = {"trips.txt": {"service_id": service_ids}}
        try:
            self.feed = ptg.load_feed(str(self.path), view)
        except Exception as e:
            print_partridge_warning(e)
            return
        # Create a routes df with some custom information
        try:
            self.df_routes = self.feed.routes[
                ["route_id", "route_type"]
            ].set_index("route_id")
        except AttributeError as e:
            print_bad_gtfs_warning(e)
            return 
        df_feed_routes_reindexed = self.feed.routes.set_index("route_id").copy()
        if "route_short_name" in df_feed_routes_reindexed:
            self.df_routes["route_short_name"] = df_feed_routes_reindexed["route_short_name"].copy()
        else:
            self.df_routes["route_short_name"] = np.nan
        if "route_long_name" in df_feed_routes_reindexed:
            self.df_routes["route_long_name"] = df_feed_routes_reindexed["route_long_name"].copy()
        else:
            self.df_routes["route_long_name"] = np.nan
        self.df_routes["route_aggregated_name"] = self.df_routes["route_short_name"].fillna(
            self.df_routes["route_long_name"].copy().fillna(
                pd.Series(self.df_routes.index, index=self.df_routes.index).dropna()
            )
        )
        print("partridge loaded")
        self.df_routes["route_mode_key"] = self.df_routes["route_type"].map(GTFS_ROUTE_TYPE_TO_ID_MAP)

        # Create stops df
        df_stops = self.feed.stops.copy()
        gdf_stops = gpd.GeoDataFrame(
            df_stops,
            geometry=gpd.points_from_xy(df_stops["stop_lon"], df_stops["stop_lat"]),
            crs=GEODESIC_CRS,
        )
        gdf_stops_in_area = gdf_stops.loc[
            gdf_stops.within(transform_shapely_geometry(filter_area_crs, GEODESIC_CRS, filter_area))
        ]
        if gdf_stops_in_area.size == 0:
            # No stops in area
            self.df_trips = pd.DataFrame()
            self.df_stop_times = pd.DataFrame()
            self.df_routes = pd.DataFrame()
            return
        self.gdf_stops = gdf_stops_in_area.copy().set_index("stop_id")
        df_stop_times_filtered = self._filter_stop_times(self.feed.stop_times, self.gdf_stops.index)
        # Get dfs with trip patterns
        print("loading stop tuple")
        df_trips_with_stop_tuple = self._get_trips_by_stop_tuple(df_stop_times_filtered)
        print("loaded stop tuple")
        self.df_service_patterns = self._get_service_pattern_df(df_trips_with_stop_tuple, min_trips)
        self.df_trips = self._get_trips_by_service_pattern_id(
            df_trips_with_stop_tuple, self.df_service_patterns
        )
        self.df_stop_times = self._merge_stop_times_service_pattern(
            df_stop_times_filtered,
            self.df_trips
        )
        print("trip patterns loaded")

        self.feed_loaded = True 
        print("feed loaded")
    
    def get_agency_name(self) -> str | float:
        """Get the name of the agency if it is unique. Returns an ambiguous response if the feed contains multiple agencies"""
        #TODO: need to make agency name a route field not an overall field
        if not self.feed_loaded:
            return self._print_feed_not_loaded_error()
        if self.feed.agency.index.size == 1:
            return self.feed.agency.agency_name.iloc[0]
        else:
            return "Agency has Multiple Names"
    
    def get_agency_url(self) -> str:
        """Get the url of the agency if it is unique. Returns an ambiguous response if the feed contains multiple agencies"""
        if not self.feed_loaded:
            return self._print_feed_not_loaded_error()
        if self.feed.agency.index.size == 1:
            return self.feed.agency.agency_url.iloc[0]
        else:
            return "Agency has multiple URLs"
    
    def get_feed_loaded_correctly(self):
        """Return True if the feed is currently loaded, and False if it is not"""
        return self.feed_loaded

    def get_routes_serving_stop(
        self,
        stop_id,
    ):
        """Returns two tuples with the 0th containing route ids and the 1st containing direction ids serving the provided stop_id"""
        trip_ids_serving_stop = self.feed.stop_times.loc[self.feed.stop_times.stop_id == stop_id, "trip_id"]
        id_columns = ("route_id",)
        has_direction_id = False
        if "direction_id" in self.feed.trips.columns:
            id_columns = ("route_id", "direction_id")
            has_direction_id = True
        df_route_and_direction_ids_serving_stop = self.feed.trips.loc[ # inefficient?
            self.feed.trips.trip_id.isin(trip_ids_serving_stop),
            id_columns,
        ].drop_duplicates()
        if not has_direction_id:
            df_route_and_direction_ids_serving_stop["direction_id"] = 0
        route_direction_pair = (
            tuple(df_route_and_direction_ids_serving_stop["route_id"].to_numpy()),
            tuple(df_route_and_direction_ids_serving_stop["direction_id"].to_numpy(),)
        )
        return route_direction_pair

    def get_service_patterns_for_route(self, route_id):
        """Get all service patterns for the specified route id"""
        if route_id not in self.df_routes.index:
            raise KeyError(f"Route {route_id} is not present in feed")
        return self.df_service_patterns.index.loc[self.df_service_patterns["route_id"] == route_id]

    # "Private"
    def _get_trips_by_stop_tuple(self, df_stop_times):
        df_stop_times_indexed_by_trip = df_stop_times.sort_values(
            ["stop_sequence", "trip_id"], kind="stable"
        ).set_index("trip_id")
        df_trips = self.feed.trips.set_index("trip_id")
        df_trips_filtered = df_trips.loc[df_stop_times_indexed_by_trip.index.unique()]

        def safe_get_tuple(value_or_series):
            try:
                return tuple(value_or_series.to_numpy())
            except AttributeError:
                return tuple(value_or_series,)
            
        df_trips_filtered["stop_tuple"] = pd.Series(
            df_trips_filtered.index, index=df_trips_filtered.index
        ).map( #TODO: this is probably slower than a merge and groupby
            lambda trip_id: (
                safe_get_tuple(df_stop_times_indexed_by_trip.loc[trip_id, "stop_id"]) 
            )
        )
        return df_trips_filtered.copy()

    def _get_service_pattern_df(self, df_trips_with_stop_tuple, min_combination_count):
        service_pattern_counts = df_trips_with_stop_tuple["stop_tuple"].value_counts()
        df_service_patterns = df_trips_with_stop_tuple[["route_id", "stop_tuple"]].drop_duplicates()
        df_service_patterns_filtered = df_service_patterns.set_index("stop_tuple").loc[
            service_pattern_counts.loc[df_service_patterns["stop_tuple"]] >= min_combination_count
        ].reset_index().sort_values("route_id")
        df_service_patterns_filtered["temp_count"] = df_service_patterns_filtered.index.copy()
        df_service_patterns_filtered[
            "service_pattern_id_no_route"
        ] = df_service_patterns_filtered.groupby("route_id")["temp_count"].cumcount().astype(str)
        df_service_patterns_filtered.drop("temp_count", axis=1, inplace=True)
        df_service_patterns_filtered["service_pattern_id"] = df_service_patterns_filtered["route_id"] + "_" + df_service_patterns_filtered["service_pattern_id_no_route"]
        df_service_patterns_with_mode = df_service_patterns_filtered.merge(
            self.df_routes["route_type"].map(
                GTFS_ROUTE_TYPE_TO_ID_MAP
            ),
            how="left",
            left_on="route_id",
            right_index=True,
            validate="many_to_one"
        )
        return df_service_patterns_with_mode

    @staticmethod
    def _get_trips_by_service_pattern_id(df_trips_with_stop_tuple, df_service_patterns):
        return df_trips_with_stop_tuple.reset_index().merge(
            df_service_patterns[["stop_tuple", "service_pattern_id", "route_id"]],
            how="left",
            on=["stop_tuple", "route_id"],
            validate="many_to_one"
        ).drop("stop_tuple", axis=1).set_index("service_pattern_id").sort_index().copy()

    def _filter_stop_times(self, df_stop_times, stop_ids_to_keep):
        return df_stop_times.set_index("stop_id").loc[stop_ids_to_keep].reset_index().copy()

    @staticmethod
    def _merge_stop_times_service_pattern(df_stop_times, df_trips_with_service_patterns):
        df_stop_times_with_service_pattern_id = df_stop_times.merge(
            df_trips_with_service_patterns.reset_index()[
                ["trip_id", "service_pattern_id"]
            ],
            how="left",
            on=["trip_id"],
            validate="many_to_one"
        )
        return df_stop_times_with_service_pattern_id.reset_index()
    @staticmethod
    def _get_headway_column_name(time_start: dt.time, time_end: dt.time) -> str:
        """Get the column name for the headway column with the given start and end times"""
        return f"headway_{time_start.hour}:{time_start.min}-{time_end.hour}-{time_end.min}"

    def _print_feed_not_loaded_error(self):
        raise RuntimeError("Cannot call any functions on an unloaded feed")



