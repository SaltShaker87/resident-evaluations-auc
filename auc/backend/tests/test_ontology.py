"""The ACGME ontology defines the shape of every report, so it is worth
asserting it has not drifted. 21 sub-competencies across 6 domains is what the
migration document promises and what preflight counts against."""

import json
from pathlib import Path

import pytest

ONTOLOGY_FILE = (
    Path(__file__).resolve().parents[2] / "rag" / "ontology" / "acgme_ontology.json"
)


@pytest.fixture(scope="module")
def ontology():
    return json.loads(ONTOLOGY_FILE.read_text(encoding="utf-8"))


def test_ontology_has_twenty_one_subcompetencies(ontology):
    assert len(ontology["subcompetencies"]) == 21


def test_every_domain_is_one_of_the_six(ontology):
    import rag_retrieval

    assert set(ontology["domains"]) == set(rag_retrieval.DOMAIN_ORDER)
    for sub in ontology["subcompetencies"]:
        assert sub["domain"] in ontology["domains"]


def test_subcompetency_ids_are_unique(ontology):
    ids = [sub["id"] for sub in ontology["subcompetencies"]]
    assert len(ids) == len(set(ids))


def test_every_subcompetency_has_routing_keywords(ontology):
    """A sub-competency with no keywords can only ever be reached by the
    semantic fallback, which is a quiet way to lose evidence."""
    for sub in ontology["subcompetencies"]:
        assert sub["keywords"], f"{sub['id']} has no keywords"


def test_the_skeleton_covers_every_subcompetency(ontology):
    """The report length is the skeleton length, so a dropped entry here is a
    section missing from every resident's summary."""
    import summary_builder

    skeleton = summary_builder.build_skeleton(ontology)
    assert len(skeleton) == 21
    assert {s["id"] for s in skeleton} == {s["id"] for s in ontology["subcompetencies"]}
