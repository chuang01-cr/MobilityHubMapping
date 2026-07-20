from SEDataObjects.BaseLayer.constants import SMART_LOCATION_RAW_JOBS, SMART_LOCATION_RAW_JOBS_NAME
from SEDataObjects.BaseLayer.entities import BaseLayerSmartLocation


class SmartLocationRawJobs(BaseLayerSmartLocation):
    metric_field_id = SMART_LOCATION_RAW_JOBS
    metric_alias = SMART_LOCATION_RAW_JOBS_NAME