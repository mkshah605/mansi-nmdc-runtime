import json
import os
import re
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
from bs4 import BeautifulSoup

from nmdc_runtime.api.models.object import DrsObjectIn
from nmdc_runtime.util import (
    nmdc_jsonschema,
    drs_metadata_for,
    nmdc_jsonschema_validate,
)

pattern = re.compile(r"https?://(?P<domain>[^/]+)/(?P<path>.+)")


def url_to_name(url):
    m = pattern.match(url)
    return (
        f"{'.'.join(reversed(m.group('domain').split('.')))}"
        f"__{m.group('path').replace('/', '.')}"
    )


def fetch_url(url, timeout=30):
    return requests.get(url, timeout=timeout)


class HttpResponseNotOk(Exception):
    pass


class HttpResponseNotJson(Exception):
    pass


def response_to_json(response):
    if response.status_code != 200:
        raise HttpResponseNotOk()
    try:
        json_data = response.json()
    except:
        raise HttpResponseNotJson()
    return json_data


def json_data_from_url_to_file(json_data, url, save_dir):
    filepath = os.path.join(save_dir, url_to_name(url))
    with open(filepath, "w") as f:
        json.dump(json_data, f)
    return filepath


def json_clean(d, model, exclude_unset=False):
    return json.loads(model(**d).json(exclude_unset=exclude_unset))


def drs_object_in_for(url):
    with TemporaryDirectory() as save_dir:
        response = fetch_url(url)
        try:
            json_data = response_to_json(response)
        except HttpResponseNotOk:
            return {"error": "HttpResponseNotOk"}

        except HttpResponseNotJson:
            return {"error": "HttpResponseNotJson"}

        filepath = json_data_from_url_to_file(json_data, url, save_dir)
        drs_object_in = DrsObjectIn(
            **drs_metadata_for(
                filepath,
                {
                    "access_methods": [{"access_url": {"url": url}}],
                    "name": Path(filepath).name.replace(":", "-"),
                },
            )
        )
        return {"result": drs_object_in}


def create_drs_object_for(url, drs_object_in, client):
    rv = client.create_object(json.loads(drs_object_in.json(exclude_unset=True)))
    return {"url": url, "response": rv}


@lru_cache
def get_type_collections():
    return {
        f'nmdc:{spec["items"]["$ref"].split("/")[-1]}': collection_name
        for collection_name, spec in nmdc_jsonschema["properties"].items()
        if collection_name.endswith("_set")
    }


def specialize_activity_set_docs(docs):
    validation_errors = {}
    type_collections = get_type_collections()
    if "activity_set" in docs:
        for doc in docs["activity_set"]:
            doc_type = doc["type"]
            try:
                collection_name = type_collections[doc_type]
            except KeyError:
                msg = (
                    f"activity_set doc {doc.get('id', '<id missing>')} "
                    f"has type {doc_type}, which is not in NMDC Schema. "
                    "Note: Case is sensitive."
                )
                if "activity_set" in validation_errors:
                    validation_errors["activity_set"].append(msg)
                else:
                    validation_errors["activity_set"] = [msg]
                continue

            if collection_name in docs:
                docs[collection_name].append(doc)
            else:
                docs[collection_name] = [doc]
        del docs["activity_set"]
    return docs, validation_errors


def validate_as_metadata_and_ensure_tags_for(
    drs_id, client, tags=("schema#/definitions/Database", "metadata-in")
):
    docs = client.get_object_bytes(drs_id).json()
    docs, _ = specialize_activity_set_docs(docs)
    _ = nmdc_jsonschema_validate(docs)
    return {tag: client.ensure_object_tag(drs_id, tag) for tag in tags}


def recent_metadata_urls(
    urlpath="https://portal.nersc.gov/project/m3408/meta/anno2/",
    urlpath_extra="?C=M;O=D",
    since="2021-09",
):
    """Scrapes recent URLs from Apache/2.4.38 (Debian) Server listing.

    Designed with urlpath.startwsith("https://portal.nersc.gov/project/m3408/") in mind.
    """
    if since is None:
        now = datetime.now(timezone.utc)
        recent_enuf = now - timedelta(days=30)
        since = f"{recent_enuf.year}-{recent_enuf.month}"

    rv = requests.get(f"{urlpath}{urlpath_extra}")

    soup = BeautifulSoup(rv.text, "html.parser")

    urls = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 5:
            continue

        _, td_name, td_last_modified, td_size, _ = tds
        if td_last_modified.text.startswith(since):
            name = td_name.a.text
            if name.endswith(".json"):
                urls.append(f"{urlpath}{name}")

    return urls
