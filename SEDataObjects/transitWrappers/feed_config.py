from SEDataObjects.transitWrappers.constants import CONFIG_CLUSTERING_DISTANCE_BUS_TO_BUS, CONFIG_CLUSTERING_DISTANCE_BUS_TO_HIGH_COMFORT, CONFIG_CLUSTERING_DISTANCE_HIGH_COMFORT_TO_HIGH_COMFORT, CONFIG_CLUSTERING_DISTANCE_SAME_ROUTE, CONFIG_CLUSTERING_ENABLED, CONFIG_EVENING_PEAK_END, CONFIG_EVENING_PEAK_START, CONFIG_HEADWAY_PERCENTILE, CONFIG_MIN_TRIPS, CONFIG_MORNING_PEAK_END, CONFIG_MORNING_PEAK_START, CONFIG_OFF_PEAK_END, CONFIG_OFF_PEAK_START, CONFIG_PEAK_WEIGHT


import datetime as dt

# Note all distances are in meters
DEFAULT_FEED_CONFIG = {
    CONFIG_MORNING_PEAK_START: dt.time(hour=7), # The start of the morning peak period
    CONFIG_MORNING_PEAK_END: dt.time(hour=9, minute=0), # The end of the morning peak period
    CONFIG_OFF_PEAK_START: dt.time(hour=9, minute=0), # The start of the off peak period
    CONFIG_OFF_PEAK_END: dt.time(hour=16,minute=0), # The end of the off peak period
    CONFIG_EVENING_PEAK_START: dt.time(hour=16, minute=0), # The start of the evening peak period
    CONFIG_EVENING_PEAK_END: dt.time(hour=18), # The end of the evening peak period
    CONFIG_PEAK_WEIGHT: 0.5, # The weight given to peak period trips when calculating weighted frequencies and headways
    CONFIG_HEADWAY_PERCENTILE: 80, # The percentile of headways to report
    CONFIG_MIN_TRIPS: 5, # The minimum number of trips needed to define a service pattern and to calculate headways
    CONFIG_CLUSTERING_ENABLED: True, # Whether to enable stop clustering
    CONFIG_CLUSTERING_DISTANCE_SAME_ROUTE: 200, # The distance to cluster stops if they are the same route
    CONFIG_CLUSTERING_DISTANCE_BUS_TO_BUS: 80, # The distance to cluster bus stops to each other
    CONFIG_CLUSTERING_DISTANCE_HIGH_COMFORT_TO_HIGH_COMFORT: 500, # The distance to cluster high omfort stops to each other
    CONFIG_CLUSTERING_DISTANCE_BUS_TO_HIGH_COMFORT: 500, # The distance to cluster bus stops to high comfort stops
}