import pathlib
from typing import Callable
import numpy as np
import pandas as pd
import geopandas as gpd
import pygris
import shapely
from pyproj import CRS
import folium
import osmnx as ox
from osmnx import _errors as OsmnxExceptions
from SEDataObjects.constants import GEODESIC_CRS
from SEDataObjects.utils import basic_circle_marker, call_pygris_with_error_handling, raise_tiger_http_error, safe_is_na
from .SpatialDataObject import SpatialDataObject

PARKING_SEARCH_DISTANCE_METERS_ADDRESS = 80
PARKING_SEARCH_DISTANCE_METERS_LAT_LON = 40
LOCAL_CRS_DO_NOT_INCLUDE = 32611
OSM_SURFACE_PARKING_TYPES = ["surface", "carports"]
FTA_SURFACE_PARKING = "Surface Parking Lot"
FTA_PARKING_STRUCTURE = "Parking Structure"
PARKING_QUERY = {"amenity": ["parking"]}
PARKING_FILTER = { 
    "Facility Type": [
        "Parking Structure",
        "Surface Parking Lot"
    ],
}
NTD_FIELDS = ["NTD ID", "Agency Name", "Facility Type", "Facility Name", "Notes"]

class FTAFacilityInventoryDataObject(SpatialDataObject):
    _gdf = gpd.GeoDataFrame
    name = "ntd_transit_agency_parking"

    def __init__(
        self,
        fta_path: (str | pathlib.Path),
        osm_cache_folder: (str | pathlib.Path),
        fta_sheet_name: (str | None) = None,
    ):
        self.fta_path = pathlib.Path(fta_path)
        self.osm_cache_folder = pathlib.Path(osm_cache_folder)
        self.sheet_name = fta_sheet_name

    def load_data(
        self,
        load_area: (shapely.MultiPolygon | shapely.Polygon),
        load_area_crs: int
    ) -> None:
        # Set OSMNX cache
        old_cache_path = ox.settings.cache_folder
        ox.settings.cache_folder = self.osm_cache_folder
        # Helper functions
        def safe_geocode(query_address: str) -> gpd.GeoDataFrame:
            # Get nearby parking locations from the address in query_address using OSM. Return na if query_address is na
            if safe_is_na(query_address):
                return np.nan
            try:
                print(f"Processing {query_address}")
                output = ox.geocoder.geocode(query_address)
                print(f"INFO: Successfully geocoded {query_address}")
                return output
            except OsmnxExceptions.InsufficientResponseError:
                print(f"WARN: {query_address} could not be geocoded")
                return np.nan
        def get_parking_from_lat_lon(
                latitude: float, longitude: float, search_distance: int, not_filter: dict[str, str | list[str]] = {}, keep_columns: list[str] = []
            ) -> tuple[gpd.GeoSeries, pd.DataFrame] | tuple[float, float]:
            # Get nearby parking locations from latitude / longitude in query_address using OSM. Return na if query_address is na
            if safe_is_na(latitude) or safe_is_na(longitude):
                return np.nan, np.nan
            try:
                # Call OSMNX to find parking spaces
                output = ox.features_from_point((latitude, longitude), PARKING_QUERY, dist=search_distance)
            except OsmnxExceptions.InsufficientResponseError:
                print(f"WARN: ({latitude}, {longitude}) could not be assigned to parking")
                return np.nan, np.nan
            # Filter out values based on not_filter
            for filter in not_filter:
                if filter in output.columns:
                    if type(not_filter[filter]) is str:
                        output = output.loc[output[filter] != not_filter[filter]]
                    elif type(not_filter[filter]) == list:
                        output = output.loc[~output[filter].isin(not_filter[filter])]
                    else:
                        raise TypeError("Filter values must be a string or a list of strings")
            if len(output) == 0:
                print(f"No spaces found matching the filter for ({latitude}, {longitude})")
                return np.nan, np.nan
            print(f"INFO: Found parking for ({latitude}, {longitude})")
            # Ensure that there is a value for each element of keep_columns, even if it is na
            output[np.intersect1d(np.setxor1d(output.columns, keep_columns), keep_columns)] = np.nan
            # Return the output with only the geometry column and the columns in keep_columns
            return output.geometry, output[keep_columns]

        # Get rows specified in the parking filter
        if not self.sheet_name:
            df_inventory = pd.read_excel(self.fta_path)
        else:
            df_inventory = pd.read_excel(self.fta_path, sheet_name=self.sheet_name)
        for column in PARKING_FILTER.keys():
            df_inventory = df_inventory.loc[
                df_inventory[column].str.strip().isin(PARKING_FILTER[column])
            ].copy()
        # Get states that overlap with the load area
        gdf_states = call_pygris_with_error_handling(pygris.states, cb=False, year=2023, cache=True)
        gdf_relevant_states = gdf_states.loc[gdf_states.intersects(load_area), "STUSPS"]
        # Filter the inventory to only contain entries within the relevant states
        df_inventory = df_inventory.loc[df_inventory["State"].isin(gdf_relevant_states)]    
        # Split DF into entries containing a latitude and longitude and those that do not
        index_inventory_no_lat_lon = (df_inventory["Latitude"].isna()) | (df_inventory["Longitude"].isna())
        df_inventory_no_lat_lon = df_inventory.loc[
            index_inventory_no_lat_lon,
            ["Street Address", "City", "State", "ZIP Code"]
        ]
        # Geocode results with no latitude / longitude
        geocode_results = pd.Series(
            zip(
                df_inventory_no_lat_lon["Street Address"],
                df_inventory_no_lat_lon["City"],
                df_inventory_no_lat_lon["State"],
                df_inventory_no_lat_lon["ZIP Code"].astype(int).astype(str)
            ),
            index=df_inventory_no_lat_lon.index
        ).map(
            lambda x: safe_geocode(f"{x[0]}, {x[1]}, {x[2]}, US, {x[3]}")
        ).dropna()
        # Join geocoded lat/lon with given lat/lon
        df_geocode_results = pd.DataFrame(
            geocode_results.to_list(),
            columns=["Latitude", "Longitude"],
            index=geocode_results.index
        )
        df_geocode_results["from_address"] = True
        df_inventory["from_address"] = False
        df_inventory[["filled_latitude", "filled_longitude", "from_address"]] = df_inventory[["Latitude", "Longitude", "from_address"]].where(
            ~df_inventory[["Latitude", "Longitude"]].isna().any(axis=1),
            df_geocode_results
        )
        df_inventory = df_inventory.loc[
            gpd.points_from_xy(df_inventory["Longitude"], df_inventory["Latitude"], crs=GEODESIC_CRS).to_crs(load_area_crs).within(load_area)
        ]
        # Get data about parking from OSM
        parking_response = pd.Series(
            zip(
                df_inventory["filled_latitude"],
                df_inventory["filled_longitude"],
                df_inventory["from_address"]
            ),
            index=df_inventory.index
        ).map(
            lambda x: get_parking_from_lat_lon(
                latitude=x[0], 
                longitude=x[1], 
                search_distance=PARKING_SEARCH_DISTANCE_METERS_ADDRESS if x[2] else PARKING_SEARCH_DISTANCE_METERS_LAT_LON, 
                not_filter={"access": ["private"]},
                keep_columns=["parking"]
            )
        ).dropna()
        # Convert the result into a dataframe with one entry per OSM parking response
        df_inventory["parking_geometry"] = parking_response.map(lambda x: x[0])
        df_inventory["parking_type"] = parking_response.map(
            lambda x: [] if type(x[1]) is not pd.DataFrame else list(x[1]["parking"])
        )
        assert(
            (
                df_inventory["parking_geometry"].dropna().map(len) 
                == df_inventory["parking_type"].loc[df_inventory["parking_type"].map(len) > 0].map(len)
            ).all()
        )
        df_inventory_exploded = df_inventory.explode(
            ["parking_geometry", "parking_type"]
        ).reset_index(
            names=["original_index"]
        )
        # Filter rows so that the parking type returned by OSM matches the FTA parking type
        df_inventory_exploded = df_inventory_exploded.loc[
            (
                (df_inventory_exploded["parking_type"].map(
                    lambda osm_parking_value: FTA_SURFACE_PARKING if osm_parking_value in OSM_SURFACE_PARKING_TYPES else FTA_PARKING_STRUCTURE
                ) == df_inventory_exploded["Facility Type"])
            ) | (
                df_inventory_exploded["parking_type"].isna()
            )
        ]
        # Get a GDF of each parking geometry
        gdf_all_spaces_with_geometry = gpd.GeoDataFrame(
            df_inventory_exploded.dropna(subset=["parking_geometry"]).rename(columns={"parking_geometry": "geometry"}),
            crs=GEODESIC_CRS
        ).to_crs(LOCAL_CRS_DO_NOT_INCLUDE)
        # Get a Geoseries of each given or geocoded lat/lon
        all_points_projected = gpd.GeoSeries(
            gpd.points_from_xy(
                df_inventory_exploded["filled_longitude"], df_inventory_exploded["filled_latitude"], crs=GEODESIC_CRS
            ).to_crs(LOCAL_CRS_DO_NOT_INCLUDE),
            index=df_inventory_exploded.index
        )
        # Filter the GDF for only the closest points (or the only point if there is only one response)
        gdf_all_spaces_with_geometry["distance_to_point"] = gdf_all_spaces_with_geometry.geometry.distance(all_points_projected)
        gdf_inventory_projected = gdf_all_spaces_with_geometry.sort_values(["original_index", "distance_to_point"], ascending=True).groupby("original_index").first()
        gdf_inventory_projected.crs = LOCAL_CRS_DO_NOT_INCLUDE
        gdf_inventory_geodesic = gdf_inventory_projected.to_crs(GEODESIC_CRS)
        # Add points to geometry where OSM polygons cannot be found
        df_spaces_no_geometry = df_inventory_exploded.loc[df_inventory_exploded["parking_geometry"].isna()]
        gdf_spaces_points = gpd.GeoDataFrame(
            df_spaces_no_geometry,
            geometry=gpd.points_from_xy(df_spaces_no_geometry["filled_longitude"], df_spaces_no_geometry["filled_latitude"]),
            crs=GEODESIC_CRS
        )
        gdf_inventory_combined = pd.concat([gdf_inventory_geodesic, gdf_spaces_points])
        # Save the responses that are within the load_area TODO: add an area filter earlier up to avoid geocoding unnecessary places
        gdf_inventory_combined = gdf_inventory_combined.loc[gdf_inventory_combined.within(load_area)].dropna(subset=["geometry"]).copy()
        self._gdf = gpd.GeoDataFrame(gdf_inventory_combined[NTD_FIELDS], geometry=gdf_inventory_combined.geometry)
        # Reset OSMNX cache settings to default
        ox.settings.cache_folder = old_cache_path
        self._set_is_loaded()

    def get_folium_plot(self) -> folium.GeoJson:
        fta_popup = folium.GeoJsonPopup(
            fields=list(np.intersect1d(
                NTD_FIELDS,
                self._gdf.columns
            ))
        )
        light_blue_color = "#12aae6"
        fta_geojson = folium.GeoJson(
            self._gdf,
            marker=basic_circle_marker(light_blue_color),
            style_function = lambda _: {"radius": 15, "fillcolor": light_blue_color},
            popup=fta_popup
        )
        return fta_geojson
