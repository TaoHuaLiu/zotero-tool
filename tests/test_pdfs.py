from zotero_tool.pdfs import Fetcher, clean_doi, similarity


def test_clean_doi_from_url_and_extra():
    assert clean_doi("https://doi.org/10.1145/3313831.3376590.") == "10.1145/3313831.3376590"
    assert clean_doi("DOI: 10.3390/s21010001") == "10.3390/s21010001"
    assert clean_doi("no doi here") == ""


def test_similarity_ignores_case_accents_punctuation():
    assert similarity("Who's in charge here?", "WHO S IN CHARGE HERE") > 0.95
    assert similarity("Trust in AI", "Explainable robots in factories") < 0.5


def test_filename_is_ascii_and_keyed():
    item = {
        "key": "K1",
        "meta": {"parsedDate": "2024-01-02"},
        "data": {"key": "K1", "title": "Études: trust / XAI?", "creators": [{"lastName": "Lé"}]},
    }
    assert Fetcher.filename(item) == "Le_2024_Etudes_trust_XAI_K1.pdf"


def test_pdf_urls_dedup_and_only_oa_locations():
    work = {
        "best_oa_location": {"pdf_url": "a"},
        "open_access": {"oa_url": "a"},
        "locations": [{"is_oa": True, "pdf_url": "b"}, {"is_oa": False, "pdf_url": "c"}],
    }
    assert Fetcher.pdf_urls(work) == ["a", "b"]


def test_openalex_rate_limit_is_an_error_not_a_miss(monkeypatch, tmp_path):
    import httpx

    from zotero_tool import pdfs

    monkeypatch.setattr(pdfs.time, "sleep", lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(429)))
    f = Fetcher(outdir=tmp_path, zot=None, http=client, openalex_key=None)
    item = {"key": "K", "data": {"key": "K", "itemType": "book", "title": "T", "DOI": "10.1/x"}}
    res = f.fetch_one(item, [])
    assert res.status == "failed" and "OpenAlex" in res.detail
