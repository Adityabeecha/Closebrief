"""Unit tests for the v2.1 multi-domain foundation."""



def test_domain_registry_lists_fpa():
    from app.domains import DEFAULT_DOMAIN, list_domains
    slugs = {d.slug for d in list_domains()}
    assert "fpa" in slugs and DEFAULT_DOMAIN == "fpa"
    assert {"marketing", "ops"} <= slugs


def test_domain_system_prompt_override():
    from app.domains import get_domain
    fpa = get_domain("fpa").system_prompt
    mkt = get_domain("marketing").system_prompt
    ops = get_domain("ops").system_prompt
    assert fpa != mkt != ops
    assert "growth analyst" in mkt.lower()
    assert "operations analyst" in ops.lower()


def test_unknown_domain_falls_back_to_default():
    from app.domains import get_domain
    assert get_domain("does-not-exist").slug == "fpa"
    assert get_domain(None).slug == "fpa"


def test_domain_kpi_libraries_differ():
    from app.domains import get_domain
    mkt_names = {k["name"] for k in get_domain("marketing").kpi_library}
    ops_names = {k["name"] for k in get_domain("ops").kpi_library}
    assert "CAC" in mkt_names and "MTTR" in ops_names
    assert not (mkt_names & ops_names)


def test_domain_public_hides_prompt():
    from app.domains import get_domain
    pub = get_domain("fpa").public()
    assert "system_prompt" not in pub
    assert pub["slug"] == "fpa" and pub["kpi_count"] > 0
