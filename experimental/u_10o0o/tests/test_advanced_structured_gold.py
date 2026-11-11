from experimental.u_10o0o.advanced_structured_gold import score_typed_facts


def _fact(**overrides):
    result = {
        "family": "HG",
        "key": "HG0000-00000",
        "role": "현장 사건 수신대장",
        "field": "actual_discharge_date",
        "cost_kind": "none",
        "value": "2029-01-02",
        "value_type": "date",
        "state": "reported",
        "unit": None,
        "currency": None,
        "source": {
            "book_id": "AS000",
            "file_name": "AS000.pdf",
            "page": 1,
            "record_id": "advanced:AS000:p1:l1",
            "line_start": 1,
            "line_end": 1,
        },
        "column": "날짜 또는 상태",
        "qualifier": None,
    }
    result.update(overrides)
    return result


def test_score_typed_facts_requires_matching_value_type_and_source():
    gold = _fact()
    actual = _fact()

    scored = score_typed_facts([gold], [actual])

    assert scored["identity"]["true_positive"] == 1
    assert scored["value"]["correct"] == 1
    assert scored["source"]["correct"] == 1
    assert scored["exact"] is True


def test_hg_identity_uses_field_and_cost_not_row_column_qualifier():
    gold = _fact()
    actual = _fact(column="날짜")

    scored = score_typed_facts([gold], [actual])

    assert scored["identity"]["true_positive"] == 1
    assert scored["source"]["correct"] == 1


def test_value_or_source_error_does_not_count_as_exact():
    gold = _fact()
    actual = _fact(value="2029-01-03", source={**gold["source"], "page": 2})

    scored = score_typed_facts([gold], [actual])

    assert scored["identity"]["true_positive"] == 1
    assert scored["value"]["correct"] == 0
    assert scored["source"]["correct"] == 0
    assert scored["exact"] is False


def test_ha_identity_keeps_exact_column_and_qualifier():
    gold = _fact(
        family="HA",
        key="HA00-C000",
        role="계약 조건 확인",
        field="무료기간",
        value=7,
        value_type="integer",
        unit="day",
        column="무료기간",
        qualifier="보관료",
    )
    actual = _fact(**{**gold, "column": "다른 열"})

    scored = score_typed_facts([gold], [actual])

    assert scored["identity"]["true_positive"] == 0
    assert scored["identity"]["false_positive"] == 1
    assert scored["identity"]["false_negative"] == 1


def test_empty_prediction_has_zero_recall_and_no_value_accuracy_denominator():
    scored = score_typed_facts([_fact()], [])

    assert scored["identity"]["recall"] == 0
    assert scored["identity"]["precision"] is None
    assert scored["value"]["accuracy_on_matched"] is None
    assert scored["exact"] is False
