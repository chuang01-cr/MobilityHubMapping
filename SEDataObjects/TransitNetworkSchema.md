# TransitNetwork schemas

### `gdf_stops`

**Type:** Spatial GeoDataFrame with point geometries

**Index:** 
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `stop_id_unique` | A unique stop id that should not be duplicated in the network | `str` |

**Columns:** 
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `feed` | The id of the feed the stop is associated with | `str` |
| `stop_id_original` | The value of `stop_id` as given in `stops.txt` for the feed | `str` |
| `route_ids_original` | An iterable of `route_id`s serving the stop, as given in `trips.txt` for the feed. May have duplicate values if the same route serves the stop in both directions. | `tuple` of `str`s |
| `direction_ids` | An iterable of `direction_id`s for each route that serves the stop, as given in `trips.txt`. Should be the same length of `route_ids_original` and `route_ids_unique`, and the nth value of `direction_ids` should be the direction the nth value of `route_ids_original`/`route_ids_unique` serves the stop in. May have duplicate values if multiple routes serve the stop with the same `direction_id`. | `tuple` of `int`s |
| `direction_ids` | An iterable of `direction_id`s for each route that serves the stop, as given in `trips.txt`. Should be the same length of `route_ids_original` and `route_ids_unique`, and the nth value of `direction_ids` should be the direction the nth value of `route_ids_original`/`route_ids_unique` serves the stop in. May have duplicate values if multiple routes serve the stop with the same `direction_id`. | `tuple` of `int`s |
| `route_ids_unique` | An iterable of unique route ids that each should not be duplicated in the network. May have duplicate values if the same route serves the stop in both directions. | `tuple` of `str`s |

**CRS**: EPSG 4326 (WGS84)

### `df_stop_times`

**Type:** DataFrame

**Index**:
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `stop_id_unique` | A unique stop id that should not be duplicated in the network | `str` |
| `trip_id_unique` | A unique trip id that should not be duplicated in the network | `str` |

**Columns**:
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `feed` | The id of the feed the stop is associated with (will not appear if clustering is enabled) | `str` |
| `stop_id_original` | The value of `stop_id` as given in `stops.txt` for the feed (will not appear if clustering is enabled) | `str` |
| `trip_id_original` | The value of `trip_id` as given in `trips.txt` for the feed | `str` |
| `service_pattern_id_original` | The value of `service_pattern_id` as given in `FeedWrapper.df_service_patterns` | `str` |
| `service_pattern_id_unique` | A unique service pattern id that should not be duplicated in the network | `str` | 
| `arrival_time` | The arrival time at the stop in minutes since midnight on the service day. See details in the gtfs spec in `stop_times.txt` | non-negative `float` |
| `departure_time` | The departure time at the stop in minutes since midnight on the service day. See details in the gtfs spec in `stop_times.txt`  | non-negative `float` |
| `stop_sequence` | The order of stops, as given in `stop_times.txt`. From the GTFS spec: "The values must increase along the trip but do not need to be consecutive." | non-negative `int` |

### `df_service_patterns`

**Type:** DataFrame

**Index:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `service_pattern_id_unique` | A unique service pattern id that should not be duplicated in the network | `str` | 

**Columns:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `feed` | The id of the feed the stop is associated with | `str` |
| `route_id_unique` | A unique route id that should not be duplicated in the network | `str` |
| `mode` | The mode of the service pattern | `ModeClassification` |
| `service_pattern_id_original` | The value of `service_pattern_id` as given in `FeedWrapper.df_service_patterns` | `str` |

### `df_routes`
**Type:** DataFrame

**Index:** 
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `route_id_unique` | A unique route id that should not be duplicated in the network | `str` |

**Columns:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `feed` | The id of the feed the stop is associated with | `str` |
| `route_id_original` | The id of the route as given in the `routes.txt` for the feed | `str`

###  `df_stop_graph` (private)

**Type:** DataFrame

**Index:** A unique integer. Rows represent a stop and a service pattern that serves that stop

**Columns:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `stop_id_unique` | A unique stop id that should not be duplicated in the network | `str` |
| `service_pattern_id_unique` | A unique service pattern id that should not be duplicated in the network | `str` | 
| `stop_sequence` | The order of stops, as given in `stop_times.txt`. From the GTFS spec: "The values must increase along the trip but do not need to be consecutive." | non-negative `int` |
| `next_stop` | The `stop_id_unique` associated with the next stop. NaN if this row represents the last stop on a service pattern | `str` or `float` (`np.nan`) |
| `previous_stop` | The `stop_id_unique` associated with the previous stop. NaN if this row represents the first stop on a service pattern | `str` or `float` (`np.nan`) |
| `last_stop` | `True` if this row represents the last stop on a service pattern, `False` otherwise | `bool` |
| `first_stop` | `True` if this row represents the first stop on a service pattern, `False` otherwise | `bool` |
| `overlapping_service_patterns` | An iterable of other service patterns that serve this stop | A `tuple` of `str`s |

### `df_overlapping_service_patterns`

**Type:** DataFrame

**Index:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `overlap_id` | A unique id that represents each overlap through a stop. | `str |

**Columns:**
| Name | Definition | Type |
| ---- | ---------- | ----- |
| `stop_id_unique` | A unique stop id that should not be duplicated in the network | `str` |
| `overlapping_service_patterns` | An iterable of other service patterns that serve this stop | `tuple` of `str`s |