from SEDataObjects.BaseLayer.constants import SMART_LOCATION_JOB_DENSITY_COLUMN, SMART_LOCATION_JOB_DENSITY_NAME
from SEDataObjects.BaseLayer.entities import BaseLayerSmartLocation


class SmartLocationJobDensity(BaseLayerSmartLocation):
    metric_field_id = SMART_LOCATION_JOB_DENSITY_COLUMN
    metric_alias = SMART_LOCATION_JOB_DENSITY_NAME