from SEDataObjects.utils import safe_is_na
import numpy as np

def concatenate_id_lists(prefix, original_ids):
    return [
        f"{prefix}_{original_id}" if not safe_is_na(original_id) else np.nan
        for original_id in original_ids
    ]

