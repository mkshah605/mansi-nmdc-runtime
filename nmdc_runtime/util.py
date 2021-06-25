import json
from datetime import datetime, timezone
import mimetypes
import os
from pathlib import Path

import fastjsonschema
import pkg_resources
import requests
from toolz import merge

from nmdc_runtime.api.core.util import sha256hash_from
from nmdc_runtime.api.models.object import DrsObjectIn


with open(pkg_resources.resource_filename("nmdc_schema", "nmdc.schema.json")) as f:
    nmdc_jsonschema = json.load(f)
    nmdc_jsonschema_validate = fastjsonschema.compile(nmdc_jsonschema)


def put_object(filepath, url, mime_type=None):
    if mime_type is None:
        mime_type = mimetypes.guess_type(filepath)[0]
    reading_bytes = mime_type in {
        "application/gzip",
        "application/zip",
        "application/x-7z-compressed",
        "application/x-bzip",
        "application/x-bzip2",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "application/pdf",
    }
    with open(filepath, f'r{"b" if reading_bytes else ""}') as f:
        return requests.put(url, data=f, headers={"Content-Type": mime_type})


def drs_metadata_for(filepath, base=None):
    """given file path, get drs metadata

    required: size, created_time, and at least one checksum.
    """
    base = {} if base is None else base
    if "size" not in base:
        base["size"] = os.path.getsize(filepath)
    if "created_time" not in base:
        base["created_time"] = datetime.fromtimestamp(
            os.path.getctime(filepath), tz=timezone.utc
        )
    if "checksums" not in base:
        base["checksums"] = [{"type": "sha-256", "checksum": sha256hash_from(filepath)}]
    if "mime_type" not in base:
        base["mime_type"] = mimetypes.guess_type(filepath)[0]
    if "name" not in base:
        base["name"] = Path(filepath).name
    return base


def drs_object_in_for(filepath, op_doc, base=None):
    access_id = f'{op_doc["metadata"]["site_id"]}:{op_doc["metadata"]["object_id"]}'
    drs_obj_in = DrsObjectIn(
        **drs_metadata_for(
            filepath,
            merge(base or {}, {"access_methods": [{"access_id": access_id}]}),
        )
    )
    return json.loads(drs_obj_in.json(exclude_unset=True))
