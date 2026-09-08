from robovision.canonical import canonical_json, fingerprint


def test_canonical_key_order_is_stable():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_fingerprint_changes_with_content():
    assert fingerprint({"a": 1}) != fingerprint({"a": 2})
