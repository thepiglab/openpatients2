import copy

import pytest

from openpatients2.pmc_media import cloud_url, media_manifest, parse_figures, metadata_identifiers, CLOUD
from openpatients2.sources_demo import fixture_metadata, fixture_xml, PMID, PMCID


def parse(xml=None, metadata=None):
    m = metadata or fixture_metadata()
    return parse_figures(xml or fixture_xml(), PMCID, 1, media_manifest(m), cloud_url(m["xml_url"]), PMID)


def test_manifest_urls_not_invented_and_checksum_preserved():
    m = fixture_metadata()
    result = parse(metadata=m)
    fig = result["figures"][0]
    assert fig["image_urls"] == [cloud_url(m["media_urls"][0])]
    assert "md5=" in fig["image_urls"][0]
    assert fig["media"][0]["match_method"] == "extensionless_reference_to_manifest_entry"
    assert fig["media"][0]["http_verified"] is False
    assert fig["patient_assignment"] == "unverified"
    assert len(result["unassigned_media"]) == 1


@pytest.mark.parametrize("url", ["http://evil.invalid/x.xml", "https://169.254.169.254/a.xml",
    "file:///etc/passwd", "data:image/png;base64,xxxx", "https://user:pass@pmc-oa-opendata.s3.amazonaws.com/x.xml",
    "https://pmc-oa-opendata.s3.amazonaws.com/a/../x.xml", "s3://other-bucket/a.xml"])
def test_only_public_pmc_bucket_is_allowed(url):
    with pytest.raises(ValueError):
        cloud_url(url)


def test_s3_to_https_conversion_preserves_path_and_query():
    assert cloud_url("s3://pmc-oa-opendata/PMC123.2/a.jpg?md5=abc") == CLOUD + "/PMC123.2/a.jpg?md5=abc"


def test_caption_inline_markup_and_unicode():
    xml = fixture_xml().replace("SYNTHETIC EXAMPLE: case A", "α <italic>case</italic> A")
    assert "α case A" in parse(xml)["figures"][0]["caption"]


def test_no_guessing_jpg_extensions_when_not_in_manifest():
    m = fixture_metadata(images=False)
    fig = parse(metadata=m)["figures"][0]
    assert fig["image_urls"] == []
    assert fig["unresolved_graphic_references"] == ["case-f1"]


def test_manifest_images_not_all_assigned_to_first_figure():
    m = fixture_metadata()
    m["media_urls"].append(f"s3://pmc-oa-opendata/{PMCID}.1/another-image.png")
    result = parse(metadata=m)
    assert len(result["figures"][0]["image_urls"]) == 1
    assert any(x["filename"] == "another-image.png" for x in result["unassigned_media"])


def test_no_figures_is_distinct_from_unassigned_images():
    result = parse(fixture_xml(figures=False))
    assert result["figures"] == []
    assert len(result["unassigned_media"]) == 2


def test_multiple_figure_panels_not_independently_assigned_to_patient():
    fig = parse()["figures"][0]
    assert "case B" in fig["caption"]
    assert fig["association_scope"] == "article"
    assert fig["patient_assignment"] == "unverified"
    assert fig["training_approval"] == "not_reviewed"


def test_external_same_filename_is_not_aliased_to_local_image():
    xml = fixture_xml().replace('xlink:href="case-f1"', 'xlink:href="https://publisher.example/other/case-f1.jpg"')
    assert not parse(xml)["figures"][0]["image_urls"]


def test_figure_group_caption_retained():
    xml = fixture_xml().replace('<fig id="F1">', '<fig-group id="G1"><caption><p>Group caption.</p></caption><fig id="F1">').replace('</fig>', '</fig></fig-group>')
    fig = parse(xml)["figures"][0]
    assert fig["group_id"] == "G1"
    assert fig["group_caption"] == "Group caption."


def test_standalone_graphic_group_is_a_figure_record():
    xml = fixture_xml().replace('<fig id="F1">', '<fig-group id="G1">').replace('</fig>', '</fig-group>')
    assert parse(xml)["figures"][0]["figure_type"] == "fig-group"


def test_reject_entity_expansion():
    bad = '<!DOCTYPE article [<!ENTITY evil SYSTEM "file:///etc/passwd">]><article><body>&evil;</body></article>'
    with pytest.raises(Exception):
        parse(bad)


def test_wrong_article_xml_rejected():
    with pytest.raises(ValueError, match="PMCID"):
        parse(fixture_xml(pmcid="PMC99991111"))


def test_wrong_article_pmid_rejected():
    with pytest.raises(ValueError, match="PMID"):
        parse(fixture_xml(pmid="80000000"))


def test_metadata_requires_matching_version():
    with pytest.raises(ValueError, match="version"):
        metadata_identifiers(fixture_metadata(), PMCID, 2)


def test_namespace_qualified_jats_supported():
    xml = fixture_xml().replace('<article xmlns:xlink=', '<article xmlns="http://jats.nlm.nih.gov" xmlns:xlink=')
    assert len(parse(xml)["figures"]) == 1


def test_image_video_spreadsheet_kinds_not_conflated():
    m = fixture_metadata()
    m["media_urls"].extend([f"s3://pmc-oa-opendata/{PMCID}.1/a.mp4", f"s3://pmc-oa-opendata/{PMCID}.1/a.svg"])
    assert [x["kind"] for x in media_manifest(m)] == ["image", "supplementary_or_other", "video", "image"]


def test_figure_specific_attribution_retained_without_permission_inference():
    xml = fixture_xml().replace('</fig>', '<attrib>Reproduced by permission of a third party.</attrib></fig>')
    fig = parse(xml)["figures"][0]
    assert fig["rights_statements"][0]["type"] == "attrib"
    assert fig["training_approval"] == "not_reviewed"


def test_manifest_media_cannot_point_to_other_article():
    m = fixture_metadata()
    m["media_urls"] = ["s3://pmc-oa-opendata/PMC111.1/case-f1.jpg"]
    with pytest.raises(ValueError, match="different article"):
        media_manifest(m)


def test_manifest_media_cannot_point_to_other_version():
    m = fixture_metadata()
    m["media_urls"] = [f"s3://pmc-oa-opendata/{PMCID}.2/case-f1.jpg"]
    with pytest.raises(ValueError, match="different article"):
        media_manifest(m)


def test_parent_relative_graphic_is_not_matched_by_basename():
    xml = fixture_xml().replace('xlink:href="case-f1"', 'xlink:href="../other/case-f1.jpg"')
    assert not parse(xml)["figures"][0]["image_urls"]


@pytest.mark.parametrize("raw,expected", [("no", False), ("yes", True), (False, False),
    ("False", False), (1, True), (None, None), ("unknown", None)])
def test_metadata_flags_are_nullable_booleans(raw, expected):
    from openpatients2.pmc_media import metadata_flag
    assert metadata_flag(raw) is expected
