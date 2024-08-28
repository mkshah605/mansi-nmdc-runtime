from operator import itemgetter
from typing import List

from fastapi import APIRouter, Depends, Form
from jinja2 import Environment, PackageLoader, select_autoescape
from nmdc_runtime.minter.config import typecodes
from nmdc_runtime.util import get_nmdc_jsonschema_dict
from pymongo.database import Database as MongoDatabase
from starlette.responses import HTMLResponse
from toolz import merge, assoc_in

from nmdc_runtime.api.core.util import raise404_if_none
from nmdc_runtime.api.db.mongo import get_mongo_db, activity_collection_names
from nmdc_runtime.api.endpoints.util import (
    find_resources,
    strip_oid,
    find_resources_spanning,
    pipeline_find_resources,
)
from nmdc_runtime.api.models.metadata import Doc
from nmdc_runtime.api.models.util import (
    FindResponse,
    FindRequest,
    entity_attributes_to_index,
    PipelineFindRequest,
    PipelineFindResponse,
)
from nmdc_runtime.util import get_class_names_from_collection_spec

router = APIRouter()


@router.get(
    "/studies",
    response_model=FindResponse,
    response_model_exclude_unset=True,
)
def find_studies(
    req: FindRequest = Depends(),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    The `GET /studies` endpoint is a general purpose way to retrieve NMDC studies based on parameters provided by the user.
    Studies can be filtered and sorted based on the applicable [Study attributes](https://microbiomedata.github.io/nmdc-schema/Study/).
    """
    return find_resources(req, mdb, "study_set")


@router.get(
    "/studies/{study_id}",
    response_model=Doc,
    response_model_exclude_unset=True,
)
def find_study_by_id(
    study_id: str,
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    If the study identifier is known, a study can be retrieved directly using the GET /studies/{study_id} endpoint.
    \n Note that only one study can be retrieved at a time using this method.
    """
    return strip_oid(raise404_if_none(mdb["study_set"].find_one({"id": study_id})))


@router.get(
    "/biosamples",
    response_model=FindResponse,
    response_model_exclude_unset=True,
)
def find_biosamples(
    req: FindRequest = Depends(),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    The GET /biosamples endpoint is a general purpose way to retrieve biosample metadata using user-provided filter and sort criteria.
    Please see the applicable [Biosample attributes](https://microbiomedata.github.io/nmdc-schema/Biosample/).
    """
    return find_resources(req, mdb, "biosample_set")


@router.get(
    "/biosamples/{sample_id}",
    response_model=Doc,
    response_model_exclude_unset=True,
)
def find_biosample_by_id(
    sample_id: str,
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    If the biosample identifier is known, a biosample can be retrieved directly using the GET /biosamples/{sample_id}.
    \n Note that only one biosample metadata record can be retrieved at a time using this method.
    """
    return strip_oid(raise404_if_none(mdb["biosample_set"].find_one({"id": sample_id})))


@router.get(
    "/data_objects",
    response_model=FindResponse,
    response_model_exclude_unset=True,
)
def find_data_objects(
    req: FindRequest = Depends(),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    To retrieve metadata about NMDC data objects (such as files, records, or omics data) the GET /data_objects endpoint
    may be used along with various parameters. Please see the applicable [Data Object](https://microbiomedata.github.io/nmdc-schema/DataObject/)
    attributes.
    """
    return find_resources(req, mdb, "data_object_set")


def get_classname_from_typecode(doc_id: str) -> str:
    r"""
    Returns the name of the schema class of which an instance could have the specified `id`.

    >>> get_classname_from_typecode("nmdc:sty-11-r2h77870")
    'Study'
    """
    typecode = doc_id.split(":")[1].split("-")[0]
    class_map_data = typecodes()
    class_map = {
        entry["name"]: entry["schema_class"].split(":")[1] for entry in class_map_data
    }
    return class_map.get(typecode)


@router.get(
    "/data_objects/study/{study_id}",
    response_model_exclude_unset=True,
)
def find_data_objects_for_study(
    study_id: str,
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """This API endpoint is used to retrieve data object ids associated with
    all the biosamples that are part of a given study. This endpoint makes
    use of the `alldocs` collection for its implementation.

    :param study_id: NMDC study id for which data objects are to be retrieved
    :param mdb: PyMongo connection, defaults to Depends(get_mongo_db)
    :return: List of dictionaries where each dictionary contains biosample id as key,
        and another dictionary with key 'data_object_set' containing list of data object ids as value
    """
    biosample_data_objects = []
    study = raise404_if_none(
        mdb.study_set.find_one({"id": study_id}, ["id"]), detail="Study not found"
    )

    biosamples = mdb.biosample_set.find({"part_of": study["id"]}, ["id"])
    biosample_ids = [biosample["id"] for biosample in biosamples]

    for biosample_id in biosample_ids:
        current_ids = [biosample_id]
        collected_data_objects = []

        while current_ids:
            new_current_ids = []
            for current_id in current_ids:
                query = {"has_input": current_id}
                document = mdb.alldocs.find_one(query)

                if not document:
                    continue

                has_output = document.get("has_output")
                if not has_output:
                    continue

                for output_id in has_output:
                    if get_classname_from_typecode(output_id) == "DataObject":
                        data_object_doc = mdb.data_object_set.find_one(
                            {"id": output_id}
                        )
                        if data_object_doc:
                            collected_data_objects.append(strip_oid(data_object_doc))
                    else:
                        new_current_ids.append(output_id)

            current_ids = new_current_ids

        if collected_data_objects:
            biosample_data_objects.append(
                {
                    "biosample_id": biosample_id,
                    "data_object_set": collected_data_objects,
                }
            )

    return biosample_data_objects


@router.get(
    "/data_objects/{data_object_id}",
    response_model=Doc,
    response_model_exclude_unset=True,
)
def find_data_object_by_id(
    data_object_id: str,
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    If the data object identifier is known, the metadata can be retrieved using the GET /data_objects/{data_object_id} endpoint.
    \n Note that only one data object metadata record may be retrieved at a time using this method.
    """
    return strip_oid(
        raise404_if_none(mdb["data_object_set"].find_one({"id": data_object_id}))
    )


@router.get(
    "/workflow_executions",
    response_model=FindResponse,
    response_model_exclude_unset=True,
)
def find_workflow_executions(
    req: FindRequest = Depends(),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    # TODO: Add w3id URL links for classes (e.g. <https://w3id.org/nmdc/WorkflowExecution>) when they resolve
    #   to Berkeley schema definitions.
    """
    The GET /workflow_executions endpoint is a general way to fetch metadata about various workflow_executions (e.g. metagenome assembly,
    natural organic matter analysis, library preparation, etc.). Any "slot" (a.k.a. attribute) for
    `WorkflowExecution` or `PlannedProcess` classes may be used in the filter
    and sort parameters, including attributes of subclasses of *WorkflowExecution* and *PlannedProcess*.

    For example, attributes used in subclasses such as MetabolomicsAnalysis (subclass of *WorkflowExecution*)
    or `Extraction` (subclass of *PlannedProcess*),
    can be used as input criteria for the filter and sort parameters of this endpoint.
    """
    return find_resources(req, mdb, "workflow_execution_set")


@router.get(
    "/workflow_executions/{workflow_execution_id}",
    response_model=Doc,
    response_model_exclude_unset=True,
)
def find_workflow_execution_by_id(
    workflow_execution_id: str,
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    """
    If the workflow_execution identifier is known, the workflow_execution metadata can be retrieved using the
    GET /workflow_executions/workflow_execution_id endpoint.
    \n Note that only one metadata record for an workflow_execution may be returned at a time using this method.
    """
    return strip_oid(
        raise404_if_none(
            mdb["workflow_execution_set"].find_one({"id": workflow_execution_id})
        )
    )


jinja_env = Environment(
    loader=PackageLoader("nmdc_runtime"), autoescape=select_autoescape()
)


def attr_index_sort_key(attr):
    return "_" if attr == "id" else attr


def documentation_links(jsonschema_dict, collection_names) -> dict:
    """TODO: Add a docstring saying what this function does at a high level."""

    # TODO: Document the purpose of this initial key.
    doc_links = {"Activity": []}

    # Note: All documentation URLs generated within this function will begin with this.
    base_url = r"https://microbiomedata.github.io/nmdc-schema"

    for collection_name in collection_names:
        # Since a given collection can be associated with multiple classes, the `doc_links` dictionary
        # will have a _list_ of values for each collection.
        class_descriptors = []

        # If the collection name is one that the `search.html` page has a dedicated section for,
        # give it a top-level key; otherwise, nest it under `activity_set`.
        key_hierarchy: List[str] = ["activity_set", collection_name]
        if collection_name in ("biosample_set", "study_set", "data_object_set"):
            key_hierarchy = [collection_name]

        # Process the name of each class that the schema associates with this collection.
        collection_spec = jsonschema_dict["$defs"]["Database"]["properties"][
            collection_name
        ]
        class_names = get_class_names_from_collection_spec(collection_spec)
        for idx, class_name in enumerate(class_names):
            # Make a list of dictionaries, each of which describes one attribute of this class.
            entity_attrs = list(jsonschema_dict["$defs"][class_name]["properties"])
            entity_attr_descriptors = [
                {"url": f"{base_url}/{attr_name}", "attr_name": attr_name}
                for attr_name in entity_attrs
            ]

            # Make a dictionary describing this class.
            class_descriptor = {
                "collection_name": collection_name,
                "entity_url": f"{base_url}/{class_name}",
                "entity_name": class_name,
                "entity_attrs": sorted(
                    entity_attr_descriptors, key=itemgetter("attr_name")
                ),
            }

            # Add that descriptor to this collection's list of class descriptors.
            class_descriptors.append(class_descriptor)

        # Add a key/value pair describing this collection to the `doc_links` dictionary.
        # Reference: https://toolz.readthedocs.io/en/latest/api.html#toolz.dicttoolz.assoc_in
        doc_links = assoc_in(doc_links, keys=key_hierarchy, value=class_descriptors)

    return doc_links


@router.get("/search", response_class=HTMLResponse)
def search_page(
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    template = jinja_env.get_template("search.html")
    indexed_entity_attributes = merge(
        {n: {"id"} for n in activity_collection_names(mdb)},
        {
            coll: sorted(attrs | {"id"}, key=attr_index_sort_key)
            for coll, attrs in entity_attributes_to_index.items()
        },
    )
    doc_links = documentation_links(
        get_nmdc_jsonschema_dict(),
        (
            list(activity_collection_names(mdb))
            + ["biosample_set", "study_set", "data_object_set"]
        ),
    )
    html_content = template.render(
        activity_collection_names=sorted(activity_collection_names(mdb)),
        indexed_entity_attributes=indexed_entity_attributes,
        doc_links=doc_links,
    )
    return HTMLResponse(content=html_content, status_code=200)


@router.post(
    "/pipeline_search",
    response_model=PipelineFindResponse,
    response_model_exclude_unset=True,
)
def pipeline_search(
    req: PipelineFindRequest = Depends(),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    return pipeline_find_resources(req, mdb)


@router.post(
    "/pipeline_search_form",
    response_model=PipelineFindResponse,
    response_model_exclude_unset=True,
)
def pipeline_search(
    pipeline_spec: str = Form(...),
    description: str = Form(...),
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    req = PipelineFindRequest(pipeline_spec=pipeline_spec, description=description)
    return pipeline_find_resources(req, mdb)


@router.get("/pipeline_search", response_class=HTMLResponse)
def pipeline_search(
    mdb: MongoDatabase = Depends(get_mongo_db),
):
    template = jinja_env.get_template("pipeline_search.html")
    html_content = template.render()
    return HTMLResponse(content=html_content, status_code=200)
