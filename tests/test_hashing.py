from cache_service.hashing import digest_lists, digest_text


def test_digest_text_is_stable() -> None:
    assert digest_text("first string") == digest_text("first string")
    assert digest_text("first string") != digest_text("second string")


def test_digest_lists_depends_on_order() -> None:
    assert digest_lists(["a", "b"], ["c", "d"]) != digest_lists(["b", "a"], ["c", "d"])
    assert digest_lists(["a"], ["b"]) != digest_lists(["b"], ["a"])


def test_digest_lists_distinguishes_different_splits() -> None:
    """Inputs that would collide under naive concatenation must stay distinct."""
    assert digest_lists(["a,b"], ["c"]) != digest_lists(["a", "b"], ["c", ""])
