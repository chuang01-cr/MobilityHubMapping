from SEDataObjects.utils import download_json_safely, safe_is_na


class GBFSFeedWrapper:
    gbfs_json = None
    system_information_json = None
    station_information_json = None
    got_gbfs_json = False
    def __init__(self, url: str) -> None:
        self.gbfs_json_url = url
        self.gbfs_json = download_json_safely(url)
        self.got_gbfs_json = False
        if self.gbfs_json is not None:
            try:
                feeds = self.gbfs_json["data"]["en"]["feeds"]
                self.got_gbfs_json = True
            except KeyError:
                print(f"WARN: gbfs.json file with {url} downloaded but did not have valid English feeds")
            self.system_information = download_json_safel
    

    