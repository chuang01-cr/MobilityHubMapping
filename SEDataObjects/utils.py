import hashlib
import pathlib
import subprocess
from typing import Iterable
from urllib.error import HTTPError
from fiona.errors import DriverError
import folium
import pandas as pd
from pyproj import Transformer, Geod
import numpy as np
import requests
import shapely
import datetime as dt
from playwright.async_api import async_playwright, Playwright, Error, TimeoutError
import zipfile
import geopandas as gpd
from scipy.stats import percentileofscore
import appdirs

from SEDataObjects import SpatialDataObject

def basic_circle_marker(fillColor: str, **kwargs) -> folium.CircleMarker:
    kwargs_to_pass = dict(kwargs)
    kwargs_to_pass["color"] = "black"
    kwargs_to_pass["fillColor"] = fillColor
    if "fillOpacity" not in kwargs_to_pass:
        kwargs_to_pass["fillOpacity"] = 1
    if "radius" not in kwargs_to_pass:
        kwargs_to_pass["radius"] = 5
    if "weight" not in kwargs_to_pass:
        kwargs_to_pass["weight"] = 0.25
    return folium.CircleMarker(**kwargs_to_pass)

def transform_shapely_geometry(
    from_crs: (str | int),
    to_crs: (str | int),
    geom: (shapely.Geometry)
):
    transformer = Transformer.from_crs(from_crs, to_crs, always_xy=True)
    return shapely.ops.transform(
        transformer.transform,
        geom
    )

def safe_is_na(value: object) -> bool:
     return value is None or (type(value) == float and np.isnan(value))

def get_str_or_na(value : (int | float | str | None)) -> (str | float):
    if type(value) != str and (np.isnan(value) or value is None):
            return np.nan
    return str(value)

def yes_no_to_bool(value: (str | float | None)) -> (str | float):
    processed = get_str_or_na(value)
    if safe_is_na(processed):
         return np.nan
    elif value == "yes":
         return True
    elif value == "no":
         return False
    else:
         return np.nan

def filter_two_corresponding_arrays(reference, corresponding, other):
    assert len(corresponding) == len(other)
    corresponding_other_map = {corresponding[i]: other[i] for i in range(len(corresponding))}
    intersected = np.intersect1d(np.array(reference), np.array(corresponding))
    other_filtered = [corresponding_other_map[i] for i in intersected]
    return tuple(intersected), tuple(other_filtered)

def time_to_int(time: dt.time):
    return int(time.hour * 3600 + time.minute * 60 + time.second + time.microsecond/1000)

point_or_poly = shapely.Point | shapely.MultiPolygon | shapely.Polygon | shapely.MultiPolygon | shapely.LineString
def small_geodesic_polygons_to_points(
    geom: point_or_poly,
    max_area_square_meters: int,
    ellipsoid: str = "WGS84"
) -> point_or_poly:
    assert type(geom) in (shapely.Point, shapely.MultiPolygon, shapely.Polygon, shapely.MultiPolygon, shapely.LineString)
    # If the geometry is not a polygon, return
    if type(geom) is shapely.Point or type(geom) is shapely.MultiPoint:
         return geom
    
    # Calculate total area of the polygon or multipolygon
    geod = Geod(ellps=ellipsoid)
    def get_geodesic_area(geom: shapely.Polygon):
         return abs(geod.geometry_area_perimeter(geom)[0])
    area = 0
    if type(geom) in [shapely.Polygon, shapely.LineString]:
        area = get_geodesic_area(geom)
    if type(geom) is shapely.MultiPolygon:
        area = sum(map(get_geodesic_area, geom.geoms))
    # If the polygon is small, return it as a point
    if area < max_area_square_meters:
         return geom.centroid
    # Otherwise, return the original object
    return geom

def download_json_safely(url: str): #TODO: consider moving to utils.oy
    r = requests.get(url)
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        print(f"WARN: Error downloading {url}:")
        print(e)
        return None
    try:
        return r.json()
    except requests.JSONDecodeError:
        print(f"WARN: URL {url} did not lead to a valid JSON file. Output was:")
        print(r.text())
        return None

def download_file_with_requests(url: str, output_path: str | pathlib.Path, max_chunk_size: int): #TODO: return type
    sha1_hash = hashlib.new("sha1")
    with requests.get(url, stream=True) as r:
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            print(f"WARN: Requests download failed with the following error")
            print(e.response.text)
            return None
        with open(pathlib.Path(output_path).resolve(), "wb") as f:
            for chunk in r.iter_content(chunk_size=max_chunk_size): 
                if chunk:
                    f.write(chunk)
                    sha1_hash.update(chunk)
        if not zipfile.is_zipfile(output_path):
            print("WARN: File downloaded, but a zip file was not returned")
            return None
    return sha1_hash

def download_latest_feed_version_from_transitland(
        feed_id: str, output_path: str | pathlib.Path, max_chunk_size: int, api_key: str
    ):
    url = f"https://transit.land/api/v2/rest/feeds/{feed_id}/download_latest_feed_version?api_key={api_key}"
    return download_file_with_requests(url, output_path, max_chunk_size)

def get_sha1_hash(f, max_chunk_size, start_bytes=None):
    sha1_hash = hashlib.new("sha1")
    if start_bytes is not None:
        sha1_hash.update(start_bytes)
    while chunk := f.read(max_chunk_size):
        sha1_hash.update(chunk)
    return sha1_hash

def download_file_with_curl(url: str, output_path: str | pathlib.Path, error_id: str, max_chunk_size: int): #TODO: return type
    curl_command = f"curl -o {pathlib.Path(output_path).resolve()} {url}"
    subprocess.call(curl_command, shell=True) #TODO: internal screaming
    try:
        # Attempt to open the downloaded feed as text - this should fail if the object is actually a feed
        with open(output_path, "rb") as f:
            if not zipfile.is_zipfile(f):
                return None
            downloaded = f.read(max_chunk_size)
            try:
                if "ACCESS DENIED" in downloaded.decode("utf-8").upper():
                    print(
                        f"WARN: Curl Download still refused for {error_id}"
                    )
                else:
                    print(
                        f"WARN: The url at {url} for {error_id} responded with the following text rather than a feed"
                    )
                    print(downloaded.decode("utf-8"))
                    return None
            except UnicodeDecodeError:
                # This means that the file isn't text, so it likely is a valid feed
                print("INFO: Curl Download successful")
                # Get hash
                return get_sha1_hash(f, max_chunk_size, start_bytes=downloaded)
    except FileNotFoundError:
        print("WARN: Curl download did not succeed")
    return None

async def download_file_with_playwright(url: str, output_path: str | pathlib.Path, error_id: str, max_chunk_size: int):
    succeeded = False
    async def attempt_download(browser) -> bool:
        succeeded = False
        page = await browser.new_page()
        print(f"INFO: Downloading {url} with Playwright")
        try:
            async with page.expect_download(timeout=10000) as download_info:
                try:
                    await page.goto(url)
                    await page.screenshot(path=output_path.with_name(f"{error_id}.png"))
                except Error as e:
                    print(e)
                    download = await download_info.value
                    await download.save_as(output_path)
                    succeeded = True
        except TimeoutError as e:
            print(f"WARN: Playwright download for {error_id} timed out")
        return succeeded

    async with async_playwright() as p:
        print("INFO: About to launch Firefox browser")
        headless_browser = await p.firefox.launch(headless=True)
        print("INFO: Browser launched")
        succeeded = await attempt_download(headless_browser)
        print("INFO: Download attempted")
        await headless_browser.close()
        if succeeded and zipfile.is_zipfile(output_path):
            with open(output_path, "rb") as f:
                return get_sha1_hash(f, max_chunk_size)
        print("INFO: Trying a headless download. A browser window will now open for 10 seconds")
        headed_browser = await p.firefox.launch(headless=False)        
        succeeded = await attempt_download(headed_browser);
        await headed_browser.close()
        if succeeded and zipfile.is_zipfile(output_path):
            with open(output_path, "rb") as f:
                return get_sha1_hash(f, max_chunk_size)
    print("WARN: Playwright download did not return zip")
    return None

def get_scores_for_all_objects(objects: list[SpatialDataObject], object_names: list[str]) -> gpd.GeoDataFrame:
    assert len(objects) == len(object_names)
    gdfs = []
    for object, name in zip(objects, object_names):
        gdf = object.get_scores_with_geometry()
        gdf["type"] = name
        gdfs.append(gdf)
    return pd.concat(gdfs)

def overlap_and_weight_values(gdf_keep_geometry, gdf_keep_data, keep_columns, local_crs):
    # Overlay the current and smart location block groups and get the area that the 2021 block groups overlap the 2018 block groups
    gdf_keep_geometry_projected = gdf_keep_geometry.to_crs(local_crs)
    gdf_keep_data_projected = gdf_keep_data.to_crs(local_crs)
    gdf_keep_geometry_projected["original_index"] = gdf_keep_geometry_projected.index
    gdf_keep_geometry_projected["original_area"] = gdf_keep_geometry_projected.area
    gdf_bgs_overlapped = gdf_keep_geometry_projected.overlay(gdf_keep_data_projected, how="intersection")
    # Round values that are gvery close to 1 or 0 to avoid floating point errors and to discount very small overlaps
    gdf_bgs_overlapped["area_proportion"] = gdf_bgs_overlapped.area / gdf_bgs_overlapped["original_area"]
    gdf_bgs_overlapped.loc[gdf_bgs_overlapped["area_proportion"] < 0.01, "area_proportion"] = 0
    gdf_bgs_overlapped.loc[gdf_bgs_overlapped["area_proportion"] > 0.99, "area_proportion"] = 1
    # Infer the value for the relevant value of the 2021 block groups, based on the proportion of overlap with each of the 2018 block groups
    df_weighted_values = pd.concat([
        gdf_bgs_overlapped[keep_columns].multiply(
            gdf_bgs_overlapped["area_proportion"], axis="index"
        ),
        gdf_bgs_overlapped["original_index"],
    ], axis=1)
    df_inferred_values = df_weighted_values.groupby("original_index")[keep_columns].sum()
    assert (gdf_keep_geometry.sort_index().index == df_inferred_values.sort_index().index).all()
    gdf_inferred_values = gpd.GeoDataFrame(
        df_inferred_values.sort_index(),
        geometry=gdf_keep_geometry.sort_index().geometry
    )
    return gdf_inferred_values

#TODO: move to utils.py
def get_quantile_ranking_series(s: pd.Series) -> pd.Series:
    dropped = s.dropna()
    return pd.Series(
        get_quantile_ranking(dropped), index=dropped.index
    ).reindex(s.index)
def get_quantile_ranking(a: Iterable[float | int]) -> np.array:
    return [percentileofscore(a, i, kind="mean") / 100 for i in a]

def call_pygris_with_error_handling(pygris_function, *args, **kwargs):
    try:
        return pygris_function(*args, **kwargs)
    except (HTTPError, DriverError) as error:
        raise_tiger_http_error(error)

def raise_tiger_http_error(error):
    pygris_cache_dir = appdirs.user_cache_dir("pygris")
    print(
        f"The TIGER portal is currently unavailable. Please source files in the below exception from a mirror, ensure they have the default name, and place them in the Pygris cache folder: {pygris_cache_dir}"
    )
    raise error