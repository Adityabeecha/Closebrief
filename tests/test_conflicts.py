from app.context.conflicts import find_conflicts
from app.schemas import ContextDoc


def doc(id, title, body, tags=None, date=None):
    return ContextDoc(id=id, type="event_note", title=title, body=body,
                      metric_tags=tags or [], effective_date=date)


def test_detects_conflicting_figures():
    docs = [
        doc(1, "March pricing memo",
            "The March pricing change drove enterprise churn of 612K ARR this quarter.",
            tags=["Churned ARR"], date="2025-03"),
        doc(2, "Q1 churn analysis",
            "Q1 enterprise churn from the pricing change totaled 580K ARR.",
            tags=["Churned ARR"], date="2025-02"),
    ]
    conflicts = find_conflicts(docs)
    assert len(conflicts) == 1
    assert conflicts[0]["most_recent"] == "March pricing memo"
    figs = conflicts[0]["figures"]
    assert any({f["a"], f["b"]} == {612000.0, 580000.0} for f in figs)


def test_no_conflict_when_metrics_disjoint():
    docs = [
        doc(1, "A", "revenue enterprise churn figure was 600K here", tags=["Net Revenue"]),
        doc(2, "B", "revenue enterprise churn figure was 500K there", tags=["Cash Balance"]),
    ]
    assert find_conflicts(docs) == []


def test_no_conflict_when_topics_differ():
    docs = [
        doc(1, "A", "office lease renewed at 600K annually in Austin"),
        doc(2, "B", "marketing campaign budget was 500K for paid media"),
    ]
    assert find_conflicts(docs) == []


def test_same_figure_is_not_a_conflict():
    docs = [
        doc(1, "A", "enterprise churn from pricing change was 600K ARR"),
        doc(2, "B", "enterprise churn from pricing change was 600K ARR confirmed"),
    ]
    assert find_conflicts(docs) == []


def test_wildly_different_magnitudes_are_not_conflicts():
    # 14 engineers vs 600000 dollars are different facts, not a disagreement.
    docs = [
        doc(1, "A", "the platform hiring wave added 14 engineers to the team"),
        doc(2, "B", "the platform hiring wave cost 600000 dollars this year"),
    ]
    assert find_conflicts(docs) == []
