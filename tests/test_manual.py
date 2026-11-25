from zotero_tool.manual import match


def todo(key, title, doi=""):
    return {"item": {"key": key, "data": {"key": key, "title": title, "DOI": doi}}, "file": key}


def test_match_by_doi_before_title():
    t = [todo("A", "Something else entirely here"), todo("B", "Other", "10.1145/123.456")]
    assert match("Published as https://doi.org/10.1145/123.456", t)["item"]["key"] == "B"


def test_match_by_title_across_line_breaks_and_case():
    t = [todo("A", "Agent Transparency and Human-Autonomy Teaming Effectiveness in Multi-Robot")]
    text = (
        "AGENT TRANSPARENCY AND HUMAN-\nAUTONOMY TEAMING EFFECTIVENESS\nin multi-robot management"
    )
    assert match(text, t)["item"]["key"] == "A"


def test_no_match_for_unrelated_pdf():
    assert match("An invoice from a hotel", [todo("A", "Agent transparency for squads")]) is None
